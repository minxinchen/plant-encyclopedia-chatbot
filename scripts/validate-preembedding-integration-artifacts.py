#!/usr/bin/env python3
"""Independently validate pre-embedding structure/integration staging artifacts.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_object_hash(value: dict, field: str, label: str) -> None:
    expected = value.get(field)
    material = {key: item for key, item in value.items() if key != field}
    if not isinstance(expected, str) or expected != sha256_text(canonical_json(material)):
        fail(f"{label} hash mismatch")


def verify_locator(
    locator: dict,
    pages: dict[tuple[str, int], dict],
    source_pdf_hashes: dict[str, str],
    label: str,
) -> str:
    key = (locator.get("source_id"), locator.get("pdf_page"))
    page = pages.get(key)
    if page is None:
        fail(f"{label} page is outside frozen inputs: {key}")
    if locator.get("page_text_sha256") != page["text_sha256"]:
        fail(f"{label} page hash mismatch: {key}")
    if locator.get("source_pdf_sha256") != source_pdf_hashes[key[0]]:
        fail(f"{label} source PDF hash mismatch: {key}")
    start, end = locator.get("char_start"), locator.get("char_end")
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(page["text"]):
        fail(f"{label} invalid char locator: {key}")
    exact = page["text"][start:end]
    if locator.get("exact_text_sha256") != sha256_text(exact):
        fail(f"{label} exact text hash mismatch: {key}")
    line_start, line_end = locator.get("source_line_start"), locator.get("source_line_end")
    if line_start is not None or line_end is not None:
        if not isinstance(line_start, int) or not isinstance(line_end, int):
            fail(f"{label} incomplete line locator: {key}")
        lines = page["text"].splitlines()
        if not 1 <= line_start <= line_end <= len(lines):
            fail(f"{label} line locator out of bounds: {key}")
        if "\n".join(lines[line_start - 1:line_end]) != exact:
            fail(f"{label} line and char locators disagree: {key}")
    return exact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    root = args.root
    frozen = read_json(root / "manifest.json")
    source_receipt_path = root / "source-receipt.json"
    source_receipt = read_json(source_receipt_path)
    source_pdf_hashes = {item["source_id"]: item["sha256"] for item in source_receipt["sources"]}
    source_receipt_sha256 = hashlib.sha256(source_receipt_path.read_bytes()).hexdigest()
    summary = read_json(root / "checks/integration-summary.json")
    completion_audit = read_json(root / "checks/completion-audit.json")
    source_validation = read_json(root / "checks/source-receipt-validation.json")
    checks = read_jsonl(root / "checks/structure-validation.jsonl")
    continuation_checks = read_jsonl(root / "checks/continuation-validation.jsonl")
    recovery_checks = read_jsonl(root / "checks/content-recovery-validation.jsonl")
    repairs = read_jsonl(root / "structure/deterministic-repairs.jsonl")
    continuations = read_jsonl(root / "structure/continuation-work-packages.jsonl")
    recovery_packages = read_jsonl(root / "structure/content-recovery-work-packages.jsonl")
    dispositions = read_jsonl(root / "integration/entry-dispositions.jsonl")
    embedding = read_json(root / "integration/embedding-ready-candidate-manifest.json")

    pages: dict[tuple[str, int], dict] = {}
    entries: dict[str, dict] = {}
    for shard in frozen["shards"]:
        shard_root = root / "shards" / shard["shard_id"] / "inputs"
        for page in read_jsonl(shard_root / "pages.jsonl"):
            key = (page["source_id"], page["pdf_page"])
            if key in pages or sha256_text(page["text"]) != page["text_sha256"]:
                fail(f"duplicate or hash-drifted frozen page: {key}")
            pages[key] = page
        for entry in read_jsonl(shard_root / "entries.jsonl"):
            if entry["entry_id"] in entries:
                fail(f"duplicate frozen entry: {entry['entry_id']}")
            entries[entry["entry_id"]] = entry

    if len(pages) != 1774 or len(entries) != 265:
        fail(f"frozen scope mismatch pages={len(pages)} entries={len(entries)}")
    if set(source_pdf_hashes) != {key[0] for key in pages} or any(len(value) != 64 for value in source_pdf_hashes.values()):
        fail("source receipt does not cover all frozen source IDs")
    if len(dispositions) != 265 or {item["entry_id"] for item in dispositions} != set(entries):
        fail("entry dispositions are not one-to-one with 265 detected entries")
    if any(item.get("name_resolution_status") != "unresolved" for item in dispositions):
        fail("a disposition invented or resolved a Taiwan name")
    if any(item.get("layout_or_plate_claims_approved") is not False for item in dispositions):
        fail("a disposition approved a layout or plate claim")
    for item in dispositions:
        verify_object_hash(item, "disposition_sha256", f"disposition:{item['entry_id']}")
        entry = entries[item["entry_id"]]
        source_locators = item.get("source_locators", [])
        if [locator.get("pdf_page") for locator in source_locators] != entry["pdf_pages"]:
            fail(f"disposition entry-page coverage mismatch: {item['entry_id']}")
        for locator in source_locators:
            verify_locator(locator, pages, source_pdf_hashes, f"disposition:{item['entry_id']}")
            page = pages[(locator["source_id"], locator["pdf_page"])]
            if locator.get("char_start") != 0 or locator.get("char_end") != len(page["text"]):
                fail(f"disposition locator does not cover its full frozen page: {item['entry_id']}/p{locator['pdf_page']}")

    input_counts = Counter(item["input_disposition"] for item in dispositions)
    expected_special = {
        "eligible_local_structure": 231,
        "already_approved_overlap": 8,
        "hold_span_over_limit": 18,
        "hold_terminal_no_next_heading": 4,
        "hold_page_quality": 4,
    }
    if dict(input_counts) != expected_special:
        fail(f"input disposition counts drifted: {dict(input_counts)}")
    terminal_special = [
        item for item in dispositions
        if item["input_disposition"] == "already_approved_overlap"
    ]
    if any(item.get("terminal") is not True for item in terminal_special):
        fail("a fixed terminal special disposition is not terminal")
    overlaps = [item for item in dispositions if item["input_disposition"] == "already_approved_overlap"]
    if any(not item.get("approved_record_refs") for item in overlaps):
        fail("approved overlap lacks a traceable approved record reference")

    continuation_by_parent: dict[str, list[dict]] = defaultdict(list)
    continuation_packages_by_id: dict[str, dict] = {}
    package_ids = set()
    work_ids = set()
    for package in continuations:
        verify_object_hash(package, "package_sha256", f"continuation:{package.get('package_id')}")
        if package["package_id"] in package_ids or package.get("work_id") in work_ids or not 1 <= package["page_count"] <= 6:
            fail(f"duplicate or oversized continuation: {package['package_id']}")
        package_ids.add(package["package_id"])
        continuation_packages_by_id[package["package_id"]] = package
        work_ids.add(package["work_id"])
        if package.get("stage") != "local_structure_continuation" or package.get("dependencies") != ["primary_local_structure_batch_complete"]:
            fail(f"invalid continuation execution contract: {package['package_id']}")
        required_forbidden = {
            "canonical_record_write", "canonical_chunk_write", "embedding_index_write",
            "source_pdf_write", "external_api", "taiwan_name_invention",
            "layout_or_plate_self_approval",
        }
        if not required_forbidden.issubset(set(package.get("forbidden", []))) or len(package.get("proof_of_done", [])) < 4:
            fail(f"unsafe or incomplete continuation work contract: {package['package_id']}")
        if package.get("name_resolution_status") != "unresolved" or package.get("layout_or_plate_claims_approved") is not False:
            fail(f"unsafe continuation metadata: {package['package_id']}")
        for locator in package["source_locators"]:
            verify_locator(locator, pages, source_pdf_hashes, f"continuation:{package['package_id']}")
        continuation_by_parent[package["parent_entry_id"]].append(package)
    held_long = {entry_id: entry for entry_id, entry in entries.items() if entry["disposition"] == "hold_span_over_limit"}
    if set(continuation_by_parent) != set(held_long) or len(continuations) != 41:
        fail("continuation packages do not cover all 18 over-limit parents")
    for entry_id, entry in held_long.items():
        packages = sorted(continuation_by_parent[entry_id], key=lambda item: item["sequence"])
        if [page for package in packages for page in package["pdf_pages"]] != entry["pdf_pages"]:
            fail(f"continuation page coverage mismatch: {entry_id}")
        if any(package["sequence_count"] != len(packages) for package in packages):
            fail(f"continuation sequence count mismatch: {entry_id}")

    if len({item.get("package_id") for item in continuation_checks}) != len(continuation_checks):
        fail("duplicate continuation validation checks")
    continuation_checks_by_package = {item["package_id"]: item for item in continuation_checks}
    continuation_receipts: dict[str, dict] = {}
    for check in continuation_checks:
        package_id = check.get("package_id")
        package = continuation_packages_by_id.get(package_id)
        if package is None:
            fail(f"continuation check references unknown package: {package_id}")
        verify_object_hash(check, "check_sha256", f"continuation-check:{package_id}")
        if check.get("parent_entry_id") != package["parent_entry_id"] or check.get("package_sha256") != package["package_sha256"]:
            fail(f"continuation check identity drift: {package_id}")
        receipt_path = root / check["receipt_path"]
        if not receipt_path.is_file():
            fail(f"continuation receipt missing: {package_id}")
        receipt = read_json(receipt_path)
        verify_object_hash(receipt, "receipt_sha256", f"continuation-receipt:{package_id}")
        if receipt.get("receipt_sha256") != check.get("receipt_sha256"):
            fail(f"continuation receipt/check chain mismatch: {package_id}")
        if any(receipt.get(field) != package.get(field) for field in ("package_id", "parent_entry_id", "work_id", "owner_shard", "package_sha256")):
            fail(f"continuation receipt/package identity drift: {package_id}")
        if receipt.get("external_model_calls") != 0 or receipt.get("incremental_usd") != 0:
            fail(f"continuation receipt is not local/free: {package_id}")
        if receipt.get("name_resolution_status") != "unresolved" or receipt.get("layout_or_plate_claims_approved") is not False:
            fail(f"unsafe continuation receipt metadata: {package_id}")
        draft = receipt.get("draft", {})
        if draft.get("display_name") is not None or draft.get("name_resolution") != {"status": "unresolved", "sources": []}:
            fail(f"continuation receipt invented a name: {package_id}")
        if draft.get("review_status") != "machine_extracted" or not isinstance(draft.get("sections"), list):
            fail(f"continuation receipt escaped machine review: {package_id}")
        if check.get("section_source_locators") != receipt.get("section_source_locators"):
            fail(f"continuation receipt locator projection drift: {package_id}")
        locators_by_index = {item.get("section_index"): item for item in check["section_source_locators"]}
        if len(locators_by_index) != len(check["section_source_locators"]):
            fail(f"duplicate continuation section locator: {package_id}")
        for section_index, section in enumerate(draft["sections"]):
            locator = locators_by_index.get(section_index)
            if locator is None:
                fail(f"continuation section lacks locator: {package_id}/s{section_index}")
            if locator.get("pdf_page") not in package["pdf_pages"] or locator.get("section_type") != section.get("section_type"):
                fail(f"continuation section/package drift: {package_id}/s{section_index}")
            quote = verify_locator(locator, pages, source_pdf_hashes, f"continuation-check:{package_id}/s{section_index}")
            if section.get("exact_source_quote") != quote:
                fail(f"continuation exact quote drift: {package_id}/s{section_index}")
        expected_pass = receipt.get("deterministic_status") == "pass" and receipt.get("errors") == []
        if (check.get("status") == "pass") != expected_pass:
            fail(f"continuation check status drift: {package_id}")
        continuation_receipts[package_id] = receipt

    if len(recovery_packages) != 9 or len({item.get("package_id") for item in recovery_packages}) != 9:
        fail("expected nine unique content recovery packages")
    recovery_packages_by_id = {item["package_id"]: item for item in recovery_packages}
    recovery_by_parent: dict[str, list[dict]] = defaultdict(list)
    for package in recovery_packages:
        verify_object_hash(package, "package_sha256", f"recovery-package:{package['package_id']}")
        if package.get("stage") != "local_structure_recovery" or package.get("dependencies") != ["primary_local_structure_batch_complete"]:
            fail(f"invalid recovery execution contract: {package['package_id']}")
        if not 1 <= package.get("page_count", 0) <= 6 or package.get("page_count") != len(package.get("pdf_pages", [])):
            fail(f"invalid recovery page count: {package['package_id']}")
        if package.get("name_resolution_status") != "unresolved" or package.get("layout_or_plate_claims_approved") is not False:
            fail(f"unsafe recovery package metadata: {package['package_id']}")
        required_forbidden = {
            "canonical_record_write", "canonical_chunk_write", "embedding_index_write",
            "source_pdf_write", "external_api", "taiwan_name_invention",
            "layout_or_plate_self_approval",
        }
        if not required_forbidden.issubset(set(package.get("forbidden", []))):
            fail(f"unsafe recovery package scope: {package['package_id']}")
        if [locator.get("pdf_page") for locator in package["source_locators"]] != package["pdf_pages"]:
            fail(f"recovery source page coverage mismatch: {package['package_id']}")
        for locator in package["source_locators"]:
            verify_locator(locator, pages, source_pdf_hashes, f"recovery-package:{package['package_id']}")
        if package["recovery_kind"] == "page_quality_with_terminal_no_text_exclusions":
            if not package.get("ocr_exclusions"):
                fail(f"quality recovery lacks OCR exclusions: {package['package_id']}")
            for exclusion in package["ocr_exclusions"]:
                verify_locator(exclusion["source_locator"], pages, source_pdf_hashes, f"ocr-exclusion:{package['package_id']}")
                artifact = root / exclusion["ocr_artifact"]
                receipt = read_json(artifact)
                material = {key: item for key, item in receipt.items() if key != "receipt_sha256"}
                legacy_hash = hashlib.sha256(
                    json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                if (
                    receipt.get("receipt_sha256") != legacy_hash
                    or receipt.get("receipt_sha256") != exclusion.get("ocr_receipt_sha256")
                    or receipt.get("text") != ""
                    or receipt.get("staging_disposition") != "no_text_detected"
                ):
                    fail(f"OCR no-text exclusion receipt drift: {package['package_id']}")
        elif package["recovery_kind"] == "terminal_body_boundary_split":
            boundary = package.get("terminal_boundary", {})
            marker = boundary.get("boundary_marker")
            locator = boundary.get("boundary_source_locator")
            if verify_locator(locator, pages, source_pdf_hashes, f"terminal-boundary:{package['package_id']}") != marker:
                fail(f"terminal boundary marker drift: {package['package_id']}")
            if boundary.get("body_end_pdf_page") >= boundary.get("trailing_plate_start_pdf_page"):
                fail(f"terminal body/plate boundary is not ordered: {package['package_id']}")
        else:
            fail(f"unknown recovery kind: {package['package_id']}")
        recovery_by_parent[package["parent_entry_id"]].append(package)
    expected_recovery_parents = {
        entry_id for entry_id, entry in entries.items()
        if entry["disposition"] in {"hold_page_quality", "hold_terminal_no_next_heading"}
    }
    if set(recovery_by_parent) != expected_recovery_parents:
        fail("content recovery packages do not cover all eight content-hold parents")

    if len({item.get("package_id") for item in recovery_checks}) != len(recovery_checks):
        fail("duplicate content recovery validation checks")
    recovery_checks_by_package = {item["package_id"]: item for item in recovery_checks}
    recovery_receipts = {}
    for check in recovery_checks:
        package_id = check.get("package_id")
        package = recovery_packages_by_id.get(package_id)
        if package is None:
            fail(f"recovery check references unknown package: {package_id}")
        verify_object_hash(check, "check_sha256", f"recovery-check:{package_id}")
        if check.get("parent_entry_id") != package["parent_entry_id"] or check.get("recovered_entry_id") != package["recovered_entry_id"]:
            fail(f"recovery check identity drift: {package_id}")
        receipt_path = root / check["receipt_path"]
        receipt = read_json(receipt_path)
        verify_object_hash(receipt, "receipt_sha256", f"recovery-receipt:{package_id}")
        if receipt.get("receipt_sha256") != check.get("receipt_sha256") or receipt.get("package_sha256") != package["package_sha256"]:
            fail(f"recovery receipt chain drift: {package_id}")
        if receipt.get("recovered_entry_id") != package["recovered_entry_id"]:
            fail(f"recovery receipt target identity drift: {package_id}")
        if receipt.get("name_resolution_status") != "unresolved" or receipt.get("layout_or_plate_claims_approved") is not False:
            fail(f"unsafe recovery receipt metadata: {package_id}")
        draft = receipt.get("draft", {})
        if draft.get("display_name") is not None or draft.get("name_resolution") != {"status": "unresolved", "sources": []}:
            fail(f"recovery receipt invented a name: {package_id}")
        if draft.get("review_status") != "machine_extracted":
            fail(f"recovery receipt escaped machine review: {package_id}")
        if draft.get("book_taxon", {}).get("scientific_name_candidate") != package["book_taxon_candidate"]:
            fail(f"recovery receipt taxon identity drift: {package_id}")
        if check.get("section_source_locators") != receipt.get("section_source_locators"):
            fail(f"recovery locator projection drift: {package_id}")
        for section_index, section in enumerate(draft.get("sections", [])):
            locator = next(
                (item for item in check["section_source_locators"] if item.get("section_index") == section_index), None
            )
            if locator is None or locator.get("pdf_page") not in package["pdf_pages"]:
                fail(f"recovery section lacks in-package locator: {package_id}/s{section_index}")
            if verify_locator(locator, pages, source_pdf_hashes, f"recovery-check:{package_id}/s{section_index}") != section.get("exact_source_quote"):
                fail(f"recovery exact quote drift: {package_id}/s{section_index}")
        expected_pass = receipt.get("deterministic_status") == "pass" and receipt.get("errors") == []
        if (check.get("status") == "pass") != expected_pass:
            fail(f"recovery check status drift: {package_id}")
        recovery_receipts[package_id] = receipt

    repairs_by_id = {item.get("repair_id"): item for item in repairs}
    required_repair_ids = {"palaquium-history-cross-page-v1", "inula-cross-page-sections-v1"}
    if len(repairs_by_id) != len(repairs) or not required_repair_ids.issubset(repairs_by_id):
        fail("expected Palaquium and Inula changed-strategy repairs")
    for repair_id, candidate_repair in repairs_by_id.items():
        verify_object_hash(candidate_repair, "repair_sha256", f"changed-strategy repair:{repair_id}")
        if candidate_repair.get("status") != "deterministic_pass_changed_strategy":
            fail(f"repair is not a changed-strategy deterministic pass: {repair_id}")
        if candidate_repair.get("layout_or_plate_claims_approved") is not False:
            fail(f"repair approved a layout or plate claim: {repair_id}")
        if candidate_repair.get("name_resolution_status") != "unresolved":
            fail(f"repair escaped unresolved naming scope: {repair_id}")
        if not candidate_repair.get("source_locators"):
            fail(f"repair lacks source locators: {repair_id}")
        for locator in candidate_repair["source_locators"]:
            verify_locator(locator, pages, source_pdf_hashes, f"changed-strategy repair:{repair_id}")
    repair = repairs_by_id["palaquium-history-cross-page-v1"]
    verify_object_hash(repair, "repair_sha256", "Palaquium repair")
    repaired_quotes = [verify_locator(locator, pages, source_pdf_hashes, "Palaquium repair") for locator in repair["source_locators"]]
    if len(repaired_quotes) != 2 or not repaired_quotes[0].startswith("         Name und Geschichtliches."):
        fail("Palaquium repair did not preserve the intended cross-page history source")
    if repair.get("status") != "deterministic_pass_changed_strategy":
        fail("Palaquium repair is not a changed-strategy deterministic pass")
    inula = repairs_by_id["inula-cross-page-sections-v1"]
    verify_object_hash(inula, "repair_sha256", "Inula repair")
    inula_quotes = [verify_locator(locator, pages, source_pdf_hashes, "Inula repair") for locator in inula["source_locators"]]
    if len(inula_quotes) != 4 or not inula_quotes[0].lstrip().startswith("Anatomisches.") or not inula_quotes[2].lstrip().startswith("Bestandtheile."):
        fail("Inula repair did not preserve both intended cross-page sections")
    if inula.get("status") != "deterministic_pass_changed_strategy":
        fail("Inula repair is not a changed-strategy deterministic pass")

    if len({item["entry_id"] for item in checks}) != len(checks):
        fail("duplicate structure validation checks")
    checks_by_entry = {item["entry_id"]: item for item in checks}
    for check in checks:
        verify_object_hash(check, "check_sha256", f"check:{check['entry_id']}")
        for locator in check["section_source_locators"]:
            quote = verify_locator(locator, pages, source_pdf_hashes, f"check:{check['entry_id']}")
            if "exact_source_quote" in locator and locator["exact_source_quote"] != quote:
                fail(f"embedded exact quote mismatch in check: {check['entry_id']}")

    if embedding.get("canonical_write_allowed") is not False or embedding.get("embedding_calls_performed") is not False:
        fail("embedding-ready manifest violated staging-only scope")
    if embedding.get("vector_space_id") is not None:
        fail("pre-embedding manifest must not declare a vector space")
    if embedding.get("source_receipt_sha256") != source_receipt_sha256 or embedding.get("source_pdf_hashes") != dict(sorted(source_pdf_hashes.items())):
        fail("embedding-ready manifest source receipt chain mismatch")
    if embedding.get("candidate_count") != len(embedding.get("candidates", [])):
        fail("embedding-ready candidate count mismatch")
    verify_object_hash(embedding, "manifest_sha256", "embedding-ready manifest")
    verify_object_hash(source_validation, "check_sha256", "source receipt validation")
    if source_validation.get("source_receipt_sha256") != source_receipt_sha256:
        fail("source receipt validation chain mismatch")
    if source_validation.get("file_count") != 4 or len(source_validation.get("files", [])) != 4:
        fail("source receipt validation does not cover four PDFs")
    for item in source_validation["files"]:
        source_id = item.get("source_id")
        if item.get("expected_sha256") != source_pdf_hashes.get(source_id):
            fail(f"source receipt validation expected hash drift: {source_id}")
        if item.get("bytes_match") is not True or item.get("exists") is not True:
            fail(f"source receipt validation metadata failed: {source_id}")
        if source_validation.get("full_hash_verified"):
            if item.get("sha256_matches") is not True or item.get("observed_sha256") != item.get("expected_sha256"):
                fail(f"source receipt full hash evidence drift: {source_id}")
    candidate_ids = set()
    disposition_hashes = {item["entry_id"]: item["disposition_sha256"] for item in dispositions}
    dispositions_by_entry = {item["entry_id"]: item for item in dispositions}
    for candidate in embedding["candidates"]:
        verify_object_hash(candidate, "candidate_sha256", f"candidate:{candidate.get('entry_id')}")
        source_parent_entry_id = candidate.get("source_parent_entry_id", candidate["entry_id"])
        if candidate["entry_id"] in candidate_ids or source_parent_entry_id not in entries:
            fail(f"duplicate or unknown embedding candidate: {candidate['entry_id']}")
        candidate_ids.add(candidate["entry_id"])
        if candidate.get("source_disposition_sha256") != disposition_hashes[source_parent_entry_id]:
            fail(f"candidate disposition chain mismatch: {candidate['entry_id']}")
        if candidate.get("display_name") is not None or candidate.get("name_resolution") != {"status": "unresolved", "sources": []}:
            fail(f"candidate invented a Taiwan name: {candidate['entry_id']}")
        if candidate.get("review_status") != "machine_extracted" or candidate.get("layout_or_plate_claims_approved") is not False:
            fail(f"candidate escaped machine-only review: {candidate['entry_id']}")
        if any(section["section_type"] == "plate_description" for section in candidate["sections"]):
            fail(f"plate section entered embedding-ready text candidate: {candidate['entry_id']}")

        frozen_entry = entries[source_parent_entry_id]
        if candidate.get("recovery_package"):
            recovery = candidate["recovery_package"]
            package = recovery_packages_by_id.get(recovery.get("package_id"))
            if package is None or package.get("parent_entry_id") != source_parent_entry_id or package.get("recovered_entry_id") != candidate["entry_id"]:
                fail(f"recovery candidate/package identity drift: {candidate['entry_id']}")
            check = recovery_checks_by_package.get(package["package_id"])
            receipt = recovery_receipts.get(package["package_id"])
            if check is None or receipt is None or check.get("status") != "pass":
                fail(f"recovery candidate lacks a deterministic pass chain: {candidate['entry_id']}")
            if candidate.get("maker_receipt_sha256") != receipt["receipt_sha256"] or candidate.get("validation_check_sha256") != check["check_sha256"]:
                fail(f"recovery candidate receipt/check chain drift: {candidate['entry_id']}")
            expected_sections = []
            locators_by_section = {
                locator["section_index"]: locator for locator in check["section_source_locators"]
            }
            excluded_plate_count = 0
            for section_index, section in enumerate(receipt["draft"]["sections"]):
                locator = locators_by_section.get(section_index)
                if locator is None:
                    continue
                if section["section_type"] == "plate_description":
                    excluded_plate_count += 1
                    continue
                quote = verify_locator(locator, pages, source_pdf_hashes, f"recovery-candidate:{candidate['entry_id']}/s{section_index}")
                expected_sections.append({
                    "section_type": section["section_type"],
                    "exact_source_quotes": [quote],
                    "normalized_text_candidate": section.get("normalized_text_candidate"),
                    "zh_tw_rendering_candidate": section.get("zh_tw_rendering_candidate"),
                    "source_locators": [locator],
                    "recovery_provenance": [{
                        "package_id": package["package_id"],
                        "package_sha256": package["package_sha256"],
                        "receipt_sha256": receipt["receipt_sha256"],
                        "validation_check_sha256": check["check_sha256"],
                        "package_section_index": section_index,
                        "recovery_kind": package["recovery_kind"],
                    }],
                })
            if candidate.get("sections") != expected_sections or candidate.get("excluded_plate_section_count") != excluded_plate_count:
                fail(f"recovery candidate sections drift: {candidate['entry_id']}")
            continue
        if frozen_entry["disposition"] == "hold_span_over_limit":
            packages = sorted(continuation_by_parent[candidate["entry_id"]], key=lambda item: item["sequence"])
            if any(
                package["package_id"] not in continuation_checks_by_package
                or continuation_checks_by_package[package["package_id"]].get("status") != "pass"
                for package in packages
            ):
                fail(f"continuation candidate lacks a complete pass chain: {candidate['entry_id']}")
            expected_provenance = []
            expected_sections = []
            expected_by_key: dict[tuple, dict] = {}
            excluded_plate_count = 0
            for package in packages:
                package_id = package["package_id"]
                check = continuation_checks_by_package[package_id]
                receipt = continuation_receipts[package_id]
                expected_provenance.append({
                    "package_id": package_id,
                    "package_sha256": package["package_sha256"],
                    "receipt_sha256": receipt["receipt_sha256"],
                    "validation_check_sha256": check["check_sha256"],
                })
                locators_by_section = {
                    locator["section_index"]: locator for locator in check["section_source_locators"]
                }
                for section_index, section in enumerate(receipt["draft"]["sections"]):
                    locator = locators_by_section.get(section_index)
                    if locator is None:
                        continue
                    if section["section_type"] == "plate_description":
                        excluded_plate_count += 1
                        continue
                    quote = verify_locator(
                        locator, pages, source_pdf_hashes,
                        f"continuation-candidate:{candidate['entry_id']}:{package_id}/s{section_index}",
                    )
                    key = (
                        section["section_type"], locator["source_id"], locator["pdf_page"],
                        locator["char_start"], locator["char_end"], locator["exact_text_sha256"],
                    )
                    span_provenance = {
                        "package_id": package_id,
                        "package_sha256": package["package_sha256"],
                        "receipt_sha256": receipt["receipt_sha256"],
                        "validation_check_sha256": check["check_sha256"],
                        "package_section_index": section_index,
                    }
                    existing = expected_by_key.get(key)
                    if existing is not None:
                        if span_provenance not in existing["continuation_provenance"]:
                            existing["continuation_provenance"].append(span_provenance)
                        continue
                    merged = {
                        "section_type": section["section_type"],
                        "exact_source_quotes": [quote],
                        "normalized_text_candidate": section.get("normalized_text_candidate"),
                        "zh_tw_rendering_candidate": section.get("zh_tw_rendering_candidate"),
                        "source_locators": [locator],
                        "continuation_provenance": [span_provenance],
                    }
                    expected_by_key[key] = merged
                    expected_sections.append(merged)
            expected_receipt_chain = sha256_text(canonical_json([
                {"package_id": item["package_id"], "receipt_sha256": item["receipt_sha256"]}
                for item in expected_provenance
            ]))
            expected_check_chain = sha256_text(canonical_json([
                {"package_id": item["package_id"], "validation_check_sha256": item["validation_check_sha256"]}
                for item in expected_provenance
            ]))
            if candidate.get("continuation_receipts") != expected_provenance:
                fail(f"continuation candidate provenance drift: {candidate['entry_id']}")
            if candidate.get("maker_receipt_sha256") != expected_receipt_chain or candidate.get("validation_check_sha256") != expected_check_chain:
                fail(f"continuation candidate aggregate chain drift: {candidate['entry_id']}")
            if candidate.get("sections") != expected_sections or candidate.get("excluded_plate_section_count") != excluded_plate_count:
                fail(f"continuation candidate sections drifted: {candidate['entry_id']}")
            disposition = dispositions_by_entry[candidate["entry_id"]]
            if disposition.get("terminal") is not True or disposition.get("terminal_disposition") != "continuation_structure_validated":
                fail(f"continuation candidate parent is not terminal validated: {candidate['entry_id']}")
            continue

        check = checks_by_entry.get(candidate["entry_id"])
        if check is None or candidate.get("validation_check_sha256") != check["check_sha256"]:
            fail(f"candidate validation-check chain mismatch: {candidate['entry_id']}")
        receipt_path = root / check["receipt_path"]
        receipt = read_json(receipt_path)
        if candidate.get("maker_receipt_sha256") != receipt.get("receipt_sha256"):
            fail(f"candidate maker-receipt chain mismatch: {candidate['entry_id']}")
        expected_by_section: dict[int, list[tuple[str, dict]]] = defaultdict(list)
        for locator in check["section_source_locators"]:
            if locator["section_type"] == "plate_description":
                continue
            clean_locator = dict(locator)
            clean_locator.pop("exact_source_quote", None)
            quote = verify_locator(clean_locator, pages, source_pdf_hashes, f"check-chain:{candidate['entry_id']}")
            expected_by_section[locator["section_index"]].append((quote, clean_locator))
        expected_sections = []
        draft_sections = receipt["draft"]["sections"]
        for section_index in sorted(expected_by_section):
            section = draft_sections[section_index]
            expected_sections.append({
                "section_type": section["section_type"],
                "exact_source_quotes": [item[0] for item in expected_by_section[section_index]],
                "normalized_text_candidate": section.get("normalized_text_candidate"),
                "zh_tw_rendering_candidate": section.get("zh_tw_rendering_candidate"),
                "source_locators": [item[1] for item in expected_by_section[section_index]],
            })
        if candidate["sections"] != expected_sections:
            fail(f"candidate sections drifted from validated maker evidence: {candidate['entry_id']}")
        for section in candidate["sections"]:
            if len(section["exact_source_quotes"]) != len(section["source_locators"]):
                fail(f"quote/locator count mismatch: {candidate['entry_id']}")
            for quote, locator in zip(section["exact_source_quotes"], section["source_locators"]):
                if verify_locator(locator, pages, source_pdf_hashes, f"candidate:{candidate['entry_id']}") != quote:
                    fail(f"candidate quote is not exact source: {candidate['entry_id']}")

    for entry_id in held_long:
        packages = continuation_by_parent[entry_id]
        complete_pass_chain = all(
            package["package_id"] in continuation_checks_by_package
            and continuation_checks_by_package[package["package_id"]].get("status") == "pass"
            for package in packages
        )
        disposition = dispositions_by_entry[entry_id]
        if complete_pass_chain:
            if disposition.get("terminal") is not True or disposition.get("terminal_disposition") != "continuation_structure_validated":
                fail(f"complete continuation parent is not terminal validated: {entry_id}")
            if entry_id not in candidate_ids:
                fail(f"complete continuation parent lacks embedding-ready candidate: {entry_id}")
        else:
            if disposition.get("terminal") is not False or disposition.get("embedding_ready_candidate") is not False:
                fail(f"incomplete continuation parent was prematurely terminal: {entry_id}")
            if entry_id in candidate_ids:
                fail(f"incomplete continuation parent entered embedding-ready candidates: {entry_id}")

    for parent_entry_id, packages in recovery_by_parent.items():
        disposition = dispositions_by_entry[parent_entry_id]
        complete_pass_chain = all(
            package["package_id"] in recovery_checks_by_package
            and recovery_checks_by_package[package["package_id"]].get("status") == "pass"
            for package in packages
        )
        expected_candidate_ids = {package["recovered_entry_id"] for package in packages}
        actual_candidate_ids = expected_candidate_ids & candidate_ids
        if complete_pass_chain:
            expected_terminal = (
                "page_quality_structure_recovered"
                if entries[parent_entry_id]["disposition"] == "hold_page_quality"
                else "terminal_body_boundaries_recovered"
            )
            if disposition.get("terminal") is not True or disposition.get("terminal_disposition") != expected_terminal:
                fail(f"complete content recovery parent is not terminal: {parent_entry_id}")
            if actual_candidate_ids != expected_candidate_ids:
                fail(f"complete content recovery parent lacks recovered candidates: {parent_entry_id}")
        else:
            if disposition.get("terminal") is not False or disposition.get("embedding_ready_candidate") is not False:
                fail(f"incomplete content recovery parent was prematurely terminal: {parent_entry_id}")
            if actual_candidate_ids:
                fail(f"incomplete content recovery parent leaked candidates: {parent_entry_id}")

    terminal_count = sum(bool(item["terminal"]) for item in dispositions)
    if summary.get("detected_entries") != 265 or summary.get("terminal_entries") != terminal_count:
        fail("integration summary counts do not match dispositions")
    if summary.get("source_receipt_sha256") != source_receipt_sha256:
        fail("integration summary source receipt chain mismatch")
    if summary.get("embedding_ready_text_candidates") != len(candidate_ids):
        fail("integration summary candidate count mismatch")
    actual_needs_review = sorted(item["entry_id"] for item in checks if not item["status"].startswith("pass"))
    if summary.get("maker_receipts_needs_review") != len(actual_needs_review) or sorted(summary.get("needs_review_entry_ids", [])) != actual_needs_review:
        fail("integration summary needs-review projection mismatch")
    if summary.get("changed_strategy_repairs") != len(repairs):
        fail("integration summary repair count mismatch")
    actual_continuation_needs_review = sorted(
        item["package_id"] for item in continuation_checks if item.get("status") != "pass"
    )
    expected_continuation_summary = {
        "continuation_receipts_checked": len(continuation_checks),
        "continuation_receipts_passed": sum(item.get("status") == "pass" for item in continuation_checks),
        "continuation_receipts_needs_review": len(actual_continuation_needs_review),
        "continuation_parent_entries_validated": sum(
            item.get("terminal_disposition") == "continuation_structure_validated"
            for item in dispositions
        ),
    }
    for field, expected in expected_continuation_summary.items():
        if summary.get(field) != expected:
            fail(f"integration summary continuation projection mismatch: {field}")
    if sorted(summary.get("continuation_needs_review_package_ids", [])) != actual_continuation_needs_review:
        fail("integration summary continuation needs-review IDs drifted")
    actual_recovery_needs_review = sorted(
        item["package_id"] for item in recovery_checks if item.get("status") != "pass"
    )
    expected_recovery_summary = {
        "content_recovery_packages": len(recovery_packages),
        "content_recovery_receipts_checked": len(recovery_checks),
        "content_recovery_receipts_passed": sum(item.get("status") == "pass" for item in recovery_checks),
        "content_recovery_receipts_needs_review": len(actual_recovery_needs_review),
        "content_hold_parents_recovered": sum(
            item.get("terminal_disposition") in {"page_quality_structure_recovered", "terminal_body_boundaries_recovered"}
            for item in dispositions
        ),
        "unresolved_content_holds": sum(
            item["input_disposition"] in {"hold_page_quality", "hold_terminal_no_next_heading"}
            and not item.get("terminal") for item in dispositions
        ),
    }
    for field, expected in expected_recovery_summary.items():
        if summary.get(field) != expected:
            fail(f"integration summary content recovery projection mismatch: {field}")
    if sorted(summary.get("content_recovery_needs_review_package_ids", [])) != actual_recovery_needs_review:
        fail("integration summary content recovery needs-review IDs drifted")

    verify_object_hash(completion_audit, "audit_sha256", "completion audit")
    requirements = completion_audit.get("requirements", [])
    requirement_ids = [item.get("requirement_id") for item in requirements]
    expected_requirement_ids = [
        "detected-entry-inventory",
        "local-maker-batch",
        "deterministic-maker-validation",
        "span-over-limit-continuations",
        "continuation-receipts-complete",
        "terminal-no-next-heading",
        "page-quality-holds",
        "content-hold-recovery-complete",
        "approved-overlap-dispositions",
        "all-entry-terminal-disposition",
        "exact-source-locators",
        "source-pdf-byte-hashes",
        "embedding-ready-candidate-manifest",
        "safety-boundaries",
    ]
    if requirement_ids != expected_requirement_ids:
        fail("completion audit requirement matrix drifted")
    requirements_by_id = {item["requirement_id"]: item for item in requirements}
    needs_review_count = len(actual_needs_review)
    expected_observed = {
        "detected-entry-inventory": len(dispositions),
        "local-maker-batch": {"receipts": len(checks), "batch_status": read_json(root / "batch-status.json").get("status")},
        "deterministic-maker-validation": {
            "checked": len(checks),
            "needs_review": needs_review_count,
            "entry_ids": actual_needs_review,
        },
        "span-over-limit-continuations": {
            "parents": len(continuation_by_parent),
            "packages": len(continuations),
            "maximum_pages": max((item["page_count"] for item in continuations), default=0),
        },
        "continuation-receipts-complete": {
            "receipts": len(continuation_checks),
            "passed": sum(item.get("status") == "pass" for item in continuation_checks),
            "needs_review": sum(item.get("status") != "pass" for item in continuation_checks),
            "validated_parents": sum(
                item.get("terminal_disposition") == "continuation_structure_validated"
                for item in dispositions
            ),
        },
        "terminal-no-next-heading": input_counts["hold_terminal_no_next_heading"],
        "page-quality-holds": input_counts["hold_page_quality"],
        "content-hold-recovery-complete": {
            "receipts": len(recovery_checks),
            "passed": sum(item.get("status") == "pass" for item in recovery_checks),
            "needs_review": sum(item.get("status") != "pass" for item in recovery_checks),
            "recovered_parents": sum(
                item.get("terminal_disposition") in {"page_quality_structure_recovered", "terminal_body_boundaries_recovered"}
                for item in dispositions
            ),
            "unresolved_content_holds": sum(
                item["input_disposition"] in {"hold_page_quality", "hold_terminal_no_next_heading"}
                and not item.get("terminal") for item in dispositions
            ),
        },
        "approved-overlap-dispositions": input_counts["already_approved_overlap"],
        "all-entry-terminal-disposition": terminal_count,
        "exact-source-locators": len(dispositions),
        "source-pdf-byte-hashes": {
            "files": source_validation.get("file_count"),
            "mode": source_validation.get("mode"),
            "full_hash_verified": source_validation.get("full_hash_verified"),
            "errors": source_validation.get("errors"),
        },
        "embedding-ready-candidate-manifest": {
            "generated": True,
            "status": embedding.get("status"),
            "candidate_count": embedding.get("candidate_count"),
            "candidate_count_matches": embedding.get("candidate_count") == len(embedding.get("candidates", [])),
        },
        "safety-boundaries": True,
    }
    for requirement_id, observed in expected_observed.items():
        if requirements_by_id[requirement_id].get("observed") != observed:
            fail(f"completion audit observed value drifted: {requirement_id}")
    achieved_count = sum(item.get("status") == "achieved" for item in requirements)
    if completion_audit.get("achieved_requirements") != achieved_count or completion_audit.get("total_requirements") != len(requirements):
        fail("completion audit requirement counts drifted")
    audit_complete = all(item.get("status") == "achieved" for item in requirements)
    if completion_audit.get("overall_complete") != audit_complete or completion_audit.get("status") != ("complete" if audit_complete else "in_progress"):
        fail("completion audit aggregate status drifted")
    if audit_complete != bool(summary.get("complete")):
        fail("completion audit and integration summary completion disagree")
    if summary.get("complete") and source_validation.get("full_hash_verified") is not True:
        fail("complete integration lacks current source-PDF SHA-256 verification")
    if args.require_complete and not summary.get("complete"):
        fail("integration is not complete")
    verify_object_hash(summary, "summary_sha256", "integration summary")

    print(json.dumps({
        "status": "PASS",
        "detected_entries": len(entries),
        "terminal_entries": terminal_count,
        "nonterminal_entries": len(entries) - terminal_count,
        "maker_receipts_checked": len(checks),
        "maker_receipts_needs_review": len(actual_needs_review),
        "needs_review_entry_ids": actual_needs_review,
        "changed_strategy_repairs": len(repairs),
        "continuation_packages": len(continuations),
        "embedding_ready_text_candidates": len(candidate_ids),
        "completion_requirements_achieved": achieved_count,
        "completion_requirements_total": len(requirements),
        "complete": summary["complete"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
