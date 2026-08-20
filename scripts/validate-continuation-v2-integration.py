#!/usr/bin/env python3
"""Independently validate continuation-v2 staging integration artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
SECTION_TYPES = {
    "taxonomy", "description", "anatomy", "distribution", "history",
    "flowering", "harvest", "constituents", "historical_use", "literature",
    "plate_description", "other",
}


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


def extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("missing JSON object")
    result = json.loads(cleaned[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("JSON is not an object")
    return result


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    root = args.root
    errors: list[str] = []
    plan = read_json(root / "boundary-evidence-v1/boundary-overlay-plan.json")
    package_manifest = read_json(root / "structure/continuation-work-packages-v2-manifest.json")
    packages = read_jsonl(root / "structure/continuation-work-packages-v2.jsonl")
    checks = read_jsonl(root / "checks/continuation-v2-integration-validation.jsonl")
    summary = read_json(root / "checks/continuation-v2-integration-summary.json")
    dispositions = read_jsonl(root / "integration-v2/child-dispositions.jsonl")
    embedding = read_json(root / "integration-v2/embedding-ready-child-candidate-manifest.json")
    source_receipt_path = root / "source-receipt.json"
    source_receipt = read_json(source_receipt_path)
    source_hashes = {item["source_id"]: item["sha256"] for item in source_receipt["sources"]}
    if len(source_hashes) != 4 or len(source_hashes) != len(source_receipt.get("sources", [])):
        fail(errors, "source receipt cardinality or uniqueness drift")
    pages = {}
    for path in sorted((root / "shards").glob("S*/inputs/pages.jsonl")):
        for page in read_jsonl(path):
            key = (page["source_id"], page["pdf_page"])
            if key in pages:
                fail(errors, f"duplicate frozen page:{key}")
            pages[key] = page

    if plan.get("plan_sha256") != object_hash(plan, "plan_sha256"):
        fail(errors, "boundary overlay plan hash mismatch")
    if plan.get("summary", {}).get("parents") != 18 or plan.get("summary", {}).get("child_segments") != 34:
        fail(errors, "boundary overlay count mismatch")
    if any(value is not False for value in plan.get("safety", {}).values()):
        fail(errors, "boundary overlay safety escaped")
    parents = {}
    segments = {}
    segment_parent = {}
    for parent in plan.get("parents", []):
        parent_id = parent.get("parent_entry_id")
        if parent_id in parents or parent.get("parent_plan_sha256") != object_hash(parent, "parent_plan_sha256"):
            fail(errors, f"duplicate or hash-invalid parent:{parent_id}")
        parents[parent_id] = parent
        expected_pages = list(range(parent["start_pdf_page"], parent["end_pdf_page"] + 1))
        covered = []
        if parent.get("safe_to_replace_old_continuation") is not True or parent.get("review_candidates") != []:
            fail(errors, f"unsafe boundary parent:{parent_id}")
        if parent.get("canonical_write_allowed") is not False:
            fail(errors, f"parent canonical write escaped:{parent_id}")
        for segment in parent.get("segments", []):
            child_id = segment.get("child_entry_id")
            if child_id in segments or segment.get("segment_sha256") != object_hash(segment, "segment_sha256"):
                fail(errors, f"duplicate or hash-invalid segment:{child_id}")
            segments[child_id] = segment
            segment_parent[child_id] = parent_id
            if segment.get("pdf_pages") != list(range(segment["start_pdf_page"], segment["end_pdf_page"] + 1)):
                fail(errors, f"noncontiguous child segment:{child_id}")
            covered.extend(segment["pdf_pages"])
        if covered != expected_pages or len(covered) != len(set(covered)) or parent.get("exact_page_coverage") is not True:
            fail(errors, f"parent boundary coverage drift:{parent_id}")
    if len(parents) != 18 or len(segments) != 34:
        fail(errors, "boundary overlay cardinality drift")

    if package_manifest.get("manifest_sha256") != object_hash(package_manifest, "manifest_sha256"):
        fail(errors, "package manifest hash mismatch")
    if (
        package_manifest.get("boundary_overlay_plan_sha256") != plan.get("plan_sha256")
        or package_manifest.get("package_count") != 46
        or package_manifest.get("child_segment_count") != 34
        or package_manifest.get("parent_count") != 18
        or package_manifest.get("maximum_pages_per_package") != 6
        or package_manifest.get("old_packages_modified") is not False
        or package_manifest.get("canonical_writes") is not False
    ):
        fail(errors, "package manifest contract drift")
    package_by_id = {item.get("package_id"): item for item in packages}
    if len(packages) != 46 or len(package_by_id) != 46:
        fail(errors, "package cardinality or uniqueness drift")
    by_child: dict[str, list[dict]] = defaultdict(list)
    package_valid: dict[str, bool] = {}
    for package in packages:
        package_id = package["package_id"]
        valid = True
        segment = segments.get(package.get("child_entry_id"))
        expected_identity = (
            package.get("schema_version") == "2.0"
            and package.get("stage") == "local_structure_continuation_v2"
            and package.get("package_sha256") == object_hash(package, "package_sha256")
            and segment is not None
            and package.get("parent_entry_id") == segment_parent.get(package.get("child_entry_id"))
            and package.get("boundary_overlay_plan_sha256") == plan.get("plan_sha256")
            and package.get("boundary_segment_sha256") == (segment or {}).get("segment_sha256")
            and package.get("book_taxon_candidate") == (segment or {}).get("taxon_candidate")
            and package.get("review_status") == "candidate"
            and package.get("name_resolution_status") == "unresolved"
            and package.get("layout_or_plate_claims_approved") is False
        )
        if not expected_identity:
            valid = False
        if package.get("page_count") != len(package.get("pdf_pages", [])) or not 1 <= package.get("page_count", 0) <= 6:
            valid = False
        if segment and any(page not in segment["pdf_pages"] for page in package["pdf_pages"]):
            valid = False
        locators = package.get("source_locators", [])
        if [item.get("pdf_page") for item in locators] != package.get("pdf_pages"):
            valid = False
        for locator in locators:
            page = pages.get((package.get("source_id"), locator.get("pdf_page")))
            if page is None or not (
                locator.get("source_id") == package.get("source_id")
                and locator.get("volume") == package.get("volume")
                and locator.get("source_pdf_sha256") == source_hashes.get(package.get("source_id"))
                and locator.get("char_start") == 0 and locator.get("char_end") == len(page["text"])
                and locator.get("page_text_sha256") == page["text_sha256"]
                and locator.get("exact_text_sha256") == text_hash(page["text"])
            ):
                valid = False
        if not valid:
            fail(errors, f"invalid package/source/boundary chain:{package_id}")
        package_valid[package_id] = valid
        by_child[package.get("child_entry_id")].append(package)
    if set(by_child) != set(segments):
        fail(errors, "package child set drift")
    for child_id, segment in segments.items():
        ordered = sorted(by_child.get(child_id, []), key=lambda item: item.get("sequence", 0))
        covered = [page for item in ordered for page in item.get("pdf_pages", [])]
        if (
            covered != segment["pdf_pages"] or len(covered) != len(set(covered))
            or [item.get("sequence") for item in ordered] != list(range(1, len(ordered) + 1))
            or any(item.get("sequence_count") != len(ordered) for item in ordered)
        ):
            fail(errors, f"child package coverage/sequence drift:{child_id}")

    check_by_package = {item.get("package_id"): item for item in checks}
    if len(checks) != 46 or len(check_by_package) != 46 or set(check_by_package) != set(package_by_id):
        fail(errors, "validation check set drift")
    recomputed_status = {}
    receipts = {}
    for package_id, package in package_by_id.items():
        check = check_by_package.get(package_id, {})
        if check.get("check_sha256") != object_hash(check, "check_sha256"):
            fail(errors, f"check hash mismatch:{package_id}")
        if (
            check.get("parent_entry_id") != package["parent_entry_id"]
            or check.get("child_entry_id") != package["child_entry_id"]
            or check.get("package_sha256") != package["package_sha256"]
        ):
            fail(errors, f"check identity drift:{package_id}")
        receipt_path = root / "structure/continuation-v2-maker-receipts" / f"{package_id.replace(':', '__')}.json"
        if not receipt_path.is_file():
            recomputed_status[package_id] = "awaiting_receipt"
            if check.get("status") != "awaiting_receipt" or check.get("receipt_path") is not None:
                fail(errors, f"missing receipt projected incorrectly:{package_id}")
            continue
        receipt = read_json(receipt_path)
        receipts[package_id] = receipt
        valid = package_valid[package_id]
        if receipt.get("receipt_sha256") != object_hash(receipt, "receipt_sha256"):
            valid = False
        expected_receipt = {
            "schema_version": "2.0", "package_id": package_id,
            "parent_entry_id": package["parent_entry_id"], "child_entry_id": package["child_entry_id"],
            "recovered_entry_id": None, "work_id": package["work_id"],
            "owner_shard": package["owner_shard"], "package_sha256": package["package_sha256"],
            "prompt_version": "plant-structure-line-coordinates-v2",
            "external_model_calls": 0, "incremental_usd": 0,
            "name_resolution_status": "unresolved", "layout_or_plate_claims_approved": False,
            "deterministic_status": "pass", "errors": [],
        }
        if any(receipt.get(field) != expected for field, expected in expected_receipt.items()):
            valid = False
        repair = receipt.get("changed_strategy_repair")
        dropped_indexes: set[int] = set()
        repair_projection = None
        if repair is not None:
            if not isinstance(repair, dict):
                valid = False
                repair = {}
            raw_dropped = repair.get("dropped_section_indexes")
            source_errors = repair.get("source_errors")
            if (
                repair.get("strategy") != "drop-unmaterialized-sections-v1"
                or repair.get("external_model_calls") != 0
                or repair.get("content_added") is not False
                or repair.get("line_numbers_guessed_or_clamped") is not False
                or receipt.get("model") != "deterministic-local-repair-no-model-call"
                or receipt.get("elapsed_seconds") != 0.0
                or not isinstance(raw_dropped, list) or not raw_dropped
                or not all(isinstance(value, int) and value >= 0 for value in raw_dropped)
                or len(raw_dropped) != len(set(raw_dropped))
                or not isinstance(source_errors, list) or not source_errors
            ):
                valid = False
                raw_dropped = raw_dropped if isinstance(raw_dropped, list) else []
                source_errors = source_errors if isinstance(source_errors, list) else []
            dropped_indexes = set(raw_dropped)
            error_indexes = set()
            for error in source_errors:
                match = re.match(r"section_(\d+):", error) if isinstance(error, str) else None
                if match:
                    error_indexes.add(int(match.group(1)))
            if error_indexes != dropped_indexes:
                valid = False
            prior_path = (
                root / "structure/continuation-v2-attempts" / package_id.replace(":", "__")
                / f"prior-receipt-{repair.get('source_receipt_sha256')}.json"
            )
            prior = read_json(prior_path) if prior_path.is_file() else {}
            if (
                prior.get("receipt_sha256") != object_hash(prior, "receipt_sha256")
                or prior.get("receipt_sha256") != repair.get("source_receipt_sha256")
                or prior.get("package_id") != package_id
                or prior.get("package_sha256") != package["package_sha256"]
                or prior.get("deterministic_status") != "needs_review"
                or prior.get("errors") != source_errors
                or prior.get("attempt_sha256") != repair.get("source_attempt_sha256")
            ):
                valid = False
            source_attempt_path = (
                root / "structure/continuation-v2-attempts" / package_id.replace(":", "__")
                / f"attempt-{repair.get('source_attempt_sha256')}.json"
            )
            source_attempt = read_json(source_attempt_path) if source_attempt_path.is_file() else {}
            if (
                source_attempt.get("attempt_sha256") != object_hash(source_attempt, "attempt_sha256")
                or source_attempt.get("attempt_sha256") != repair.get("source_attempt_sha256")
                or source_attempt.get("package_id") != package_id
                or source_attempt.get("package_sha256") != package["package_sha256"]
                or source_attempt.get("parse_or_validation_errors") != source_errors
                or source_attempt.get("raw_response_sha256") != text_hash(source_attempt.get("raw_response", ""))
                or source_attempt.get("model") != prior.get("model")
            ):
                valid = False
            repair_projection = {
                "strategy": repair.get("strategy"),
                "source_receipt_path": str(prior_path.relative_to(root)),
                "source_receipt_sha256": repair.get("source_receipt_sha256"),
                "source_attempt_sha256": repair.get("source_attempt_sha256"),
                "dropped_section_indexes": raw_dropped,
                "source_errors": source_errors,
                "content_added": repair.get("content_added"),
                "line_numbers_guessed_or_clamped": repair.get("line_numbers_guessed_or_clamped"),
            }
        attempt_hash = receipt.get("attempt_sha256")
        attempt_path = root / "structure/continuation-v2-attempts" / package_id.replace(":", "__") / f"attempt-{attempt_hash}.json"
        if not attempt_path.is_file():
            valid = False
            attempt = {}
        else:
            attempt = read_json(attempt_path)
        if (
            attempt.get("attempt_sha256") != object_hash(attempt, "attempt_sha256")
            or attempt.get("attempt_sha256") != attempt_hash
            or attempt.get("package_id") != package_id
            or attempt.get("package_sha256") != package["package_sha256"]
            or attempt.get("model") != receipt.get("model")
            or attempt.get("prompt_version") != receipt.get("prompt_version")
            or attempt.get("parse_or_validation_errors") != []
        ):
            valid = False
        if isinstance(repair, dict) and (
            attempt.get("source_receipt_sha256") != repair.get("source_receipt_sha256")
            or attempt.get("source_attempt_sha256") != repair.get("source_attempt_sha256")
            or attempt.get("repair_strategy") != repair.get("strategy")
            or attempt.get("dropped_section_indexes") != repair.get("dropped_section_indexes")
            or attempt.get("source_errors") != repair.get("source_errors")
        ):
            valid = False
        raw = attempt.get("raw_response")
        if not isinstance(raw, str) or attempt.get("raw_response_sha256") != text_hash(raw) or receipt.get("raw_response_sha256") != attempt.get("raw_response_sha256"):
            valid = False
            raw = raw if isinstance(raw, str) else ""
        try:
            raw_draft = extract_json(raw)
        except Exception:
            raw_draft = {}
            valid = False
        draft = receipt.get("draft") if isinstance(receipt.get("draft"), dict) else {}
        expected_draft_fields = {
            "package_id": package_id, "parent_entry_id": package["parent_entry_id"],
            "display_name": None, "name_resolution": {"status": "unresolved", "sources": []},
            "review_status": "machine_extracted",
        }
        if any(
            draft.get(field) != expected or (repair is None and raw_draft.get(field) != expected)
            for field, expected in expected_draft_fields.items()
        ):
            valid = False
        if (
            (repair is None and raw_draft.get("book_taxon", {}).get("scientific_name_candidate") != package["book_taxon_candidate"])
            or draft.get("book_taxon", {}).get("scientific_name_candidate") != package["book_taxon_candidate"]
        ):
            valid = False
        raw_sections, materialized_sections = raw_draft.get("sections"), draft.get("sections")
        if repair is not None:
            retained = raw_draft.get("retained_source_line_coordinates")
            if set(raw_draft) != {"package_id", "retained_source_line_coordinates"} or raw_draft.get("package_id") != package_id or not isinstance(retained, list):
                valid = False
                retained = []
            raw_sections = []
            for value in retained:
                line_range = value.get("source_line_range") if isinstance(value, dict) else None
                if (
                    not isinstance(value, dict) or set(value) != {"pdf_page", "section_type", "source_line_range"}
                    or not isinstance(line_range, list) or len(line_range) != 2
                ):
                    valid = False
                    continue
                raw_sections.append({
                    "section_type": value["section_type"], "pdf_page": value["pdf_page"],
                    "source_line_start": line_range[0], "source_line_end": line_range[1],
                })
            expected_warning = "Deterministic changed strategy dropped only sections that lacked an exact source locator; no line number was repaired or guessed."
            if draft.get("warnings") != list(raw_draft.get("warnings", [])) + [expected_warning]:
                valid = False
        expected_locators = []
        seen_types = set()
        if (
            not isinstance(raw_sections, list) or not isinstance(materialized_sections, list)
            or not raw_sections or len(raw_sections) != len(materialized_sections) or len(raw_sections) > 6
        ):
            valid = False
            raw_sections, materialized_sections = [], []
        for index, (raw_section, materialized) in enumerate(zip(raw_sections, materialized_sections)):
            section_type = raw_section.get("section_type") if isinstance(raw_section, dict) else None
            pdf_page = raw_section.get("pdf_page") if isinstance(raw_section, dict) else None
            start = raw_section.get("source_line_start") if isinstance(raw_section, dict) else None
            end = raw_section.get("source_line_end") if isinstance(raw_section, dict) else None
            page = pages.get((package["source_id"], pdf_page))
            if pdf_page not in package["pdf_pages"] or page is None:
                valid = False
                continue
            if section_type not in SECTION_TYPES:
                # Invalid section types are still source-materialized by the
                # maker runner before the receipt is marked needs_review.
                valid = False
            if section_type in seen_types:
                # The runner still materializes exact locators for a duplicate
                # type, then marks the receipt needs_review.  Mirror that
                # projection so the check remains auditable while invalid.
                valid = False
            seen_types.add(section_type)
            lines = page["text"].splitlines()
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > len(lines) or end - start + 1 > 60:
                valid = False
                continue
            quote = "\n".join(lines[start - 1:end])
            char_start = page["text"].find(quote)
            if not quote.strip() or char_start < 0 or page["text"].find(quote, char_start + 1) >= 0:
                valid = False
                continue
            expected_section = dict(raw_section)
            expected_section.pop("source_line_start", None)
            expected_section.pop("source_line_end", None)
            expected_section["source_line_range"] = [start, end]
            expected_section["exact_source_quote"] = quote
            if materialized != expected_section:
                valid = False
            expected_locators.append({
                "source_id": package["source_id"], "volume": package["volume"], "pdf_page": pdf_page,
                "source_pdf_sha256": source_hashes[package["source_id"]],
                "char_start": char_start, "char_end": char_start + len(quote),
                "source_line_start": start, "source_line_end": end,
                "page_text_sha256": page["text_sha256"], "exact_text_sha256": text_hash(quote),
                "section_index": index, "section_type": section_type,
            })
        if receipt.get("section_source_locators") != expected_locators:
            valid = False
        recomputed_status[package_id] = "pass" if valid else "needs_review"
        if check.get("status") != recomputed_status[package_id]:
            fail(errors, f"check status differs from independent result:{package_id}")
        if check.get("receipt_sha256") != receipt.get("receipt_sha256"):
            fail(errors, f"check receipt chain drift:{package_id}")
        if check.get("attempt_sha256") != attempt.get("attempt_sha256"):
            fail(errors, f"check attempt chain drift:{package_id}")
        if check.get("section_source_locators") != expected_locators:
            fail(errors, f"check locator projection drift:{package_id}")
        if check.get("changed_strategy_repair") != repair_projection:
            fail(errors, f"check changed-strategy repair projection drift:{package_id}")

    pass_count = sum(value == "pass" for value in recomputed_status.values())
    review_count = sum(value == "needs_review" for value in recomputed_status.values())
    awaiting_count = sum(value == "awaiting_receipt" for value in recomputed_status.values())
    all_pass = not errors and pass_count == 46 and review_count == 0 and awaiting_count == 0
    disposition_by_child = {item.get("child_entry_id"): item for item in dispositions}
    if len(dispositions) != 34 or len(disposition_by_child) != 34 or set(disposition_by_child) != set(segments):
        fail(errors, "child disposition set drift")
    for child_id, disposition in disposition_by_child.items():
        if disposition.get("disposition_sha256") != object_hash(disposition, "disposition_sha256"):
            fail(errors, f"child disposition hash mismatch:{child_id}")
        child_packages = sorted(by_child[child_id], key=lambda item: item["sequence"])
        child_passes = sum(recomputed_status[item["package_id"]] == "pass" for item in child_packages)
        expected_terminal = all_pass and child_passes == len(child_packages)
        if (
            disposition.get("parent_entry_id") != segment_parent[child_id]
            or disposition.get("taxon_candidate") != segments[child_id]["taxon_candidate"]
            or disposition.get("pdf_pages") != segments[child_id]["pdf_pages"]
            or disposition.get("package_ids") != [item["package_id"] for item in child_packages]
            or disposition.get("package_checks_passed") != child_passes
            or disposition.get("package_checks_required") != len(child_packages)
            or disposition.get("terminal") is not expected_terminal
            or disposition.get("embedding_ready_candidate") is not expected_terminal
            or disposition.get("name_resolution_status") != "unresolved"
            or disposition.get("layout_or_plate_claims_approved") is not False
            or disposition.get("canonical_write_allowed") is not False
        ):
            fail(errors, f"child disposition projection drift:{child_id}")

    if embedding.get("manifest_sha256") != object_hash(embedding, "manifest_sha256"):
        fail(errors, "embedding-ready child manifest hash mismatch")
    if (
        embedding.get("boundary_overlay_plan_sha256") != plan["plan_sha256"]
        or embedding.get("package_manifest_sha256") != package_manifest["manifest_sha256"]
        or embedding.get("source_receipt_sha256") != hashlib.sha256(source_receipt_path.read_bytes()).hexdigest()
        or embedding.get("canonical_write_allowed") is not False
        or embedding.get("chunk_write_allowed") is not False
        or embedding.get("index_write_allowed") is not False
        or embedding.get("embedding_calls_performed") is not False
    ):
        fail(errors, "embedding-ready child manifest source/safety drift")
    candidates = embedding.get("candidates", [])
    if embedding.get("candidate_count") != len(candidates):
        fail(errors, "candidate manifest count drift")
    if all_pass:
        if embedding.get("all_packages_deterministic_pass") is not True or len(candidates) != 34:
            fail(errors, "46-pass gate did not release exactly 34 candidates")
    elif embedding.get("all_packages_deterministic_pass") is not False or candidates:
        fail(errors, "partial v2 integration leaked child candidates")
    candidate_ids = set()
    for candidate in candidates:
        child_id = candidate.get("entry_id")
        if child_id in candidate_ids or child_id not in segments:
            fail(errors, f"duplicate or unknown child candidate:{child_id}")
            continue
        candidate_ids.add(child_id)
        if candidate.get("candidate_sha256") != object_hash(candidate, "candidate_sha256"):
            fail(errors, f"candidate hash mismatch:{child_id}")
        disposition = disposition_by_child[child_id]
        if (
            candidate.get("source_parent_entry_id") != segment_parent[child_id]
            or candidate.get("book_taxon_candidate") != segments[child_id]["taxon_candidate"]
            or candidate.get("boundary_segment_sha256") != segments[child_id]["segment_sha256"]
            or candidate.get("source_disposition_sha256") != disposition["disposition_sha256"]
            or candidate.get("display_name") is not None
            or candidate.get("name_resolution") != {"status": "unresolved", "sources": []}
            or candidate.get("review_status") != "machine_extracted"
            or candidate.get("layout_or_plate_claims_approved") is not False
            or candidate.get("canonical_write_allowed") is not False
            or candidate.get("embedding_call_performed") is not False
        ):
            fail(errors, f"candidate identity/safety drift:{child_id}")
        if any(section.get("section_type") == "plate_description" for section in candidate.get("sections", [])):
            fail(errors, f"plate section leaked into child candidate:{child_id}")
        for section in candidate.get("sections", []):
            if len(section.get("exact_source_quotes", [])) != len(section.get("source_locators", [])):
                fail(errors, f"candidate quote/locator cardinality drift:{child_id}")
            for quote, locator in zip(section.get("exact_source_quotes", []), section.get("source_locators", [])):
                page = pages.get((locator.get("source_id"), locator.get("pdf_page")))
                if page is None or not (
                    locator.get("pdf_page") in segments[child_id]["pdf_pages"]
                    and locator.get("page_text_sha256") == page["text_sha256"]
                    and locator.get("source_pdf_sha256") == source_hashes[locator["source_id"]]
                    and page["text"][locator["char_start"]:locator["char_end"]] == quote
                    and locator.get("exact_text_sha256") == text_hash(quote)
                ):
                    fail(errors, f"candidate exact source locator drift:{child_id}")
            span_provenance = section.get("continuation_v2_provenance", [])
            if not span_provenance:
                fail(errors, f"candidate section lacks v2 provenance:{child_id}")
            for item in span_provenance:
                package_id = item.get("package_id")
                package = package_by_id.get(package_id)
                receipt = receipts.get(package_id)
                check = check_by_package.get(package_id)
                section_index = item.get("package_section_index")
                if (
                    package is None or receipt is None or check is None
                    or package.get("child_entry_id") != child_id
                    or item.get("package_sha256") != package.get("package_sha256")
                    or item.get("receipt_sha256") != receipt.get("receipt_sha256")
                    or item.get("attempt_sha256") != receipt.get("attempt_sha256")
                    or item.get("validation_check_sha256") != check.get("check_sha256")
                    or not isinstance(section_index, int)
                    or section_index < 0
                    or section_index >= len(receipt.get("draft", {}).get("sections", []))
                ):
                    fail(errors, f"candidate section provenance chain drift:{child_id}")
                    continue
                receipt_section = receipt["draft"]["sections"][section_index]
                receipt_locator = next(
                    (
                        locator for locator in check.get("section_source_locators", [])
                        if locator.get("section_index") == section_index
                    ),
                    None,
                )
                span_matches = any(
                    quote == receipt_section.get("exact_source_quote") and locator == receipt_locator
                    for quote, locator in zip(
                        section.get("exact_source_quotes", []), section.get("source_locators", [])
                    )
                )
                if section.get("section_type") != receipt_section.get("section_type") or not span_matches:
                    fail(errors, f"candidate section drifted from receipt/check:{child_id}")
        provenance = candidate.get("continuation_v2_receipts", [])
        expected_packages = sorted(by_child[child_id], key=lambda item: item["sequence"])
        if [item.get("package_id") for item in provenance] != [item["package_id"] for item in expected_packages]:
            fail(errors, f"candidate receipt provenance coverage drift:{child_id}")
        for item in provenance:
            package = package_by_id[item["package_id"]]
            receipt = receipts[item["package_id"]]
            check = check_by_package[item["package_id"]]
            if (
                item.get("package_sha256") != package["package_sha256"]
                or item.get("receipt_sha256") != receipt["receipt_sha256"]
                or item.get("attempt_sha256") != receipt["attempt_sha256"]
                or item.get("validation_check_sha256") != check["check_sha256"]
            ):
                fail(errors, f"candidate provenance hash chain drift:{child_id}")

    if summary.get("summary_sha256") != object_hash(summary, "summary_sha256"):
        fail(errors, "integration-v2 summary hash mismatch")
    expected_summary = {
        "parents": 18, "child_segments": 34, "packages": 46,
        "receipts_found": pass_count + review_count,
        "package_checks_passed": pass_count, "package_checks_needs_review": review_count,
        "package_checks_awaiting_receipt": awaiting_count, "child_dispositions": 34,
        "embedding_ready_child_candidates": len(candidates),
    }
    if any(summary.get(field) != expected for field, expected in expected_summary.items()):
        fail(errors, "integration-v2 summary count projection drift")
    expected_complete = all_pass and len(candidates) == 34 and len(dispositions) == 34
    if summary.get("complete") is not expected_complete:
        fail(errors, "integration-v2 summary complete flag drift")
    if any(value is not False for value in summary.get("safety", {}).values()):
        fail(errors, "integration-v2 summary safety escaped")

    result = {
        "status": "PASS" if not errors else "FAIL", "complete": expected_complete and not errors,
        "parents": len(parents), "child_segments": len(segments), "packages": len(packages),
        "receipts_found": pass_count + review_count, "package_checks_passed": pass_count,
        "package_checks_needs_review": review_count, "package_checks_awaiting_receipt": awaiting_count,
        "child_dispositions": len(dispositions), "embedding_ready_child_candidates": len(candidates),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False))
    if errors or (args.require_complete and not result["complete"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
