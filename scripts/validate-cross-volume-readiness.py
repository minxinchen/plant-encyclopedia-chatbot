#!/usr/bin/env python3
"""Validate four volume samples plus the approved bounded production candidates.

This does not declare any volume complete. It checks only durable, approved artifacts.

author: Codex (GPT-5)
date: 2026-08-12
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")
PROFILE_ID = "section-aware-512-100-v1"

SAMPLES = [
    {
        "kind": "volume_sample",
        "volume": 1,
        "record_id": "podophyllum-peltatum",
        "report": "reports/chunking-ab-podophyllum-2026-08-11.json",
        "fixture": "data/tests/podophyllum-chunking-ab.json",
        "chunks": "data/chunks/podophyllum-peltatum-section-aware-512-100-v1.jsonl",
    },
    {
        "kind": "volume_sample",
        "volume": 2,
        "record_id": "strychnos-nux-vomica",
        "report": "reports/volume2-strychnos-profile-check-2026-08-11.json",
        "fixture": "data/tests/strychnos-nux-vomica-volume2.json",
        "chunks": "data/chunks/strychnos-nux-vomica-section-aware-512-100-v1.jsonl",
    },
    {
        "kind": "volume_sample",
        "volume": 3,
        "record_id": "carica-papaya",
        "report": "reports/volume3-carica-papaya-profile-check-2026-08-11.json",
        "fixture": "data/tests/carica-papaya-volume3.json",
        "chunks": "data/chunks/carica-papaya-section-aware-512-100-v1.jsonl",
    },
    {
        "kind": "volume_sample",
        "volume": 4,
        "record_id": "cibotium-barometz",
        "report": "reports/volume4-cibotium-barometz-profile-check-2026-08-12.json",
        "fixture": "data/tests/cibotium-barometz-volume4.json",
        "chunks": "data/chunks/cibotium-barometz-section-aware-512-100-v1.jsonl",
    },
    {
        "kind": "production_candidate",
        "volume": 1,
        "record_id": "atropa-belladonna",
        "report": "reports/production1-atropa-belladonna-profile-check-2026-08-12.json",
        "fixture": "data/tests/atropa-belladonna-volume1.json",
        "chunks": "data/chunks/atropa-belladonna-section-aware-512-100-v1.jsonl",
    },
    {
        "kind": "production_candidate",
        "volume": 2,
        "record_id": "piper-nigrum",
        "report": "reports/production2-piper-nigrum-profile-check-2026-08-12.json",
        "fixture": "data/tests/piper-nigrum-volume2.json",
        "chunks": "data/chunks/piper-nigrum-section-aware-512-100-v1.jsonl",
    },
    {
        "kind": "production_candidate",
        "volume": 2,
        "record_id": "polygala-senega",
        "report": "reports/production3-polygala-senega-profile-check-2026-08-12.json",
        "fixture": "data/tests/polygala-senega-volume2.json",
        "chunks": "data/chunks/polygala-senega-section-aware-512-100-v1.jsonl",
    },
    {
        "kind": "production_candidate",
        "volume": 2,
        "record_id": "laminaria-hyperborea",
        "report": "reports/production4-laminaria-hyperborea-profile-check-2026-08-13.json",
        "fixture": "data/tests/laminaria-hyperborea-volume2.json",
        "chunks": "data/chunks/laminaria-hyperborea-section-aware-512-100-v1.jsonl",
    },
]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=LAB / "reports/production4-text-readiness-2026-08-13.json",
    )
    parser.add_argument(
        "--main-db",
        type=Path,
        default=LAB / "data/index/plant-embeddings.sqlite",
    )
    args = parser.parse_args()

    canonical_ids: set[str] = set()
    fixture_query_ids: set[str] = set()
    sample_results = []
    total_external_calls = 0
    for sample in SAMPLES:
        report_path = LAB / sample["report"]
        fixture_path = LAB / sample["fixture"]
        chunks_path = LAB / sample["chunks"]
        for path in (report_path, fixture_path, chunks_path):
            if not path.is_file():
                fail(f"missing required artifact: {path}")
        report = load(report_path)
        fixture = load(fixture_path)
        chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line]
        if report.get("verdict") != "promote" or report.get("selected_profile") != PROFILE_ID:
            fail(f"sample is not promoted on the portable profile: volume {sample['volume']}")
        if report.get("incremental_usd") != 0 or report.get("paid_fallback_used"):
            fail(f"sample violates zero-incremental-cost policy: volume {sample['volume']}")
        if not chunks or {item["profile_id"] for item in chunks} != {PROFILE_ID}:
            fail(f"canonical chunk package is empty or mixed: volume {sample['volume']}")
        if {item["record_id"] for item in chunks} != {sample["record_id"]}:
            fail(f"canonical package record mismatch: volume {sample['volume']}")
        query_ids = {item["query_id"] for item in fixture["queries"]}
        statuses = {item["expected_status"] for item in fixture["queries"]}
        if not {"answerable", "unanswerable"}.issubset(statuses):
            fail(f"answer/refusal coverage missing: volume {sample['volume']}")
        if not any(item["query_id"].endswith("-en") or "-en-" in item["query_id"] for item in fixture["queries"]):
            fail(f"English answerable query missing: volume {sample['volume']}")
        canonical_ids.update(item["chunk_id"] for item in chunks)
        fixture_query_ids.update(query_ids)
        calls = int(report.get("external_model_calls", report.get("external_model_calls_this_run", 0)))
        total_external_calls += calls
        sample_results.append({
            "kind": sample["kind"],
            "volume": sample["volume"],
            "record_id": sample["record_id"],
            "pdf_pages": fixture["pdf_pages"],
            "canonical_child_chunks": len(chunks),
            "query_count": len(query_ids),
            "pure_semantic_verdict": report.get("pure_semantic_verdict", report.get("verdict")),
            "hybrid_verdict": report["verdict"],
            "incremental_usd": report["incremental_usd"],
        })

    connection = sqlite3.connect(f"file:{args.main_db}?mode=ro", uri=True)
    main_ids = {
        row[0] for row in connection.execute(
            "SELECT chunk_id FROM embedding_chunks WHERE profile_id=? AND review_status='approved'",
            (PROFILE_ID,),
        )
    }
    main_query_ids = {
        row[0] for row in connection.execute(
            "SELECT query_id FROM embedding_queries WHERE profile_id=? AND review_status='approved'",
            (PROFILE_ID,),
        )
    }
    meta = dict(connection.execute("SELECT key, value FROM embedding_meta"))
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    connection.close()
    if main_ids != canonical_ids:
        fail(f"main/canonical child mismatch: missing={sorted(canonical_ids-main_ids)}, extra={sorted(main_ids-canonical_ids)}")
    if main_query_ids != fixture_query_ids:
        fail("main query set does not equal the approved bounded fixtures")
    if meta.get("active_chunk_profile") != PROFILE_ID or integrity != "ok":
        fail("main index metadata or SQLite integrity failed")

    grounding = load(LAB / "data/tests/cibotium-barometz-grounding.json")
    grounding_statuses = {item["category"] for item in grounding["cases"]}
    if not {"wrong_name", "incomplete_section", "medical_advice"}.issubset(grounding_statuses):
        fail("grounding guard fixture is incomplete")
    chat_cases = load(LAB / "data/tests/chat-api-acceptance.json")["cases"]

    output = {
        "schema_version": "1.0",
        "run_id": "production4-text-readiness-2026-08-13",
        "validated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "status": "fourth_bounded_production_candidate_approved",
        "not_claimed": [
            "No volume is fully reviewed.",
            "The 512/100 profile is not universally superior to page baselines.",
            "Image reasoning is not part of the text-only production proposal.",
        ],
        "profile_id": PROFILE_ID,
        "sample_results": sample_results,
        "four_volume_sample_count": sum(item["kind"] == "volume_sample" for item in sample_results),
        "production_candidate_count": sum(item["kind"] == "production_candidate" for item in sample_results),
        "bounded_package_count": len(sample_results),
        "approved_child_chunks": len(main_ids),
        "distinct_parent_pages": len({item.rsplit(":", 1)[0] for item in main_ids}),
        "approved_query_vectors": len(main_query_ids),
        "chat_acceptance_cases": len(chat_cases),
        "grounding_guard_statuses": sorted(grounding_statuses),
        "external_model_calls_across_sample_reports": total_external_calls,
        "incremental_usd": 0,
        "paid_fallback_used": False,
        "next_batch_gate": "Select one OCR-clean taxon spanning at most six adjacent PDF pages; preserve the same source, Taiwan-name, bilingual retrieval and refusal checks before promotion.",
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
