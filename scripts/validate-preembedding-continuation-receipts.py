#!/usr/bin/env python3
"""Validate local continuation maker receipts against frozen source evidence.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
SECTION_TYPES = {
    "taxonomy", "description", "anatomy", "distribution", "history",
    "flowering", "harvest", "constituents", "historical_use", "literature",
    "plate_description", "other",
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def object_hash(value: dict, field: str) -> str:
    return sha256_text(canonical_json({key: item for key, item in value.items() if key != field}))


def taxon_binomial(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    tokens = re.findall(r"[A-Za-z][A-Za-z.-]*", value)
    if len(tokens) < 2:
        return None
    return tokens[0].casefold(), tokens[1].casefold()


def load_pages(root: Path) -> dict[tuple[str, int], dict]:
    pages = {}
    for path in sorted((root / "shards").glob("S*/inputs/pages.jsonl")):
        for page in read_jsonl(path):
            key = (page["source_id"], page["pdf_page"])
            if key in pages or page["text_sha256"] != sha256_text(page["text"]):
                fail(f"duplicate or hash-drifted frozen page: {key}")
            pages[key] = page
    return pages


def validate_receipt(receipt: dict, package: dict, pages: dict[tuple[str, int], dict], lane: str) -> list[str]:
    errors: list[str] = []
    if receipt.get("receipt_sha256") != object_hash(receipt, "receipt_sha256"):
        errors.append("receipt_sha256_mismatch")
    expected_identity = {
        "package_id": package["package_id"],
        "parent_entry_id": package["parent_entry_id"],
        "work_id": package["work_id"],
        "owner_shard": package["owner_shard"],
        "package_sha256": package["package_sha256"],
    }
    for field, expected in expected_identity.items():
        if receipt.get(field) != expected:
            errors.append(f"{field}_mismatch")
    if lane == "continuation-v2":
        if receipt.get("child_entry_id") != package.get("child_entry_id"):
            errors.append("child_entry_id_mismatch")
        if receipt.get("prompt_version") != "plant-structure-line-coordinates-v2":
            errors.append("prompt_version_mismatch")
        if not receipt.get("attempt_sha256") or not receipt.get("raw_response_sha256"):
            errors.append("attempt_provenance_missing")
    if receipt.get("external_model_calls") != 0 or receipt.get("incremental_usd") != 0:
        errors.append("nonlocal_or_nonzero_cost_receipt")
    if receipt.get("name_resolution_status") != "unresolved":
        errors.append("name_resolution_status_must_be_unresolved")
    if receipt.get("layout_or_plate_claims_approved") is not False:
        errors.append("layout_or_plate_claim_was_approved")
    draft = receipt.get("draft")
    if not isinstance(draft, dict):
        return sorted(set(errors + ["draft_missing_or_not_object"]))
    if draft.get("package_id") != package["package_id"] or draft.get("parent_entry_id") != package["parent_entry_id"]:
        errors.append("draft_identity_mismatch")
    if draft.get("review_status") != "machine_extracted":
        errors.append("review_status_must_be_machine_extracted")
    if draft.get("display_name") is not None:
        errors.append("display_name_must_be_null")
    if draft.get("name_resolution") != {"status": "unresolved", "sources": []}:
        errors.append("draft_name_resolution_must_be_unresolved_without_sources")
    draft_taxon = draft.get("book_taxon", {}).get("scientific_name_candidate")
    if taxon_binomial(draft_taxon) != taxon_binomial(package.get("book_taxon_candidate")):
        errors.append("book_taxon_candidate_mismatch")
    sections = draft.get("sections")
    if not isinstance(sections, list) or not sections:
        return sorted(set(errors + ["no_sections"]))
    if len(sections) > 6:
        errors.append("too_many_sections")
    locators = receipt.get("section_source_locators")
    if not isinstance(locators, list):
        return sorted(set(errors + ["section_source_locators_missing"]))
    locators_by_index = {item.get("section_index"): item for item in locators}
    if len(locators_by_index) != len(locators):
        errors.append("duplicate_section_locator_index")
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
        locator = locators_by_index.get(index)
        if not isinstance(locator, dict):
            errors.append(f"{prefix}:missing_locator")
            continue
        page_number = section.get("pdf_page")
        key = (package["source_id"], page_number)
        page = pages.get(key)
        if page is None or page_number not in package["pdf_pages"]:
            errors.append(f"{prefix}:page_outside_package:p{page_number}")
            continue
        line_range = section.get("source_line_range")
        if not isinstance(line_range, list) or len(line_range) != 2 or not all(isinstance(item, int) for item in line_range):
            errors.append(f"{prefix}:invalid_line_range")
            continue
        start, end = line_range
        lines = page["text"].splitlines()
        if start < 1 or end < start or end > len(lines) or end - start + 1 > 60:
            errors.append(f"{prefix}:line_range_out_of_bounds")
            continue
        quote = "\n".join(lines[start - 1:end])
        char_start = page["text"].find(quote)
        if char_start < 0 or page["text"].find(quote, char_start + 1) >= 0:
            errors.append(f"{prefix}:quote_not_uniquely_locatable")
            continue
        source_pdf_sha256 = next(
            item["source_pdf_sha256"] for item in package["source_locators"]
            if item["pdf_page"] == page_number
        )
        expected_locator = {
            "source_id": package["source_id"],
            "volume": package["volume"],
            "pdf_page": page_number,
            "source_pdf_sha256": source_pdf_sha256,
            "char_start": char_start,
            "char_end": char_start + len(quote),
            "source_line_start": start,
            "source_line_end": end,
            "page_text_sha256": page["text_sha256"],
            "exact_text_sha256": sha256_text(quote),
            "section_index": index,
            "section_type": section_type,
        }
        if locator != expected_locator:
            errors.append(f"{prefix}:locator_drift")
        if section.get("exact_source_quote") != quote:
            errors.append(f"{prefix}:exact_source_quote_drift")
        zh_tw = section.get("zh_tw_rendering_candidate")
        if zh_tw is not None and (not isinstance(zh_tw, str) or len(zh_tw) > 120):
            errors.append(f"{prefix}:invalid_zh_tw_rendering")
    if len(locators) != len(sections):
        errors.append("section_locator_count_mismatch")
    declared_errors = receipt.get("errors")
    declared_status = receipt.get("deterministic_status")
    if declared_status == "pass" and declared_errors != []:
        errors.append("pass_receipt_declares_errors")
    if declared_status not in {"pass", "needs_review"}:
        errors.append("invalid_deterministic_status")
    return sorted(set(errors))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--lane", choices=["continuation", "continuation-v2"], default="continuation")
    args = parser.parse_args()
    root = args.root
    pages = load_pages(root)
    package_name = "continuation-work-packages-v2.jsonl" if args.lane == "continuation-v2" else "continuation-work-packages.jsonl"
    packages = read_jsonl(root / "structure" / package_name)
    packages_by_id = {item["package_id"]: item for item in packages}
    expected_count = 46 if args.lane == "continuation-v2" else 41
    if len(packages) != expected_count or len(packages_by_id) != expected_count:
        fail(f"expected {expected_count} unique {args.lane} packages")
    status_name = "continuation-v2-batch-status.json" if args.lane == "continuation-v2" else "continuation-batch-status.json"
    status = read_json(root / "checks" / status_name)
    if status.get("status_sha256") != object_hash(status, "status_sha256"):
        fail("continuation batch status hash mismatch")
    if status.get("package_count") != len(packages):
        fail("continuation batch status package count mismatch")
    if status.get("safety") != {
        "external_model_calls": 0,
        "incremental_usd": 0,
        "canonical_writes": False,
        "embedding_calls": False,
        "taiwan_name_resolution": False,
        "layout_or_plate_approval": False,
    }:
        fail("continuation batch status safety contract drifted")

    receipt_dir_name = "continuation-v2-maker-receipts" if args.lane == "continuation-v2" else "continuation-maker-receipts"
    receipt_dir = root / "structure" / receipt_dir_name
    receipt_paths = sorted(receipt_dir.glob("*.json")) if receipt_dir.exists() else []
    seen = set()
    passed = 0
    needs_review = []
    for path in receipt_paths:
        receipt = read_json(path)
        package_id = receipt.get("package_id")
        package = packages_by_id.get(package_id)
        if package is None or package_id in seen:
            fail(f"unknown or duplicate continuation receipt: {package_id}")
        seen.add(package_id)
        expected_filename = f"{package_id.replace(':', '__')}.json"
        if path.name != expected_filename:
            fail(f"continuation receipt filename drift: {path.name}")
        errors = validate_receipt(receipt, package, pages, args.lane)
        if errors:
            needs_review.append({"package_id": package_id, "errors": errors})
        elif receipt.get("deterministic_status") == "pass":
            passed += 1
        else:
            needs_review.append({"package_id": package_id, "errors": receipt.get("errors", [])})
    complete = passed == len(packages) and not needs_review
    if status.get("status") == "complete" and not complete:
        fail(f"{args.lane} batch claims complete without {expected_count} deterministic passes")
    if args.require_complete and not complete:
        fail("continuation receipts are not complete")
    print(json.dumps({
        "status": "PASS",
        "lane": args.lane,
        "package_count": len(packages),
        "receipts_found": len(receipt_paths),
        "deterministic_pass": passed,
        "needs_review": len(needs_review),
        "needs_review_package_ids": [item["package_id"] for item in needs_review],
        "remaining": len(packages) - passed,
        "complete": complete,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
