#!/usr/bin/env python3
"""Build a fail-closed continuation-v2 staging integration.

This helper is deliberately isolated from the canonical integration.  It reads
the boundary overlay plus 46 taxon-safe packages and their local Qwen receipts.
It emits 34 child candidates only when every package has a deterministic,
source-exact package/attempt/receipt chain.

author: Codex (GPT-5)
date: 2026-08-20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
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


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def object_hash(value: dict, field: str) -> str:
    return digest({key: item for key, item in value.items() if key != field})


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def write_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path: Path, values: list[dict]) -> None:
    atomic_write(path, "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values))


def extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response contains no JSON object")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("response JSON is not an object")
    return value


def load_pages(root: Path) -> dict[tuple[str, int], dict]:
    pages = {}
    for path in sorted((root / "shards").glob("S*/inputs/pages.jsonl")):
        for page in read_jsonl(path):
            key = (page["source_id"], page["pdf_page"])
            if key in pages:
                raise SystemExit(f"duplicate frozen page: {key}")
            pages[key] = page
    return pages


def validate_plan(plan: dict) -> tuple[list[str], dict[str, dict], dict[str, dict]]:
    errors = []
    if plan.get("plan_sha256") != object_hash(plan, "plan_sha256"):
        errors.append("boundary_overlay_plan_hash_mismatch")
    if plan.get("summary", {}).get("parents") != 18 or plan.get("summary", {}).get("child_segments") != 34:
        errors.append("boundary_overlay_plan_count_mismatch")
    if any(value is not False for value in plan.get("safety", {}).values()):
        errors.append("unsafe_boundary_overlay_plan")
    parents = {}
    segments = {}
    for parent in plan.get("parents", []):
        parent_id = parent.get("parent_entry_id")
        if parent_id in parents:
            errors.append(f"duplicate_parent:{parent_id}")
            continue
        parents[parent_id] = parent
        if parent.get("parent_plan_sha256") != object_hash(parent, "parent_plan_sha256"):
            errors.append(f"parent_plan_hash_mismatch:{parent_id}")
        expected_pages = list(range(parent.get("start_pdf_page", 0), parent.get("end_pdf_page", -1) + 1))
        covered = []
        if parent.get("safe_to_replace_old_continuation") is not True or parent.get("canonical_write_allowed") is not False:
            errors.append(f"unsafe_parent_plan:{parent_id}")
        if parent.get("review_candidates") != []:
            errors.append(f"unresolved_boundary_review_candidates:{parent_id}")
        for segment in parent.get("segments", []):
            child_id = segment.get("child_entry_id")
            if child_id in segments:
                errors.append(f"duplicate_child:{child_id}")
                continue
            segments[child_id] = segment
            if segment.get("segment_sha256") != object_hash(segment, "segment_sha256"):
                errors.append(f"segment_hash_mismatch:{child_id}")
            if segment.get("page_count") != len(segment.get("pdf_pages", [])):
                errors.append(f"segment_page_count_mismatch:{child_id}")
            if segment.get("pdf_pages") != list(range(segment.get("start_pdf_page", 0), segment.get("end_pdf_page", -1) + 1)):
                errors.append(f"noncontiguous_segment:{child_id}")
            covered.extend(segment.get("pdf_pages", []))
        if covered != expected_pages or len(covered) != len(set(covered)) or parent.get("exact_page_coverage") is not True:
            errors.append(f"parent_page_coverage_mismatch:{parent_id}")
    if len(parents) != 18 or len(segments) != 34:
        errors.append("boundary_overlay_cardinality_mismatch")
    return sorted(set(errors)), parents, segments


def package_errors(
    package: dict,
    plan: dict,
    segments: dict[str, dict],
    pages: dict[tuple[str, int], dict],
    source_hashes: dict[str, str],
) -> list[str]:
    errors = []
    package_id = package.get("package_id")
    segment = segments.get(package.get("child_entry_id"))
    if package.get("schema_version") != "2.0" or package.get("stage") != "local_structure_continuation_v2":
        errors.append("invalid_package_schema_or_stage")
    if package.get("package_sha256") != object_hash(package, "package_sha256"):
        errors.append("package_sha256_mismatch")
    if segment is None:
        return sorted(set(errors + ["unknown_child_segment"]))
    if package.get("boundary_overlay_plan_sha256") != plan.get("plan_sha256"):
        errors.append("boundary_overlay_plan_sha256_mismatch")
    if package.get("boundary_segment_sha256") != segment.get("segment_sha256"):
        errors.append("boundary_segment_sha256_mismatch")
    if package.get("book_taxon_candidate") != segment.get("taxon_candidate"):
        errors.append("taxon_boundary_mismatch")
    pdf_pages = package.get("pdf_pages", [])
    if package.get("page_count") != len(pdf_pages) or not 1 <= len(pdf_pages) <= 6:
        errors.append("invalid_package_page_count")
    if any(page not in segment.get("pdf_pages", []) for page in pdf_pages):
        errors.append("package_page_outside_child_boundary")
    if package.get("review_status") != "candidate" or package.get("name_resolution_status") != "unresolved":
        errors.append("unsafe_package_review_or_name_status")
    if package.get("layout_or_plate_claims_approved") is not False:
        errors.append("package_layout_or_plate_self_approved")
    if package.get("dependencies") != ["primary_local_structure_batch_complete", "boundary_overlay_plan_valid"]:
        errors.append("package_dependency_drift")
    if package.get("forbidden") != [
        "canonical_record_write", "canonical_chunk_write", "embedding_index_write",
        "source_pdf_write", "external_api", "taiwan_name_invention",
        "layout_or_plate_self_approval",
    ]:
        errors.append("package_forbidden_contract_drift")
    locators = package.get("source_locators", [])
    if [item.get("pdf_page") for item in locators] != pdf_pages:
        errors.append("package_source_locator_coverage_mismatch")
    for locator in locators:
        key = (package.get("source_id"), locator.get("pdf_page"))
        page = pages.get(key)
        if page is None:
            errors.append(f"missing_frozen_page:p{locator.get('pdf_page')}")
            continue
        expected_source_hash = source_hashes.get(package.get("source_id"))
        if (
            locator.get("source_id") != package.get("source_id")
            or locator.get("volume") != package.get("volume")
            or locator.get("source_pdf_sha256") != expected_source_hash
            or locator.get("char_start") != 0
            or locator.get("char_end") != len(page["text"])
            or locator.get("page_text_sha256") != page["text_sha256"]
            or locator.get("exact_text_sha256") != text_hash(page["text"])
        ):
            errors.append(f"package_source_locator_drift:p{locator.get('pdf_page')}")
    return sorted(set(errors))


def materialize_raw_section(
    raw: dict,
    section: dict,
    package: dict,
    pages: dict[tuple[str, int], dict],
    source_hashes: dict[str, str],
    section_index: int,
) -> tuple[dict | None, dict | None, list[str]]:
    errors = []
    section_type = raw.get("section_type")
    pdf_page = raw.get("pdf_page")
    start, end = raw.get("source_line_start"), raw.get("source_line_end")
    if section_type not in SECTION_TYPES:
        errors.append(f"section_{section_index}:invalid_section_type")
    if pdf_page not in package.get("pdf_pages", []):
        errors.append(f"section_{section_index}:page_outside_package")
        return None, None, errors
    page = pages.get((package["source_id"], pdf_page))
    if page is None:
        errors.append(f"section_{section_index}:missing_frozen_page")
        return None, None, errors
    lines = page["text"].splitlines()
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > len(lines) or end - start + 1 > 60:
        errors.append(f"section_{section_index}:invalid_line_range")
        return None, None, errors
    quote = "\n".join(lines[start - 1:end])
    char_start = page["text"].find(quote)
    if not quote.strip() or char_start < 0 or page["text"].find(quote, char_start + 1) >= 0:
        errors.append(f"section_{section_index}:nonunique_exact_quote")
        return None, None, errors
    expected_section = dict(raw)
    expected_section.pop("source_line_start", None)
    expected_section.pop("source_line_end", None)
    expected_section["source_line_range"] = [start, end]
    expected_section["exact_source_quote"] = quote
    if section != expected_section:
        errors.append(f"section_{section_index}:receipt_draft_drift_from_attempt")
    locator = {
        "source_id": package["source_id"], "volume": package["volume"],
        "pdf_page": pdf_page, "source_pdf_sha256": source_hashes[package["source_id"]],
        "char_start": char_start, "char_end": char_start + len(quote),
        "source_line_start": start, "source_line_end": end,
        "page_text_sha256": page["text_sha256"], "exact_text_sha256": text_hash(quote),
        "section_index": section_index, "section_type": section_type,
    }
    return expected_section, locator, errors


def receipt_errors(
    root: Path,
    package: dict,
    receipt: dict,
    pages: dict[tuple[str, int], dict],
    source_hashes: dict[str, str],
) -> tuple[list[str], list[dict], dict | None, str | None, dict | None]:
    errors = []
    package_id = package["package_id"]
    identity = {
        "schema_version": "2.0", "package_id": package_id,
        "parent_entry_id": package["parent_entry_id"], "child_entry_id": package["child_entry_id"],
        "recovered_entry_id": None, "work_id": package["work_id"],
        "owner_shard": package["owner_shard"], "package_sha256": package["package_sha256"],
        "prompt_version": "plant-structure-line-coordinates-v2",
    }
    for field, expected in identity.items():
        if receipt.get(field) != expected:
            errors.append(f"receipt_{field}_mismatch")
    if receipt.get("receipt_sha256") != object_hash(receipt, "receipt_sha256"):
        errors.append("receipt_sha256_mismatch")
    if receipt.get("external_model_calls") != 0 or receipt.get("incremental_usd") != 0:
        errors.append("receipt_nonlocal_or_nonzero_cost")
    if receipt.get("name_resolution_status") != "unresolved" or receipt.get("layout_or_plate_claims_approved") is not False:
        errors.append("unsafe_receipt_name_layout_status")
    if receipt.get("deterministic_status") != "pass" or receipt.get("errors") != []:
        errors.append("receipt_not_deterministic_pass")
    attempt_hash = receipt.get("attempt_sha256")
    attempt_path = (
        root / "structure/continuation-v2-attempts" / package_id.replace(":", "__")
        / f"attempt-{attempt_hash}.json"
    )
    attempt = read_json(attempt_path) if attempt_path.is_file() else None
    if attempt is None:
        errors.append("linked_attempt_missing")
        return sorted(set(errors)), [], None, None, None
    repair = receipt.get("changed_strategy_repair")
    repair_projection = None
    dropped_indexes: set[int] = set()
    if repair is not None:
        if not isinstance(repair, dict):
            errors.append("changed_strategy_repair_not_object")
            repair = {}
        if (
            repair.get("strategy") != "drop-unmaterialized-sections-v1"
            or repair.get("external_model_calls") != 0
            or repair.get("content_added") is not False
            or repair.get("line_numbers_guessed_or_clamped") is not False
            or receipt.get("model") != "deterministic-local-repair-no-model-call"
            or receipt.get("elapsed_seconds") != 0.0
        ):
            errors.append("unsafe_changed_strategy_repair_contract")
        raw_dropped = repair.get("dropped_section_indexes")
        if (
            not isinstance(raw_dropped, list) or not raw_dropped
            or not all(isinstance(value, int) and value >= 0 for value in raw_dropped)
            or len(raw_dropped) != len(set(raw_dropped))
        ):
            errors.append("invalid_changed_strategy_drop_indexes")
            raw_dropped = []
        dropped_indexes = set(raw_dropped)
        source_errors = repair.get("source_errors")
        if not isinstance(source_errors, list) or not source_errors:
            errors.append("changed_strategy_source_errors_missing")
            source_errors = []
        error_indexes = set()
        for error in source_errors:
            match = re.match(r"section_(\d+):", error) if isinstance(error, str) else None
            if match:
                error_indexes.add(int(match.group(1)))
        if error_indexes != dropped_indexes:
            errors.append("changed_strategy_drops_not_exact_error_sections")
        source_receipt_hash = repair.get("source_receipt_sha256")
        prior_path = (
            root / "structure/continuation-v2-attempts" / package_id.replace(":", "__")
            / f"prior-receipt-{source_receipt_hash}.json"
        )
        prior = read_json(prior_path) if prior_path.is_file() else None
        if prior is None:
            errors.append("changed_strategy_prior_receipt_missing")
        else:
            if (
                prior.get("receipt_sha256") != object_hash(prior, "receipt_sha256")
                or prior.get("receipt_sha256") != source_receipt_hash
                or prior.get("package_id") != package_id
                or prior.get("package_sha256") != package["package_sha256"]
                or prior.get("deterministic_status") != "needs_review"
                or prior.get("errors") != source_errors
                or prior.get("attempt_sha256") != repair.get("source_attempt_sha256")
            ):
                errors.append("changed_strategy_prior_receipt_chain_mismatch")
        source_attempt_hash = repair.get("source_attempt_sha256")
        source_attempt_path = (
            root / "structure/continuation-v2-attempts" / package_id.replace(":", "__")
            / f"attempt-{source_attempt_hash}.json"
        )
        source_attempt = read_json(source_attempt_path) if source_attempt_path.is_file() else None
        if source_attempt is None:
            errors.append("changed_strategy_source_attempt_missing")
        elif (
            source_attempt.get("attempt_sha256") != object_hash(source_attempt, "attempt_sha256")
            or source_attempt.get("attempt_sha256") != source_attempt_hash
            or source_attempt.get("package_id") != package_id
            or source_attempt.get("package_sha256") != package["package_sha256"]
            or source_attempt.get("parse_or_validation_errors") != source_errors
            or source_attempt.get("raw_response_sha256") != text_hash(source_attempt.get("raw_response", ""))
            or (prior is not None and source_attempt.get("model") != prior.get("model"))
        ):
            errors.append("changed_strategy_source_attempt_chain_mismatch")
        repair_projection = {
            "strategy": repair.get("strategy"),
            "source_receipt_path": str(prior_path.relative_to(root)),
            "source_receipt_sha256": source_receipt_hash,
            "source_attempt_sha256": repair.get("source_attempt_sha256"),
            "dropped_section_indexes": raw_dropped,
            "source_errors": source_errors,
            "content_added": repair.get("content_added"),
            "line_numbers_guessed_or_clamped": repair.get("line_numbers_guessed_or_clamped"),
        }
    if attempt.get("attempt_sha256") != object_hash(attempt, "attempt_sha256") or attempt.get("attempt_sha256") != attempt_hash:
        errors.append("attempt_sha256_mismatch")
    if attempt.get("package_id") != package_id or attempt.get("package_sha256") != package["package_sha256"]:
        errors.append("attempt_package_identity_mismatch")
    if attempt.get("model") != receipt.get("model") or attempt.get("prompt_version") != receipt.get("prompt_version"):
        errors.append("attempt_model_or_prompt_mismatch")
    if isinstance(repair, dict) and (
        attempt.get("source_receipt_sha256") != repair.get("source_receipt_sha256")
        or attempt.get("source_attempt_sha256") != repair.get("source_attempt_sha256")
        or attempt.get("repair_strategy") != repair.get("strategy")
        or attempt.get("dropped_section_indexes") != repair.get("dropped_section_indexes")
        or attempt.get("source_errors") != repair.get("source_errors")
    ):
        errors.append("changed_strategy_repair_attempt_projection_mismatch")
    raw_response = attempt.get("raw_response")
    if not isinstance(raw_response, str) or attempt.get("raw_response_sha256") != text_hash(raw_response):
        errors.append("attempt_raw_response_sha256_mismatch")
        raw_response = raw_response if isinstance(raw_response, str) else ""
    if receipt.get("raw_response_sha256") != attempt.get("raw_response_sha256"):
        errors.append("receipt_attempt_raw_response_chain_mismatch")
    if attempt.get("parse_or_validation_errors") != []:
        errors.append("attempt_contains_validation_errors")
    try:
        raw_draft = extract_json(raw_response)
    except Exception as exc:
        errors.append(f"attempt_raw_json_invalid:{type(exc).__name__}")
        return sorted(set(errors)), [], attempt, str(attempt_path.relative_to(root)), repair_projection
    draft = receipt.get("draft")
    if not isinstance(draft, dict):
        errors.append("receipt_draft_missing")
        return sorted(set(errors)), [], attempt, str(attempt_path.relative_to(root)), repair_projection
    for field, expected in {
        "package_id": package_id, "parent_entry_id": package["parent_entry_id"],
        "display_name": None, "name_resolution": {"status": "unresolved", "sources": []},
        "review_status": "machine_extracted",
    }.items():
        if draft.get(field) != expected or (repair is None and raw_draft.get(field) != expected):
            errors.append(f"draft_{field}_mismatch")
    raw_taxon = raw_draft.get("book_taxon", {}).get("scientific_name_candidate") if repair is None else package["book_taxon_candidate"]
    receipt_taxon = draft.get("book_taxon", {}).get("scientific_name_candidate")
    if raw_taxon != package["book_taxon_candidate"] or receipt_taxon != package["book_taxon_candidate"]:
        errors.append("draft_taxon_boundary_mismatch")
    raw_sections = raw_draft.get("sections")
    sections = draft.get("sections")
    if repair is not None:
        retained = raw_draft.get("retained_source_line_coordinates")
        if set(raw_draft) != {"package_id", "retained_source_line_coordinates"} or raw_draft.get("package_id") != package_id or not isinstance(retained, list):
            errors.append("changed_strategy_retained_coordinate_projection_invalid")
            retained = []
        raw_sections = []
        for value in retained:
            line_range = value.get("source_line_range") if isinstance(value, dict) else None
            if (
                not isinstance(value, dict) or set(value) != {"pdf_page", "section_type", "source_line_range"}
                or not isinstance(line_range, list) or len(line_range) != 2
            ):
                errors.append("changed_strategy_retained_coordinate_invalid")
                continue
            raw_sections.append({
                "section_type": value["section_type"], "pdf_page": value["pdf_page"],
                "source_line_start": line_range[0], "source_line_end": line_range[1],
            })
        expected_warning = "Deterministic changed strategy dropped only sections that lacked an exact source locator; no line number was repaired or guessed."
        if draft.get("warnings") != list(raw_draft.get("warnings", [])) + [expected_warning]:
            errors.append("changed_strategy_warning_projection_mismatch")
    if not isinstance(raw_sections, list) or not isinstance(sections, list) or not raw_sections or len(raw_sections) != len(sections) or len(sections) > 6:
        errors.append("draft_sections_invalid")
        return sorted(set(errors)), [], attempt, str(attempt_path.relative_to(root)), repair_projection
    seen_types = set()
    locators = []
    for index, (raw_section, section) in enumerate(zip(raw_sections, sections)):
        if not isinstance(raw_section, dict) or not isinstance(section, dict):
            errors.append(f"section_{index}:not_object")
            continue
        section_type = raw_section.get("section_type")
        if section_type in seen_types:
            errors.append(f"section_{index}:duplicate_section_type")
        seen_types.add(section_type)
        _, locator, section_errors = materialize_raw_section(
            raw_section, section, package, pages, source_hashes, index
        )
        errors.extend(section_errors)
        if locator:
            locators.append(locator)
    if receipt.get("section_source_locators") != locators:
        errors.append("receipt_section_source_locators_drift")
    return sorted(set(errors)), locators, attempt, str(attempt_path.relative_to(root)), repair_projection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    root = args.root
    plan = read_json(root / "boundary-evidence-v1/boundary-overlay-plan.json")
    package_manifest = read_json(root / "structure/continuation-work-packages-v2-manifest.json")
    packages = read_jsonl(root / "structure/continuation-work-packages-v2.jsonl")
    source_receipt_path = root / "source-receipt.json"
    source_receipt = read_json(source_receipt_path)
    source_hashes = {item["source_id"]: item["sha256"] for item in source_receipt["sources"]}
    pages = load_pages(root)
    plan_errors, parents, segments = validate_plan(plan)
    if len(source_hashes) != 4 or len(source_hashes) != len(source_receipt.get("sources", [])):
        plan_errors.append("source_receipt_cardinality_or_uniqueness_mismatch")
    manifest_errors = []
    if package_manifest.get("manifest_sha256") != object_hash(package_manifest, "manifest_sha256"):
        manifest_errors.append("package_manifest_sha256_mismatch")
    expected_manifest = {
        "boundary_overlay_plan_sha256": plan.get("plan_sha256"), "parent_count": 18,
        "child_segment_count": 34, "package_count": 46,
        "maximum_pages_per_package": 6, "old_package_count": 41,
        "old_packages_modified": False, "canonical_writes": False,
    }
    for field, expected in expected_manifest.items():
        if package_manifest.get(field) != expected:
            manifest_errors.append(f"package_manifest_{field}_mismatch")
    if len(packages) != 46 or len({item.get("package_id") for item in packages}) != 46:
        manifest_errors.append("package_set_cardinality_mismatch")

    checks = []
    by_child: dict[str, list[dict]] = defaultdict(list)
    for package in packages:
        by_child[package.get("child_entry_id")].append(package)
    package_set_errors = []
    for child_id, segment in segments.items():
        child_packages = sorted(by_child.get(child_id, []), key=lambda item: item.get("sequence", 0))
        covered = [page for package in child_packages for page in package.get("pdf_pages", [])]
        if covered != segment.get("pdf_pages") or len(covered) != len(set(covered)):
            package_set_errors.append(f"child_package_page_coverage_mismatch:{child_id}")
        if [item.get("sequence") for item in child_packages] != list(range(1, len(child_packages) + 1)):
            package_set_errors.append(f"child_package_sequence_mismatch:{child_id}")
        if any(item.get("sequence_count") != len(child_packages) for item in child_packages):
            package_set_errors.append(f"child_package_sequence_count_mismatch:{child_id}")
    if set(by_child) != set(segments):
        package_set_errors.append("package_child_set_mismatch")

    for package in packages:
        package_id = package["package_id"]
        errors = package_errors(package, plan, segments, pages, source_hashes)
        receipt_path = root / "structure/continuation-v2-maker-receipts" / f"{package_id.replace(':', '__')}.json"
        receipt = read_json(receipt_path) if receipt_path.is_file() else None
        locators = []
        attempt = None
        attempt_path = None
        repair_projection = None
        if receipt is None:
            status = "awaiting_receipt"
        else:
            receipt_validation_errors, locators, attempt, attempt_path, repair_projection = receipt_errors(
                root, package, receipt, pages, source_hashes
            )
            errors.extend(receipt_validation_errors)
            status = "pass" if not errors else "needs_review"
        check = {
            "schema_version": "2.0", "package_id": package_id,
            "parent_entry_id": package["parent_entry_id"], "child_entry_id": package["child_entry_id"],
            "package_sha256": package["package_sha256"],
            "receipt_path": str(receipt_path.relative_to(root)) if receipt else None,
            "receipt_sha256": receipt.get("receipt_sha256") if receipt else None,
            "attempt_path": attempt_path,
            "attempt_sha256": attempt.get("attempt_sha256") if attempt else None,
            "changed_strategy_repair": repair_projection,
            "status": status, "errors": sorted(set(errors)),
            "section_source_locators": locators, "checked_at": now(),
            "safety": {
                "canonical_writes": False, "chunk_writes": False, "index_writes": False,
                "embedding_calls": False, "external_api_calls": False,
                "taiwan_name_resolution": False, "layout_or_plate_approval": False,
            },
        }
        check["check_sha256"] = object_hash(check, "check_sha256")
        checks.append(check)

    global_errors = sorted(set(plan_errors + manifest_errors + package_set_errors))
    passed = sum(item["status"] == "pass" for item in checks)
    needs_review = sum(item["status"] == "needs_review" for item in checks)
    awaiting = sum(item["status"] == "awaiting_receipt" for item in checks)
    all_packages_pass = not global_errors and passed == 46 and needs_review == 0 and awaiting == 0
    candidates = []
    dispositions = []
    checks_by_package = {item["package_id"]: item for item in checks}
    for parent in plan["parents"]:
        for segment in parent["segments"]:
            child_id = segment["child_entry_id"]
            child_packages = sorted(by_child[child_id], key=lambda item: item["sequence"])
            child_checks = [checks_by_package[item["package_id"]] for item in child_packages]
            child_pass = all(item["status"] == "pass" for item in child_checks)
            disposition = {
                "schema_version": "2.0", "parent_entry_id": parent["parent_entry_id"],
                "child_entry_id": child_id, "source_id": parent["source_id"],
                "taxon_candidate": segment["taxon_candidate"], "pdf_pages": segment["pdf_pages"],
                "boundary_segment_sha256": segment["segment_sha256"],
                "package_ids": [item["package_id"] for item in child_packages],
                "package_checks_passed": sum(item["status"] == "pass" for item in child_checks),
                "package_checks_required": len(child_checks),
                "terminal": all_packages_pass and child_pass,
                "terminal_disposition": (
                    "continuation_v2_taxon_boundary_validated"
                    if all_packages_pass and child_pass
                    else "continuation_v2_global_gate_awaiting_46_passes"
                ),
                "embedding_ready_candidate": all_packages_pass and child_pass,
                "name_resolution_status": "unresolved",
                "layout_or_plate_claims_approved": False,
                "canonical_write_allowed": False,
            }
            disposition["disposition_sha256"] = object_hash(disposition, "disposition_sha256")
            dispositions.append(disposition)
            if not all_packages_pass or not child_pass:
                continue
            merged_sections = []
            seen_spans = {}
            excluded_plate_count = 0
            provenance = []
            for package, check in zip(child_packages, child_checks):
                receipt = read_json(root / check["receipt_path"])
                provenance.append({
                    "package_id": package["package_id"], "package_sha256": package["package_sha256"],
                    "receipt_sha256": receipt["receipt_sha256"], "attempt_sha256": receipt["attempt_sha256"],
                    "validation_check_sha256": check["check_sha256"],
                })
                locators_by_section = {item["section_index"]: item for item in check["section_source_locators"]}
                for section_index, section in enumerate(receipt["draft"]["sections"]):
                    locator = locators_by_section.get(section_index)
                    if locator is None:
                        continue
                    if section["section_type"] == "plate_description":
                        excluded_plate_count += 1
                        continue
                    key = (
                        section["section_type"], locator["source_id"], locator["pdf_page"],
                        locator["char_start"], locator["char_end"], locator["exact_text_sha256"],
                    )
                    span_provenance = {
                        "package_id": package["package_id"], "package_sha256": package["package_sha256"],
                        "receipt_sha256": receipt["receipt_sha256"], "attempt_sha256": receipt["attempt_sha256"],
                        "validation_check_sha256": check["check_sha256"], "package_section_index": section_index,
                    }
                    if key in seen_spans:
                        seen_spans[key]["continuation_v2_provenance"].append(span_provenance)
                        continue
                    value = {
                        "section_type": section["section_type"],
                        "exact_source_quotes": [section["exact_source_quote"]],
                        "normalized_text_candidate": section.get("normalized_text_candidate"),
                        "zh_tw_rendering_candidate": section.get("zh_tw_rendering_candidate"),
                        "source_locators": [locator],
                        "continuation_v2_provenance": [span_provenance],
                    }
                    seen_spans[key] = value
                    merged_sections.append(value)
            if not merged_sections:
                raise SystemExit(f"all-pass child has no non-plate sections: {child_id}")
            candidate = {
                "schema_version": "2.0", "entry_id": child_id,
                "source_parent_entry_id": parent["parent_entry_id"], "source_id": parent["source_id"],
                "volume": child_packages[0]["volume"], "book_taxon_candidate": segment["taxon_candidate"],
                "boundary_overlay_plan_sha256": plan["plan_sha256"],
                "boundary_segment_sha256": segment["segment_sha256"],
                "source_disposition_sha256": disposition["disposition_sha256"],
                "continuation_v2_receipts": provenance,
                "display_name": None, "name_resolution": {"status": "unresolved", "sources": []},
                "review_status": "machine_extracted", "sections": merged_sections,
                "excluded_plate_section_count": excluded_plate_count,
                "layout_or_plate_claims_approved": False, "canonical_write_allowed": False,
                "embedding_call_performed": False,
            }
            candidate["candidate_sha256"] = object_hash(candidate, "candidate_sha256")
            candidates.append(candidate)

    output_dir = root / "integration-v2"
    checks_dir = root / "checks"
    write_jsonl(checks_dir / "continuation-v2-integration-validation.jsonl", checks)
    write_jsonl(output_dir / "child-dispositions.jsonl", dispositions)
    candidate_manifest = {
        "schema_version": "2.0", "pipeline_id": "preembedding-continuation-v2-integration",
        "boundary_overlay_plan_sha256": plan["plan_sha256"],
        "package_manifest_sha256": package_manifest["manifest_sha256"],
        "source_receipt_sha256": hashlib.sha256(source_receipt_path.read_bytes()).hexdigest(),
        "all_packages_deterministic_pass": all_packages_pass,
        "candidate_count": len(candidates), "candidates": candidates,
        "canonical_write_allowed": False, "chunk_write_allowed": False,
        "index_write_allowed": False, "embedding_calls_performed": False,
    }
    candidate_manifest["manifest_sha256"] = object_hash(candidate_manifest, "manifest_sha256")
    write_json(output_dir / "embedding-ready-child-candidate-manifest.json", candidate_manifest)
    summary = {
        "schema_version": "2.0", "pipeline_id": "preembedding-continuation-v2-integration",
        "checked_at": now(), "boundary_overlay_plan_sha256": plan.get("plan_sha256"),
        "package_manifest_sha256": package_manifest.get("manifest_sha256"),
        "global_errors": global_errors, "parents": len(parents), "child_segments": len(segments),
        "packages": len(packages), "receipts_found": passed + needs_review,
        "package_checks_passed": passed, "package_checks_needs_review": needs_review,
        "package_checks_awaiting_receipt": awaiting,
        "child_dispositions": len(dispositions), "embedding_ready_child_candidates": len(candidates),
        "complete": all_packages_pass and len(dispositions) == 34 and len(candidates) == 34,
        "safety": {
            "canonical_writes": False, "chunk_writes": False, "index_writes": False,
            "embedding_calls": False, "external_api_calls": False,
            "taiwan_name_resolution": False, "layout_or_plate_approval": False,
        },
    }
    summary["summary_sha256"] = object_hash(summary, "summary_sha256")
    write_json(checks_dir / "continuation-v2-integration-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    if args.require_complete and not summary["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
