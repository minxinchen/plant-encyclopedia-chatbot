#!/usr/bin/env python3
"""Build source-exact, non-canonical plant record candidates.

Inputs are the integration candidate manifest and Taiwan naming staging.  These
records are machine-extracted handoff artifacts, never canonical approvals.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def slug(entry_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", entry_id.casefold()).strip("-")


def naming_sources(naming: dict) -> list[dict]:
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
    """Project every available maker/check chain without inventing one hash."""
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    integration_path = args.root / "integration/embedding-ready-candidate-manifest.json"
    integration = read_json(integration_path)
    naming_dir = args.root / "naming/staging"
    output_dir = args.root / "records-candidate"
    records_dir = output_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    expected_files = set()
    manifest_records = []
    missing_naming = []

    for candidate in integration["candidates"]:
        entry_id = candidate["entry_id"]
        naming_path = naming_dir / f"{entry_id.replace(':', '__')}.naming.json"
        if not naming_path.exists():
            missing_naming.append(entry_id)
            continue
        naming_text = naming_path.read_text(encoding="utf-8")
        naming = json.loads(naming_text)
        if naming["entry_id"] != entry_id:
            raise SystemExit(f"naming entry mismatch: {entry_id}")
        resolution = naming["name_resolution"]
        terminal_status = resolution["terminal_status"]
        display_name = resolution.get("display_name_zh_tw")
        display_name_source_scope = resolution.get("display_name_source_scope") or (
            "unresolved" if display_name is None else "unclassified_staging"
        )
        if terminal_status == "unresolved" and display_name is not None:
            raise SystemExit(f"unresolved name injection: {entry_id}")
        if terminal_status in {"accepted", "alias"} and not display_name:
            raise SystemExit(f"resolved entry lacks Chinese display name: {entry_id}")

        evidence = []
        evidence_index = {}
        sections = []
        for section_index, section in enumerate(candidate["sections"]):
            if section["section_type"] == "plate_description":
                raise SystemExit(f"plate section leaked into integration candidate: {entry_id}")
            quotes = section["exact_source_quotes"]
            locators = section["source_locators"]
            if len(quotes) != len(locators):
                raise SystemExit(f"quote/locator mismatch: {entry_id}:section-{section_index}")
            for span_index, (quote, locator) in enumerate(zip(quotes, locators)):
                key = (locator["source_id"], locator["pdf_page"])
                if key not in evidence_index:
                    evidence_index[key] = len(evidence)
                    evidence.append({
                        "source_id": locator["source_id"],
                        "volume": locator["volume"],
                        "pdf_page": locator["pdf_page"],
                        "evidence_type": "text",
                        "page_text_sha256": locator["page_text_sha256"],
                        "source_pdf_sha256": locator["source_pdf_sha256"],
                    })
                sections.append({
                    "section_id": f"{entry_id}:s{section_index:02d}:x{span_index:02d}",
                    "section_type": section["section_type"],
                    "original_text": quote,
                    "normalized_text": section.get("normalized_text_candidate"),
                    "zh_tw_rendering": section.get("zh_tw_rendering_candidate") if span_index == 0 else None,
                    "evidence_indexes": [evidence_index[key]],
                    "source_locator": locator,
                    "exact_text_sha256": sha256_text(quote),
                    "review_status": "machine_extracted_candidate",
                })

        sources = naming_sources(naming)
        if terminal_status in {"accepted", "alias"} and not sources:
            raise SystemExit(f"resolved entry lacks naming evidence URL: {entry_id}")
        record = {
            "schema_version": "1.0",
            "record_id": f"preembedding-{slug(entry_id)}",
            "entry_id": entry_id,
            "owner_shard": candidate.get("owner_shard") or naming.get("owner_shard"),
            "book_taxon": {
                "scientific_name": candidate["book_taxon_candidate"],
                "authorship": naming["book_name"].get("authorship_candidate"),
                "aliases": naming["book_name"].get("aliases_candidates", []),
            },
            "display_name": display_name,
            "name_resolution": {
                "terminal_status": terminal_status,
                "query_names": resolution.get("query_names", []),
                "accepted_scientific_name": resolution.get("accepted_scientific_name"),
                "display_name_source_scope": display_name_source_scope,
                "taiwan_occurrence_status": resolution.get("taiwan_occurrence_status", "not_checked"),
                "checked_at": resolution.get("checked_at"),
                "sources": sources,
                "rationale": resolution.get("rationale"),
            },
            "book_evidence": evidence,
            "sections": sections,
            "review_status": "machine_extracted_candidate",
            "warnings": list(dict.fromkeys([
                *naming.get("warnings", []),
                "Köhler book facts are separate from external Taiwan naming metadata.",
                "Historical uses are not modern medical advice.",
                "Plate and image claims are excluded until visual review.",
            ])),
            "provenance": {
                "source_receipt_sha256": integration.get("source_receipt_sha256"),
                "integration_manifest_sha256": integration["manifest_sha256"],
                "candidate_sha256": candidate["candidate_sha256"],
                "maker_receipt_sha256": candidate.get("maker_receipt_sha256"),
                "validation_check_sha256": candidate.get("validation_check_sha256"),
                "source_disposition_sha256": candidate.get("source_disposition_sha256"),
                "candidate_source_chain": candidate_source_chain(candidate),
                "naming_artifact_sha256": sha256_text(naming_text),
            },
            "safety": {
                "book_facts_separate_from_naming_metadata": True,
                "layout_or_plate_claims_approved": False,
                "canonical_write_allowed": False,
                "canonical_target": None,
                "embedding_call_performed": False,
                "embedding_target": None,
                "index_write_allowed": False,
                "index_target": None,
            },
        }
        record["record_sha256"] = sha256_json(record)
        filename = f"{entry_id.replace(':', '__')}.record.json"
        expected_files.add(filename)
        output = records_dir / filename
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
        manifest_records.append({
            "entry_id": entry_id,
            "path": f"records/{filename}",
            "record_sha256": record["record_sha256"],
            "candidate_sha256": candidate["candidate_sha256"],
            "naming_artifact_sha256": record["provenance"]["naming_artifact_sha256"],
            "section_count": len(sections),
            "evidence_count": len(evidence),
        })

    for stale in records_dir.glob("*.record.json"):
        if stale.name not in expected_files:
            stale.unlink()
    manifest_records.sort(key=lambda row: row["entry_id"])
    manifest = {
        "schema_version": "1.0",
        "generated_at": now(),
        "integration_manifest_sha256": integration["manifest_sha256"],
        "source_candidate_count": integration["candidate_count"],
        "record_count": len(manifest_records),
        "missing_naming_entry_ids": missing_naming,
        "records": manifest_records,
        "review_status": "machine_extracted_candidate",
        "canonical_writes": 0,
        "embedding_calls": 0,
        "index_writes": 0,
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    output = output_dir / "manifest.json"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"manifest": str(output), **{k: manifest[k] for k in ("source_candidate_count", "record_count", "missing_naming_entry_ids")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
