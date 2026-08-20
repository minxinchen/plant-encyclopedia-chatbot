#!/usr/bin/env python3
"""Validate source, naming, chain, and safety invariants for record staging."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def projected_naming_sources(naming: dict) -> list[dict]:
    output = []
    seen = set()
    for evidence in naming.get("evidence", []):
        authority = evidence.get("authority") or evidence.get("source_id") or "unknown"
        urls = []
        for key in ("name_match_url", "query_url", "url", "taxon_url", "taxon_api_url"):
            if evidence.get(key):
                urls.append(evidence[key])
        for match in evidence.get("name_match_results", []):
            for key in ("taxon_url", "taxon_api_url"):
                if match.get(key):
                    urls.append(match[key])
        for url in urls:
            key = (authority, url)
            if key in seen:
                continue
            seen.add(key)
            output.append({
                "authority": authority,
                "url": url,
                "query_name": evidence.get("query_name"),
                "retrieved_at": evidence.get("retrieved_at"),
                "assertion_scope": evidence.get("assertion_scope"),
            })
    return output


def candidate_source_chain(candidate: dict) -> dict:
    fields = (
        "maker_receipt_sha256",
        "validation_check_sha256",
        "source_disposition_sha256",
        "boundary_overlay_plan_sha256",
        "boundary_segment_sha256",
        "package_sha256",
        "receipt_sha256",
        "recovery_kind",
        "source_parent_entry_id",
        "continuation_v2_receipts",
    )
    return {key: candidate.get(key) for key in fields if candidate.get(key) is not None}


def load_frozen_pages(root: Path) -> dict[tuple[str, int], dict]:
    pages = {}
    for path in sorted(root.glob("shards/*/inputs/pages.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            key = (row["source_id"], row["pdf_page"])
            previous = pages.get(key)
            if previous is not None and previous["text_sha256"] != row["text_sha256"]:
                raise SystemExit(f"conflicting frozen page: {key}")
            pages[key] = row
    return pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--require-caught-up", action="store_true")
    args = parser.parse_args()
    integration = read_json(args.root / "integration/embedding-ready-candidate-manifest.json")
    candidates = {row["entry_id"]: row for row in integration["candidates"]}
    pages = load_frozen_pages(args.root)
    manifest = read_json(args.root / "records-candidate/manifest.json")
    errors = []
    records = []

    for row in manifest["records"]:
        path = Path(row["path"])
        if not path.is_absolute():
            path = args.root / "records-candidate" / path
        if not path.exists():
            errors.append(f"missing_record:{row['entry_id']}")
            continue
        record = read_json(path)
        records.append(record)
        entry_id = record.get("entry_id")
        candidate = candidates.get(entry_id)
        if candidate is None:
            errors.append(f"unknown_candidate:{entry_id}")
            continue
        if row["entry_id"] != entry_id:
            errors.append(f"manifest_entry_mismatch:{entry_id}")
        stored_hash = record.get("record_sha256")
        unhashed = dict(record)
        unhashed.pop("record_sha256", None)
        if stored_hash != sha256_json(unhashed) or row["record_sha256"] != stored_hash:
            errors.append(f"record_hash_mismatch:{entry_id}")
        provenance = record.get("provenance", {})
        chain = {
            "candidate_sha256": candidate["candidate_sha256"],
            "maker_receipt_sha256": candidate.get("maker_receipt_sha256"),
            "validation_check_sha256": candidate.get("validation_check_sha256"),
            "source_disposition_sha256": candidate.get("source_disposition_sha256"),
            "integration_manifest_sha256": integration["manifest_sha256"],
            "source_receipt_sha256": integration.get("source_receipt_sha256"),
            "candidate_source_chain": candidate_source_chain(candidate),
        }
        for key, expected in chain.items():
            if provenance.get(key) != expected:
                errors.append(f"cross_chain_substitution:{entry_id}:{key}")
        naming_path = args.root / "naming/staging" / f"{entry_id.replace(':', '__')}.naming.json"
        if not naming_path.exists() or provenance.get("naming_artifact_sha256") != sha256_text(naming_path.read_text(encoding="utf-8")):
            errors.append(f"naming_chain_mismatch:{entry_id}")
            continue
        naming = read_json(naming_path)
        resolution = record["name_resolution"]
        source_resolution = naming["name_resolution"]
        if resolution.get("sources") != projected_naming_sources(naming):
            errors.append(f"naming_source_projection_drift:{entry_id}")
        expected_scope = source_resolution.get("display_name_source_scope") or (
            "unresolved" if source_resolution.get("display_name_zh_tw") is None else "unclassified_staging"
        )
        for key in ("terminal_status", "accepted_scientific_name", "display_name_zh_tw"):
            record_value = record["display_name"] if key == "display_name_zh_tw" else resolution.get(key)
            if record_value != source_resolution.get(key):
                errors.append(f"naming_value_drift:{entry_id}:{key}")
        if resolution.get("display_name_source_scope") != expected_scope:
            errors.append(f"naming_value_drift:{entry_id}:display_name_source_scope")
        if resolution["terminal_status"] == "unresolved" and record["display_name"] is not None:
            errors.append(f"unresolved_display_name_injection:{entry_id}")
        if resolution["terminal_status"] in {"accepted", "alias"}:
            if not record["display_name"] or not resolution.get("sources"):
                errors.append(f"resolved_name_lacks_evidence:{entry_id}")
            for source in resolution.get("sources", []):
                if not str(source.get("url", "")).startswith("https://") or not source.get("retrieved_at"):
                    errors.append(f"invalid_naming_evidence:{entry_id}")

        candidate_sections = {
            (section_index, span_index): (quote, locator, section)
            for section_index, section in enumerate(candidate["sections"])
            for span_index, (quote, locator) in enumerate(zip(section["exact_source_quotes"], section["source_locators"]))
        }
        for section in record["sections"]:
            if section["section_type"] == "plate_description":
                errors.append(f"plate_leakage:{entry_id}")
            locator = section["source_locator"]
            section_suffix = section["section_id"].rsplit(":s", 1)[1]
            record_section_index = int(section_suffix.split(":x", 1)[0])
            record_span_index = int(section_suffix.rsplit("x", 1)[1])
            key = (record_section_index, record_span_index)
            expected = candidate_sections.get(key)
            if expected is None:
                errors.append(f"unknown_section_span:{section['section_id']}")
                continue
            quote, expected_locator, expected_section = expected
            if locator != expected_locator or section["original_text"] != quote:
                errors.append(f"candidate_section_drift:{section['section_id']}")
            page = pages.get((locator["source_id"], locator["pdf_page"]))
            if page is None:
                errors.append(f"missing_frozen_page:{section['section_id']}")
                continue
            if page["text_sha256"] != locator["page_text_sha256"]:
                errors.append(f"page_hash_drift:{section['section_id']}")
            if page["text"][locator["char_start"]:locator["char_end"]] != section["original_text"]:
                errors.append(f"source_quote_drift:{section['section_id']}")
            if sha256_text(section["original_text"]) != locator["exact_text_sha256"]:
                errors.append(f"exact_hash_drift:{section['section_id']}")
            evidence = record["book_evidence"][section["evidence_indexes"][0]]
            for field in ("source_id", "pdf_page", "page_text_sha256", "source_pdf_sha256"):
                if evidence.get(field) != locator.get(field):
                    errors.append(f"evidence_locator_drift:{section['section_id']}:{field}")

        safety = record.get("safety", {})
        if record.get("review_status") != "machine_extracted_candidate":
            errors.append(f"review_status_promotion:{entry_id}")
        if safety.get("layout_or_plate_claims_approved"):
            errors.append(f"layout_self_approval:{entry_id}")
        for flag in ("canonical_write_allowed", "embedding_call_performed", "index_write_allowed"):
            if safety.get(flag):
                errors.append(f"unsafe_write_flag:{entry_id}:{flag}")
        for target in ("canonical_target", "embedding_target", "index_target"):
            if safety.get(target) is not None:
                errors.append(f"unsafe_write_target:{entry_id}:{target}")

    duplicate_ids = [key for key, count in Counter(record["entry_id"] for record in records).items() if count > 1]
    errors.extend(f"duplicate_entry:{entry_id}" for entry_id in duplicate_ids)
    if manifest["record_count"] != len(records):
        errors.append("manifest_record_count_mismatch")
    if manifest["source_candidate_count"] != integration["candidate_count"]:
        errors.append("source_candidate_count_mismatch")
    if manifest["integration_manifest_sha256"] != integration["manifest_sha256"]:
        errors.append("integration_manifest_hash_mismatch")
    if args.require_caught_up and manifest["missing_naming_entry_ids"]:
        errors.append("naming_not_caught_up")
    if manifest.get("canonical_writes") or manifest.get("embedding_calls") or manifest.get("index_writes"):
        errors.append("manifest_reports_unsafe_writes")
    manifest_without_hash = dict(manifest)
    stored_manifest_hash = manifest_without_hash.pop("manifest_sha256", None)
    if stored_manifest_hash != sha256_json(manifest_without_hash):
        errors.append("manifest_hash_mismatch")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "source_candidates": integration["candidate_count"],
        "records": len(records),
        "sections": sum(len(record["sections"]) for record in records),
        "missing_naming": len(manifest["missing_naming_entry_ids"]),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
