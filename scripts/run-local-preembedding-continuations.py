#!/usr/bin/env python3
"""Run resumable local-only makers for preembedding continuation work packages.

The default mode is a read-only plan check. ``--execute`` is required before any
model request is made, and execution is refused until the primary 231-entry
batch is complete. Outputs remain machine-extracted staging receipts under the
preembedding-v1 structure lane.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
WORKSTATION = LAB.parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
SECTION_TYPES = {
    "taxonomy", "description", "anatomy", "distribution", "history",
    "flowering", "harvest", "constituents", "historical_use", "literature",
    "plate_description", "other",
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_status(path: Path, value: dict) -> None:
    value.pop("status_sha256", None)
    value["updated_at"] = now()
    value["status_sha256"] = object_hash(value, "status_sha256")
    write_json(path, value)


def object_hash(value: dict, field: str) -> str:
    return sha256_text(canonical_json({key: item for key, item in value.items() if key != field}))


def receipt_path(root: Path, package_id: str, lane: str = "continuation") -> Path:
    directory = {
        "continuation": "continuation-maker-receipts",
        "continuation-v2": "continuation-v2-maker-receipts",
        "recovery": "recovery-maker-receipts",
    }[lane]
    return root / "structure" / directory / f"{package_id.replace(':', '__')}.json"


def endpoint_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status == 200
    except Exception:
        return False


def extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response contains no JSON object")
    return json.loads(cleaned[start:end + 1])


def request_local(endpoint: str, model: str, prompt: str, max_tokens: int) -> tuple[str, float]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a maker-only evidence extraction agent. Return one JSON object only. "
                    "Never invent a Taiwan Chinese name, occurrence, modern medical advice, image fact, "
                    "or approval status. Preserve Latin names, numbers and exact German line anchors."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"], time.monotonic() - started


def numbered_page(text: str) -> str:
    return "\n".join(
        f"L{line_number:04d}\t{line}"
        for line_number, line in enumerate(text.splitlines(), 1)
        if line.strip()
    )


def prompt_for(package: dict, pages: dict[tuple[str, int], dict]) -> str:
    source = "\n\n".join(
        f"=== PDF PAGE {page} ===\n{numbered_page(pages[(package['source_id'], page)]['text'])}"
        for page in package["pdf_pages"]
    )
    return f"""Extract representative candidate sections from one continuation package of a Köhler book entry.

PACKAGE_ID: {package['package_id']}
PARENT_ENTRY_ID: {package['parent_entry_id']}
TARGET_ENTRY_ID: {package.get('child_entry_id', package.get('recovered_entry_id', package['parent_entry_id']))}
SEQUENCE: {package['sequence']} of {package['sequence_count']}
BOOK_TAXON_CANDIDATE: {package['book_taxon_candidate']}
ALLOWED_PDF_PAGES: {package['pdf_pages']}

Required JSON shape:
{{
  "package_id": "{package['package_id']}",
  "parent_entry_id": "{package['parent_entry_id']}",
  "book_taxon": {{"scientific_name_candidate": "{package['book_taxon_candidate']}"}},
  "display_name": null,
  "name_resolution": {{"status": "unresolved", "sources": []}},
  "sections": [
    {{
      "section_type": "taxonomy|description|anatomy|distribution|history|flowering|harvest|constituents|historical_use|literature|plate_description|other",
      "pdf_page": 1,
      "source_line_start": 1,
      "source_line_end": 3
    }}
  ],
  "review_status": "machine_extracted",
  "warnings": []
}}

Rules:
- Return JSON only and no markdown.
- Return at most 5 representative sections and never repeat a section_type. If
  the same topic occurs twice, choose only the single strongest range.
- section_type must be copied from the exact allowed list above. In particular,
  pharmaceutical or therapeutic use is historical_use, never a new label.
- Choose one short contiguous source range per section, at most 60 numbered lines.
- Copy source_line_start/source_line_end only from line labels visibly present on
  that PDF page; never estimate the next line number.
- Every pdf_page value must be copied verbatim from ALLOWED_PDF_PAGES. It is an
  absolute PDF page number, never package-relative page 1, 2, 3, etc.
- The program materializes exact quotes and locators; do not emit exact_source_quote.
- Do not summarize, translate, normalize, or quote the source; return line coordinates only.
- display_name must be null and name resolution must remain unresolved.
- Historical medical content is historical evidence, never modern advice.
- Plate captions may be transcribed as text, but never infer from an image.
- Unsupported fields must be null or empty arrays.

SOURCE:
{source}
"""


def materialize_and_validate(
    draft: dict,
    package: dict,
    pages: dict[tuple[str, int], dict],
) -> tuple[list[str], list[dict]]:
    errors: list[str] = []
    locators: list[dict] = []
    if draft.get("package_id") != package["package_id"]:
        errors.append("package_id_mismatch")
    if draft.get("parent_entry_id") != package["parent_entry_id"]:
        errors.append("parent_entry_id_mismatch")
    if draft.get("review_status") != "machine_extracted":
        errors.append("review_status_must_be_machine_extracted")
    if draft.get("display_name") is not None:
        errors.append("display_name_must_be_null")
    if draft.get("name_resolution") != {"status": "unresolved", "sources": []}:
        errors.append("name_resolution_must_be_unresolved_without_sources")
    sections = draft.get("sections")
    if not isinstance(sections, list) or not sections:
        return sorted(set(errors + ["no_sections"])), locators
    if len(sections) > 6:
        errors.append("too_many_sections")
    seen_types: set[str] = set()
    for index, section in enumerate(sections):
        prefix = f"section_{index}"
        if not isinstance(section, dict):
            errors.append(f"{prefix}:not_object")
            continue
        section_type = section.get("section_type")
        if section_type not in SECTION_TYPES:
            errors.append(f"{prefix}:invalid_section_type")
        elif section_type in seen_types:
            errors.append(f"{prefix}:duplicate_section_type:{section_type}")
        else:
            seen_types.add(section_type)
        page_number = section.get("pdf_page")
        page = pages.get((package["source_id"], page_number))
        start = section.pop("source_line_start", None)
        end = section.pop("source_line_end", None)
        if isinstance(start, int) and isinstance(end, int):
            section["source_line_range"] = [start, end]
        if page is None or page_number not in package["pdf_pages"]:
            errors.append(f"{prefix}:source_page_outside_package:p{page_number}")
            continue
        lines = page["text"].splitlines()
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > len(lines) or end - start + 1 > 60:
            errors.append(f"{prefix}:invalid_source_line_range:p{page_number}:L{start}-L{end}")
            continue
        quote = "\n".join(lines[start - 1:end])
        char_start = page["text"].find(quote)
        if not quote.strip() or char_start < 0:
            errors.append(f"{prefix}:source_quote_not_exact:p{page_number}")
            continue
        if page["text"].find(quote, char_start + 1) >= 0:
            errors.append(f"{prefix}:source_quote_ambiguous:p{page_number}")
            continue
        section["exact_source_quote"] = quote
        locator = {
            "source_id": package["source_id"],
            "volume": package["volume"],
            "pdf_page": page_number,
            "source_pdf_sha256": next(
                item["source_pdf_sha256"] for item in package["source_locators"]
                if item["pdf_page"] == page_number
            ),
            "char_start": char_start,
            "char_end": char_start + len(quote),
            "source_line_start": start,
            "source_line_end": end,
            "page_text_sha256": page["text_sha256"],
            "exact_text_sha256": sha256_text(quote),
            "section_index": index,
            "section_type": section_type,
        }
        locators.append(locator)
        normalized = section.get("normalized_text_candidate")
        if normalized is not None and not isinstance(normalized, str):
            errors.append(f"{prefix}:normalized_text_candidate_not_string_or_null")
        zh_tw = section.get("zh_tw_rendering_candidate")
        if zh_tw is not None and not isinstance(zh_tw, str):
            errors.append(f"{prefix}:zh_tw_rendering_candidate_not_string_or_null")
        elif isinstance(zh_tw, str) and len(zh_tw) > 120:
            errors.append(f"{prefix}:zh_tw_rendering_candidate_over_120_chars")
    return sorted(set(errors)), locators


def validate_package(package: dict, pages: dict[tuple[str, int], dict], lane: str = "continuation") -> list[str]:
    errors = []
    if package.get("package_sha256") != object_hash(package, "package_sha256"):
        errors.append("package_sha256_mismatch")
    expected_stage = {
        "continuation": "local_structure_continuation",
        "continuation-v2": "local_structure_continuation_v2",
        "recovery": "local_structure_recovery",
    }[lane]
    if package.get("stage") != expected_stage:
        errors.append("invalid_stage")
    expected_dependencies = (
        ["primary_local_structure_batch_complete", "boundary_overlay_plan_valid"]
        if lane == "continuation-v2"
        else ["primary_local_structure_batch_complete"]
    )
    if package.get("dependencies") != expected_dependencies:
        errors.append("invalid_dependency")
    if not 1 <= package.get("page_count", 0) <= 6 or package.get("page_count") != len(package.get("pdf_pages", [])):
        errors.append("invalid_page_count")
    if package.get("name_resolution_status") != "unresolved" or package.get("layout_or_plate_claims_approved") is not False:
        errors.append("unsafe_package_metadata")
    for expected in package.get("source_locators", []):
        page = pages.get((expected.get("source_id"), expected.get("pdf_page")))
        if page is None:
            errors.append(f"missing_page:p{expected.get('pdf_page')}")
            continue
        if expected.get("char_start") != 0 or expected.get("char_end") != len(page["text"]):
            errors.append(f"partial_package_page_locator:p{expected['pdf_page']}")
        if expected.get("page_text_sha256") != page["text_sha256"] or expected.get("exact_text_sha256") != sha256_text(page["text"]):
            errors.append(f"package_page_hash_mismatch:p{expected['pdf_page']}")
    return sorted(set(errors))


def load_pages(root: Path) -> dict[tuple[str, int], dict]:
    pages = {}
    for path in sorted((root / "shards").glob("S*/inputs/pages.jsonl")):
        for page in read_jsonl(path):
            pages[(page["source_id"], page["pdf_page"])] = page
    return pages


def existing_valid_receipt(root: Path, package: dict, lane: str = "continuation") -> bool:
    path = receipt_path(root, package["package_id"], lane)
    if not path.exists():
        return False
    receipt = read_json(path)
    return (
        receipt.get("receipt_sha256") == object_hash(receipt, "receipt_sha256")
        and receipt.get("package_id") == package["package_id"]
        and receipt.get("package_sha256") == package["package_sha256"]
        and receipt.get("deterministic_status") == "pass"
        and receipt.get("errors") == []
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18080/v1/chat/completions")
    parser.add_argument("--models-endpoint", default="http://127.0.0.1:18080/v1/models")
    parser.add_argument("--model", default=os.environ.get("QWEN35_MODEL_ID", ""))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-service", action="store_true")
    parser.add_argument("--keep-service-on", action="store_true")
    parser.add_argument("--lane", choices=["continuation", "continuation-v2", "recovery"], default="continuation")
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    root = args.root
    status_name = {
        "continuation": "continuation-batch-status.json",
        "continuation-v2": "continuation-v2-batch-status.json",
        "recovery": "recovery-batch-status.json",
    }[args.lane]
    package_name = {
        "continuation": "continuation-work-packages.jsonl",
        "continuation-v2": "continuation-work-packages-v2.jsonl",
        "recovery": "content-recovery-work-packages.jsonl",
    }[args.lane]
    status_path = root / "checks" / status_name
    primary = read_json(root / "batch-status.json")
    packages = read_jsonl(root / "structure" / package_name)
    pages = load_pages(root)
    package_errors = {
        package["package_id"]: errors
        for package in packages
        if (errors := validate_package(package, pages, args.lane))
    }
    if package_errors:
        raise SystemExit("continuation package validation failed: " + json.dumps(package_errors, ensure_ascii=False))
    completed_before = sum(existing_valid_receipt(root, package, args.lane) for package in packages)
    pending = [package for package in packages if not existing_valid_receipt(root, package, args.lane)]
    model = args.model or primary.get("model", "")
    state = {
        "schema_version": "2.0" if args.lane == "continuation-v2" else "1.0",
        "lane": args.lane,
        "started_at": now(),
        "status": "planned" if primary.get("status") == "complete" else "waiting_for_primary_batch",
        "dependency": {
            "primary_batch_status": primary.get("status"),
            "primary_batch_started_at": primary.get("started_at"),
            "required_status": "complete",
        },
        "model": model,
        "endpoint": args.endpoint,
        "package_count": len(packages),
        "valid_receipts_before_run": completed_before,
        "processed_this_run": 0,
        "pass_this_run": 0,
        "needs_review_this_run": 0,
        "remaining": len(pending),
        "current_package_id": None,
        "errors": [],
        "safety": {
            "external_model_calls": 0,
            "incremental_usd": 0,
            "canonical_writes": False,
            "embedding_calls": False,
            "taiwan_name_resolution": False,
            "layout_or_plate_approval": False,
        },
    }
    write_status(status_path, state)
    if not args.execute:
        print(json.dumps(state, ensure_ascii=False))
        return
    if primary.get("status") != "complete":
        raise SystemExit("primary local structure batch must be complete before continuation execution")
    if not model:
        raise SystemExit("--model or the primary batch model is required")

    started_service = False
    if not endpoint_ready(args.models_endpoint) and args.start_service:
        subprocess.run([str(WORKSTATION / "service"), "qwen", "on"], cwd=WORKSTATION, check=True)
        started_service = True
    if not endpoint_ready(args.models_endpoint):
        raise SystemExit("local model endpoint is unavailable; use --start-service after the primary batch completes")

    selected = pending[:args.limit]
    lock_path = WORKSTATION / "services/qwen35-mlx/runtime/preembedding-model.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state["status"] = "running"
        write_status(status_path, state)
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for package in selected:
                state["current_package_id"] = package["package_id"]
                write_status(status_path, state)
                started = time.monotonic()
                raw_response = ""
                try:
                    raw_response, elapsed = request_local(args.endpoint, model, prompt_for(package, pages), args.max_tokens)
                    draft = extract_json(raw_response)
                    errors, locators = materialize_and_validate(draft, package, pages)
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    draft, locators = None, []
                    errors = [f"model_response_error:{type(exc).__name__}:{str(exc)[:240]}"]
                attempt = {
                    "schema_version": "1.0",
                    "package_id": package["package_id"],
                    "package_sha256": package["package_sha256"],
                    "model": model,
                    "prompt_version": "plant-structure-line-coordinates-v2",
                    "raw_response": raw_response,
                    "raw_response_sha256": sha256_text(raw_response),
                    "parse_or_validation_errors": errors,
                }
                attempt["attempt_sha256"] = object_hash(attempt, "attempt_sha256")
                if args.lane == "continuation-v2":
                    attempt_dir = root / "structure/continuation-v2-attempts" / package["package_id"].replace(":", "__")
                    prior_path = receipt_path(root, package["package_id"], args.lane)
                    if prior_path.exists():
                        prior = read_json(prior_path)
                        prior_hash = prior.get("receipt_sha256", "unhashed")
                        write_json(attempt_dir / f"prior-receipt-{prior_hash}.json", prior)
                    write_json(attempt_dir / f"attempt-{attempt['attempt_sha256']}.json", attempt)
                receipt = {
                    "schema_version": "2.0" if args.lane == "continuation-v2" else "1.0",
                    "package_id": package["package_id"],
                    "parent_entry_id": package["parent_entry_id"],
                    "child_entry_id": package.get("child_entry_id"),
                    "recovered_entry_id": package.get("recovered_entry_id"),
                    "work_id": package["work_id"],
                    "owner_shard": package["owner_shard"],
                    "package_sha256": package["package_sha256"],
                    "model": model,
                    "prompt_version": "plant-structure-line-coordinates-v2" if args.lane == "continuation-v2" else "plant-structure-continuation-line-anchors-v1",
                    "attempt_sha256": attempt["attempt_sha256"],
                    "raw_response_sha256": attempt["raw_response_sha256"],
                    "elapsed_seconds": round(elapsed, 3),
                    "external_model_calls": 0,
                    "incremental_usd": 0,
                    "name_resolution_status": "unresolved",
                    "layout_or_plate_claims_approved": False,
                    "deterministic_status": "pass" if not errors else "needs_review",
                    "errors": errors,
                    "section_source_locators": locators,
                    "draft": draft,
                }
                receipt["receipt_sha256"] = object_hash(receipt, "receipt_sha256")
                write_json(receipt_path(root, package["package_id"], args.lane), receipt)
                state["processed_this_run"] += 1
                key = "pass_this_run" if receipt["deterministic_status"] == "pass" else "needs_review_this_run"
                state[key] += 1
                state["remaining"] = len(packages) - completed_before - state["pass_this_run"]
                write_status(status_path, state)
        state["current_package_id"] = None
        state["status"] = "complete" if state["remaining"] == 0 else "partial"
        write_status(status_path, state)
    except Exception as exc:
        state["status"] = "failed"
        state["errors"].append(f"{type(exc).__name__}:{str(exc)[:1000]}")
        write_status(status_path, state)
        raise
    finally:
        if started_service and not args.keep_service_on:
            subprocess.run([str(WORKSTATION / "service"), "qwen", "off"], cwd=WORKSTATION, check=False)
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
