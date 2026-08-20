#!/usr/bin/env python3
"""Independently validate the main child-vector schema and parent-page collapse contract."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sqlite3
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
SELECTED_PROFILE = "section-aware-512-100-v1"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def load_retriever():
    path = LAB / "scripts/query-main-index.py"
    spec = importlib.util.spec_from_file_location("query_main_index", path)
    if spec is None or spec.loader is None:
        fail("cannot load query-main-index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.retrieve_collapsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    parser.add_argument(
        "--experiment-db", type=Path,
        default=LAB / "data/index/experiments/podophyllum-chunking-ab.sqlite",
    )
    parser.add_argument(
        "--chunks-jsonl", type=Path,
        default=LAB / "data/chunks/podophyllum-peltatum-section-aware-512-100-v1.jsonl",
    )
    parser.add_argument("--tests", type=Path, default=LAB / "data/tests/podophyllum-chunking-ab.json")
    parser.add_argument("--profile", type=Path, default=LAB / "config/embedding-profile.json")
    parser.add_argument("--manifest", type=Path, default=LAB / "data/source-manifest.json")
    parser.add_argument(
        "--migration-report", type=Path,
        default=LAB / "reports/main-index-child-migration-podophyllum-2026-08-11.json",
    )
    args = parser.parse_args()

    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    tests = json.loads(args.tests.read_text(encoding="utf-8"))
    report = json.loads(args.migration_report.read_text(encoding="utf-8"))
    canonical = {
        item["chunk_id"]: item
        for line in args.chunks_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for item in [json.loads(line)]
    }
    canonical_record_ids = {item["record_id"] for item in canonical.values()}
    if len(canonical_record_ids) != 1:
        fail("canonical fixture must contain exactly one record_id")
    canonical_record_id = next(iter(canonical_record_ids))
    if report.get("external_model_calls") != 0 or report.get("incremental_usd") != 0:
        fail("migration used an external model or non-zero cost")
    if report.get("execution_count", 0) < 2 or not report.get("schema_migrated_ever"):
        fail("idempotent second migration run was not recorded")
    if report.get("selected_profile") != SELECTED_PROFILE or len(canonical) != 4:
        fail("selected profile or canonical child count mismatch")

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    columns = {row[1] for row in connection.execute("PRAGMA table_info(embedding_chunks)")}
    for required in (
        "parent_chunk_id", "profile_id", "evidence_type", "parent_text_sha256",
        "token_unit_start", "token_unit_end", "char_start", "char_end",
    ):
        if required not in columns:
            fail(f"main schema lacks {required}")
    for index in connection.execute("PRAGMA index_list(embedding_chunks)"):
        if not index[2]:
            continue
        index_columns = [row[2] for row in connection.execute(f"PRAGMA index_info('{index[1]}')")]
        if {"source_id", "pdf_page", "record_id"}.issubset(index_columns):
            fail("legacy one-vector-per-page uniqueness still exists")
    meta = dict(connection.execute("SELECT key, value FROM embedding_meta"))
    if meta.get("schema_version") != "2" or meta.get("active_chunk_profile") != SELECTED_PROFILE:
        fail("main index active profile metadata mismatch")
    if meta.get("retrieval_grouping") != "max_hybrid_score_per_parent_chunk_id_before_top_k":
        fail("parent collapse policy metadata mismatch")

    rows = connection.execute(
        "SELECT * FROM embedding_chunks WHERE profile_id=? AND review_status='approved' AND record_id=? ORDER BY chunk_id",
        (SELECTED_PROFILE, canonical_record_id),
    ).fetchall()
    if {row["chunk_id"] for row in rows} != set(canonical):
        fail("approved main child rows differ from canonical child set")
    if len({row["parent_chunk_id"] for row in rows}) != 2:
        fail("four child chunks do not map to exactly two parent pages")
    if connection.execute(
        "SELECT count(*) FROM embedding_chunks WHERE profile_id='page-baseline'"
    ).fetchone()[0] != 2:
        fail("page baseline rows were not preserved")

    source = sqlite3.connect(f"file:{LAB / 'data/fulltext/kohler-pages.sqlite'}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    experiment = sqlite3.connect(f"file:{args.experiment_db}?mode=ro", uri=True)
    experiment.row_factory = sqlite3.Row
    for row in rows:
        chunk = canonical[row["chunk_id"]]
        parent = source.execute(
            "SELECT best_text FROM pages WHERE source_id=? AND pdf_page=?",
            (row["source_id"], row["pdf_page"]),
        ).fetchone()
        if parent is None:
            fail(f"source parent unavailable: {row['chunk_id']}")
        parent_text = parent["best_text"].strip()
        if hashlib.sha256(parent_text.encode()).hexdigest() != row["parent_text_sha256"]:
            fail(f"parent source hash mismatch: {row['chunk_id']}")
        if parent_text[row["char_start"]:row["char_end"]] != row["source_text"]:
            fail(f"child is not the declared parent substring: {row['chunk_id']}")
        if hashlib.sha256(row["source_text"].encode()).hexdigest() != row["text_sha256"]:
            fail(f"child source hash mismatch: {row['chunk_id']}")
        if row["evidence_type"] != "text" or row["display_name"] != "盾葉鬼臼":
            fail(f"evidence or Taiwan display metadata mismatch: {row['chunk_id']}")
        expected = experiment.execute(
            "SELECT embedding_json, vector_space_id FROM chunks WHERE profile_id=? AND chunk_id=? AND review_status='approved'",
            (SELECTED_PROFILE, row["chunk_id"]),
        ).fetchone()
        if expected is None or json.loads(expected["embedding_json"]) != json.loads(row["embedding_json"]):
            fail(f"experiment vector changed during migration: {row['chunk_id']}")
        vector = json.loads(row["embedding_json"])
        norm = math.sqrt(sum(value * value for value in vector))
        if len(vector) != int(profile["dimensions"]) or not 0.98 <= norm <= 1.02:
            fail(f"invalid child vector: {row['chunk_id']}")
        if row["vector_space_id"] != profile["vector_space_id"]:
            fail(f"vector space mismatch: {row['chunk_id']}")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    volume = next(item for item in manifest["files"] if item["source_id"] == "kohler-volume-1")
    source_path = Path(volume["path"])
    if not source_path.is_file() or source_path.stat().st_size != volume["bytes"]:
        fail("source PDF is unavailable or its byte size changed")

    retrieve = load_retriever()
    global_raw_count = connection.execute(
        "SELECT count(*) FROM embedding_chunks WHERE profile_id=? AND review_status='approved'",
        (SELECTED_PROFILE,),
    ).fetchone()[0]
    global_parent_count = connection.execute(
        "SELECT count(DISTINCT parent_chunk_id) FROM embedding_chunks WHERE profile_id=? AND review_status='approved'",
        (SELECTED_PROFILE,),
    ).fetchone()[0]
    verdicts = []
    for fixture in tests["queries"]:
        result = retrieve(args.database, args.tests, fixture["query_id"])
        collapsed = result["collapsed_parent_hits"]
        if result["raw_hit_count"] != global_raw_count or result["collapsed_hit_count"] != global_parent_count:
            fail(f"parent collapse count mismatch: {fixture['query_id']}")
        if len({item["parent_chunk_id"] for item in collapsed}) != len(collapsed):
            fail(f"duplicate parent survived collapse: {fixture['query_id']}")
        if any(not item.get("citation", {}).get("source_id") or not item["citation"].get("pdf_page") for item in collapsed):
            fail(f"collapsed result lacks citation: {fixture['query_id']}")
        if fixture["expected_status"] == "answerable":
            if result["answer_gate"] != "supporting_book_terms_found" or collapsed[0]["pdf_page"] != 192:
                fail(f"answerable query lost page-192 evidence: {fixture['query_id']}")
        elif result["answer_gate"] != "no_supporting_book_relation":
            fail(f"unanswerable query passed the evidence gate: {fixture['query_id']}")
        verdicts.append({
            "query_id": fixture["query_id"],
            "answer_gate": result["answer_gate"],
            "top_parent_page": collapsed[0]["pdf_page"],
            "duplicates_removed": result["duplicate_parent_hits_removed"],
        })

    connection.close()
    experiment.close()
    source.close()
    print(json.dumps({
        "valid": True,
        "schema_version": 2,
        "selected_profile": SELECTED_PROFILE,
        "approved_child_chunks": len(rows),
        "parent_pages": 2,
        "legacy_page_rows_preserved": 2,
        "one_vector_per_page_uniqueness_removed": True,
        "parent_page_collapse": True,
        "source_pdf_unchanged": True,
        "idempotent_migration_runs": report["execution_count"],
        "external_model_calls": 0,
        "incremental_usd": 0,
        "verdicts": verdicts,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
