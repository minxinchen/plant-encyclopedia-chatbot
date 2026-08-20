#!/usr/bin/env python3
"""Run one serialized local-LLM maker over eligible entries in a frozen shard.

Outputs remain machine-extracted drafts. The deterministic validator below rejects
quotes that are not exact substrings and forbids Taiwan-name invention or promotion.

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
import time
import urllib.request
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
WORKSTATION = LAB.parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
SECTION_TYPES = {
    "taxonomy", "description", "anatomy", "distribution", "history", "flowering",
    "harvest", "constituents", "historical_use", "literature", "plate_description", "other",
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response contains no JSON object")
    return json.loads(cleaned[start : end + 1])


def request_local(endpoint: str, model: str, prompt: str, max_tokens: int) -> tuple[dict, float]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a maker-only evidence extraction agent. Return one JSON object only. "
                    "Never invent a Taiwan Chinese name, occurrence, modern medical advice, image fact, "
                    "or approval status. Preserve Latin names, numbers and exact German source quotes."
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
    elapsed = time.monotonic() - started
    return extract_json(result["choices"][0]["message"]["content"]), elapsed


def materialize_quotes(draft: dict, pages: dict[int, dict]) -> list[str]:
    """Turn model-selected line ranges into byte-for-byte source quotes."""
    errors = []
    for section in draft.get("sections", []):
        page = section.get("pdf_page")
        start = section.pop("source_line_start", None)
        end = section.pop("source_line_end", None)
        if isinstance(start, int) and isinstance(end, int):
            section["source_line_range"] = [start, end]
        if page not in pages or not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"invalid_source_line_range:p{page}")
            continue
        lines = pages[page]["text"].splitlines()
        if start < 1 or end < start or end > len(lines) or end - start + 1 > 60:
            errors.append(f"invalid_source_line_range:p{page}:L{start}-L{end}")
            continue
        quote = "\n".join(lines[start - 1 : end])
        if not quote.strip():
            errors.append(f"empty_source_line_range:p{page}:L{start}-L{end}")
            continue
        section["exact_source_quote"] = quote
        section["source_line_range"] = [start, end]
    return errors


def validate_draft(draft: dict, entry: dict, pages: dict[int, dict]) -> list[str]:
    errors = []
    if draft.get("entry_id") != entry["entry_id"]:
        errors.append("entry_id_mismatch")
    if draft.get("review_status") != "machine_extracted":
        errors.append("review_status_must_be_machine_extracted")
    if draft.get("display_name") is not None:
        errors.append("display_name_must_be_null")
    if draft.get("name_resolution", {}).get("status") != "unresolved":
        errors.append("name_resolution_must_be_unresolved")
    for section in draft.get("sections", []):
        if section.get("section_type") not in SECTION_TYPES:
            errors.append("invalid_section_type")
            continue
        page = section.get("pdf_page")
        exact = section.get("exact_source_quote")
        if page not in pages or not isinstance(exact, str) or exact not in pages[page]["text"]:
            errors.append(f"source_quote_not_exact:p{page}")
    if not draft.get("sections"):
        errors.append("no_sections")
    return errors


def numbered_page(text: str) -> str:
    return "\n".join(
        f"L{line_no:04d}\t{line}"
        for line_no, line in enumerate(text.splitlines(), 1)
        if line.strip()
    )


def prompt_for(entry: dict, pages: dict[int, dict]) -> str:
    source = "\n\n".join(
        f"=== PDF PAGE {page} ===\n{numbered_page(pages[page]['text'])}" for page in entry["pdf_pages"]
    )
    return f"""Extract a candidate structure for this one Köhler book entry.

ENTRY_ID: {entry['entry_id']}
BOOK_TAXON_CANDIDATE: {entry['book_taxon_candidate']}
ALLOWED_PDF_PAGES: {entry['pdf_pages']}

Required JSON shape:
{{
  "entry_id": "{entry['entry_id']}",
  "book_taxon": {{
    "scientific_name_candidate": "preserve the source spelling",
    "authorship_candidate": null,
    "aliases_candidates": []
  }},
  "display_name": null,
  "name_resolution": {{"status": "unresolved", "sources": []}},
  "sections": [
    {{
      "section_type": "taxonomy|description|anatomy|distribution|history|flowering|harvest|constituents|historical_use|literature|plate_description|other",
      "pdf_page": 1,
      "source_line_start": 1,
      "source_line_end": 3,
      "normalized_text_candidate": null,
      "zh_tw_rendering_candidate": "Traditional Chinese summary constrained to the quote, or null",
      "warnings": []
    }}
  ],
  "review_status": "machine_extracted",
  "warnings": []
}}

Rules:
- Return JSON only. Do not use markdown.
- Return at most 6 representative sections, one per supported section type.
- Keep each exact_source_quote to one short contiguous passage (at most 500 characters).
- Keep each Traditional Chinese summary under 120 characters.
- Select a concise contiguous source range; never exceed 60 numbered lines. The program will copy the exact quote; do not emit exact_source_quote yourself.
- Do not create a Taiwan name or Taiwan occurrence. display_name must be null.
- Historical medical content must be described as historical and never as advice.
- Plate captions may be transcribed as text, but do not infer anything from an image.
- When a field is not supported, use null or an empty array.

SOURCE:
{source}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("shard", choices=[f"S{i:02d}" for i in range(1, 9)])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18080/v1/chat/completions")
    parser.add_argument("--model", default=os.environ.get("QWEN35_MODEL_ID", ""))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=3072)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.model:
        raise SystemExit("--model or QWEN35_MODEL_ID is required")

    shard_root = args.root / "shards" / args.shard
    pages = {row["pdf_page"]: row for row in read_jsonl(shard_root / "inputs/pages.jsonl")}
    entries = [
        row for row in read_jsonl(shard_root / "inputs/entries.jsonl")
        if row["disposition"] == "eligible_local_structure"
    ]
    output_dir = shard_root / "maker/structure-drafts"
    output_dir.mkdir(parents=True, exist_ok=True)
    pending = entries if args.force else [
        entry for entry in entries
        if not (output_dir / f"{entry['entry_id'].replace(':', '__')}.json").exists()
    ]
    selected = pending[: args.limit]
    lock_path = WORKSTATION / "services/qwen35-mlx/runtime/preembedding-model.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for entry in selected:
            started = time.monotonic()
            try:
                draft, elapsed = request_local(args.endpoint, args.model, prompt_for(entry, pages), args.max_tokens)
                errors = materialize_quotes(draft, pages)
                errors.extend(validate_draft(draft, entry, pages))
            except Exception as exc:  # Keep the batch resumable; the draft remains unapproved.
                elapsed = time.monotonic() - started
                draft = None
                errors = [f"model_response_error:{type(exc).__name__}:{str(exc)[:240]}"]
            receipt = {
                "schema_version": "1.0",
                "entry_id": entry["entry_id"],
                "owner_shard": args.shard,
                "model": args.model,
                "prompt_version": "plant-structure-line-anchors-v2",
                "elapsed_seconds": round(elapsed, 3),
                "external_model_calls": 0,
                "incremental_usd": 0,
                "deterministic_status": "pass" if not errors else "needs_review",
                "errors": errors,
                "draft": draft,
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            output = output_dir / f"{entry['entry_id'].replace(':', '__')}.json"
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            results.append({"entry_id": entry["entry_id"], "status": receipt["deterministic_status"]})
    print(json.dumps({"shard": args.shard, "processed": len(results), "remaining": len(pending) - len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
