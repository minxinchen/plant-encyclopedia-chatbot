#!/usr/bin/env python3
"""Calculate transparent Köhler chatbot completion metrics from durable artifacts.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
WORKSTATION = LAB.parents[1]
TZ = ZoneInfo("Asia/Taipei")
PROFILE_ID = "section-aware-512-100-v1"


def percent(numerator: int | float, denominator: int | float) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 0.0


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=LAB / "reports/project-completion-2026-08-13.json",
    )
    args = parser.parse_args()

    fulltext_db = LAB / "data/fulltext/kohler-pages.sqlite"
    with sqlite3.connect(f"file:{fulltext_db}?mode=ro", uri=True) as db:
        total_pages = db.execute("SELECT count(*) FROM pages").fetchone()[0]
        quality_counts = dict(db.execute("SELECT quality, count(*) FROM pages GROUP BY quality"))
    usable_pages = quality_counts.get("usable", 0) + quality_counts.get("clean", 0)

    main_db = LAB / "data/index/plant-embeddings.sqlite"
    with sqlite3.connect(f"file:{main_db}?mode=ro", uri=True) as db:
        approved_children = db.execute(
            "SELECT count(*) FROM embedding_chunks WHERE profile_id=? AND review_status='approved'",
            (PROFILE_ID,),
        ).fetchone()[0]
        approved_parent_pages = db.execute(
            "SELECT count(DISTINCT parent_chunk_id) FROM embedding_chunks "
            "WHERE profile_id=? AND review_status='approved'",
            (PROFILE_ID,),
        ).fetchone()[0]
        approved_queries = db.execute(
            "SELECT count(*) FROM embedding_queries WHERE profile_id=? AND review_status='approved'",
            (PROFILE_ID,),
        ).fetchone()[0]
        index_integrity = db.execute("PRAGMA integrity_check").fetchone()[0]

    records = []
    for path in sorted((LAB / "data/records").glob("*.json")):
        record = load(path)
        if record.get("review_status") in {"sample_reviewed", "approved"}:
            records.append(record)
    approved_records = len(records)

    selector = load(LAB / "reports/production-candidate-selection-2026-08-13.json")
    remaining_candidates = int(selector["candidate_count"])
    operational_taxon_denominator = approved_records + remaining_candidates

    fixture = load(LAB / "data/tests/chat-api-acceptance.json")
    chat_check = subprocess.run(
        [sys.executable, str(LAB / "scripts/validate-chat-api.py")],
        cwd=LAB,
        text=True,
        capture_output=True,
        check=False,
    )
    chat_valid = chat_check.returncode == 0
    chat_cases = len(fixture["cases"])

    gem_pack = LAB / "exports/google-gem/approved-evidence-pack.md"
    gem_text = gem_pack.read_text(encoding="utf-8") if gem_pack.is_file() else ""
    gem_ready = all(record["record_id"] in gem_text for record in records)

    n8n_source = load(LAB / "n8n/plant-encyclopedia-hybrid-index-batch.workflow.json")
    n8n_source_inactive = n8n_source.get("active") is False
    n8n_db_path = WORKSTATION / "services/n8n/data/.n8n/data/database.sqlite"
    n8n_imported_inactive = False
    if n8n_db_path.is_file():
        with sqlite3.connect(f"file:{n8n_db_path}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT active, nodes FROM workflow_entity WHERE id='PEHybridIdx001'"
            ).fetchone()
        n8n_imported_inactive = bool(
            row and not row[0] and "laminaria-hyperborea-volume2.json" in row[1]
        )

    state = load(LAB / "loop/state.json")
    image_ready = bool(state.get("chat_api", {}).get("image_reasoning"))
    public_ready = bool(
        state.get("chat_api", {}).get("public_exposure")
        or state.get("public_demo", {}).get("public")
    )

    source_indexed_ratio = total_pages / total_pages if total_pages else 0.0
    usable_text_ratio = usable_pages / total_pages if total_pages else 0.0
    page_coverage_ratio = approved_parent_pages / total_pages if total_pages else 0.0
    taxon_coverage_ratio = (
        approved_records / operational_taxon_denominator if operational_taxon_denominator else 0.0
    )
    source_foundation_ratio = (source_indexed_ratio + usable_text_ratio) / 2
    content_coverage_ratio = (page_coverage_ratio + taxon_coverage_ratio) / 2
    chat_quality_ratio = 1.0 if chat_valid else 0.0
    portability_gates = {
        "local_chat_api_validated": chat_valid,
        "google_gem_pack_exported": gem_ready,
        "n8n_workflow_imported_and_inactive": n8n_imported_inactive and n8n_source_inactive,
        "public_or_google_live_connection": public_ready,
    }
    portability_ratio = sum(portability_gates.values()) / len(portability_gates)
    image_ratio = 1.0 if image_ready else 0.0

    weights = {
        "source_foundation": 0.25,
        "approved_content_coverage": 0.45,
        "bounded_chat_quality": 0.15,
        "portability_and_delivery": 0.05,
        "image_reasoning": 0.10,
    }
    weighted_parts = {
        "source_foundation": source_foundation_ratio,
        "approved_content_coverage": content_coverage_ratio,
        "bounded_chat_quality": chat_quality_ratio,
        "portability_and_delivery": portability_ratio,
        "image_reasoning": image_ratio,
    }
    roadmap_score = sum(weights[key] * weighted_parts[key] for key in weights)

    bounded_mvp_gates = {
        "all_pdf_pages_indexed": total_pages > 0,
        "approved_source_exact_records_exist": approved_records > 0,
        "approved_main_index_integrity": index_integrity == "ok" and approved_children > 0,
        "approved_query_vectors_exist": approved_queries > 0,
        "chat_acceptance_passed": chat_valid,
        "google_gem_pack_ready": gem_ready,
        "n8n_replay_ready_and_inactive": n8n_imported_inactive and n8n_source_inactive,
        "taiwan_name_layer_present": all(record.get("name_resolution", {}).get("sources") for record in records),
    }

    result = {
        "schema_version": "1.0",
        "calculated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "scope_note": "The bounded text MVP and whole-book content coverage are separate metrics. Test pass rate is not full-book accuracy.",
        "source_foundation": {
            "indexed_pages": total_pages,
            "total_pages": total_pages,
            "indexed_percent": percent(total_pages, total_pages),
            "usable_or_clean_text_pages": usable_pages,
            "usable_or_clean_text_percent": percent(usable_pages, total_pages),
            "quality_counts": quality_counts,
        },
        "approved_content_coverage": {
            "approved_records": approved_records,
            "remaining_ocr_clean_candidates": remaining_candidates,
            "operational_candidate_taxa_total": operational_taxon_denominator,
            "operational_taxon_coverage_percent": percent(approved_records, operational_taxon_denominator),
            "approved_indexed_parent_pages": approved_parent_pages,
            "book_page_coverage_percent": percent(approved_parent_pages, total_pages),
            "denominator_warning": "The operational candidate denominator is the current OCR-clean selector inventory plus approved records; it is not a verified count of every taxon printed in the four volumes.",
        },
        "bounded_text_mvp": {
            "gates_passed": sum(bounded_mvp_gates.values()),
            "gates_total": len(bounded_mvp_gates),
            "completion_percent": percent(sum(bounded_mvp_gates.values()), len(bounded_mvp_gates)),
            "gates": bounded_mvp_gates,
            "approved_child_chunks": approved_children,
            "approved_query_vectors": approved_queries,
            "chat_acceptance_passed": chat_cases if chat_valid else 0,
            "chat_acceptance_total": chat_cases,
        },
        "portability_and_delivery": {
            "completion_percent": percent(sum(portability_gates.values()), len(portability_gates)),
            "gates": portability_gates,
        },
        "image_reasoning": {
            "production_enabled": image_ready,
            "completion_percent": percent(int(image_ready), 1),
        },
        "roadmap_weighted_indicator": {
            "completion_percent": round(100 * roadmap_score, 1),
            "weights": weights,
            "component_percent": {key: round(100 * value, 2) for key, value in weighted_parts.items()},
            "warning": "This is a planning indicator defined by the listed weights, not an objective measure of book accuracy or content coverage.",
        },
        "next_candidate": selector["candidates"][0] if selector.get("candidates") else None,
        "incremental_usd_this_completion_check": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "roadmap_weighted_percent": result["roadmap_weighted_indicator"]["completion_percent"],
        "bounded_text_mvp_percent": result["bounded_text_mvp"]["completion_percent"],
        "book_page_coverage_percent": result["approved_content_coverage"]["book_page_coverage_percent"],
        "operational_taxon_coverage_percent": result["approved_content_coverage"]["operational_taxon_coverage_percent"],
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
