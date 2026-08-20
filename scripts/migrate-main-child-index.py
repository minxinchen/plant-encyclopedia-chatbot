#!/usr/bin/env python3
"""Promote an approved child-chunk experiment into the rebuildable main index.

author: Codex (GPT-5)
date: 2026-08-11
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")
SELECTED_PROFILE = "section-aware-512-100-v1"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def create_chunk_table(connection: sqlite3.Connection, table: str = "embedding_chunks") -> None:
    connection.execute(
        f"""
        CREATE TABLE {table} (
          chunk_id TEXT PRIMARY KEY,
          parent_chunk_id TEXT NOT NULL,
          profile_id TEXT NOT NULL,
          source_id TEXT NOT NULL,
          source_filename TEXT NOT NULL DEFAULT '',
          volume INTEGER NOT NULL,
          pdf_page INTEGER NOT NULL,
          record_id TEXT NOT NULL,
          scientific_name TEXT NOT NULL,
          display_name TEXT,
          embedding_input_display_name TEXT NOT NULL DEFAULT '',
          evidence_type TEXT NOT NULL DEFAULT 'text',
          chunk_kind TEXT NOT NULL,
          parent_text_sha256 TEXT,
          text_sha256 TEXT NOT NULL,
          source_text TEXT NOT NULL,
          target_token_units INTEGER,
          overlap_token_units INTEGER,
          token_unit_start INTEGER,
          token_unit_end INTEGER,
          char_start INTEGER,
          char_end INTEGER,
          embedding_json TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          model TEXT NOT NULL,
          task_type TEXT NOT NULL,
          embedded_at TEXT NOT NULL,
          review_status TEXT NOT NULL,
          provider TEXT NOT NULL DEFAULT '',
          vector_space_id TEXT NOT NULL DEFAULT '',
          prompt_contract_version TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        f"CREATE INDEX idx_{table}_active_profile_parent "
        f"ON {table}(profile_id, review_status, parent_chunk_id)"
    )
    connection.execute(
        f"CREATE INDEX idx_{table}_source_page ON {table}(source_id, pdf_page)"
    )


def migrate_chunk_schema(connection: sqlite3.Connection) -> bool:
    required = {
        "parent_chunk_id", "profile_id", "source_filename", "embedding_input_display_name",
        "evidence_type", "parent_text_sha256", "target_token_units", "overlap_token_units",
        "token_unit_start", "token_unit_end", "char_start", "char_end",
    }
    columns = {row[1] for row in connection.execute("PRAGMA table_info(embedding_chunks)")}
    if required.issubset(columns):
        return False
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("ALTER TABLE embedding_chunks RENAME TO embedding_chunks_legacy_v1")
        create_chunk_table(connection)
        connection.execute(
            """
            INSERT INTO embedding_chunks (
              chunk_id, parent_chunk_id, profile_id, source_id, volume, pdf_page, record_id,
              scientific_name, display_name, embedding_input_display_name, evidence_type,
              chunk_kind, parent_text_sha256, text_sha256, source_text, embedding_json,
              dimensions, model, task_type, embedded_at, review_status, provider,
              vector_space_id, prompt_contract_version
            )
            SELECT
              chunk_id, chunk_id, 'page-baseline', source_id, volume, pdf_page, record_id,
              scientific_name, display_name, COALESCE(display_name, ''), 'text', chunk_kind,
              text_sha256, text_sha256, source_text, embedding_json, dimensions, model,
              task_type, embedded_at, review_status, provider, vector_space_id,
              prompt_contract_version
            FROM embedding_chunks_legacy_v1
            """
        )
        connection.execute("DROP TABLE embedding_chunks_legacy_v1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-db", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    parser.add_argument(
        "--experiment-db", type=Path,
        default=LAB / "data/index/experiments/podophyllum-chunking-ab.sqlite",
    )
    parser.add_argument(
        "--chunks-jsonl", type=Path,
        default=LAB / "data/chunks/podophyllum-peltatum-section-aware-512-100-v1.jsonl",
    )
    parser.add_argument(
        "--tests", type=Path, default=LAB / "data/tests/podophyllum-chunking-ab.json"
    )
    parser.add_argument(
        "--experiment-report", type=Path,
        default=LAB / "reports/chunking-ab-podophyllum-2026-08-11.json",
    )
    parser.add_argument("--profile", type=Path, default=LAB / "config/embedding-profile.json")
    parser.add_argument(
        "--plant-record", type=Path, default=LAB / "data/records/podophyllum-peltatum.json"
    )
    parser.add_argument(
        "--report", type=Path,
        default=LAB / "reports/main-index-child-migration-podophyllum-2026-08-11.json",
    )
    args = parser.parse_args()

    profile = load_json(args.profile)
    tests = load_json(args.tests)
    experiment_report = load_json(args.experiment_report)
    plant_record = load_json(args.plant_record)
    if profile.get("chunking_profile", {}).get("profile_id") != SELECTED_PROFILE:
        raise SystemExit("selected embedding profile is not section-aware-512-100-v1")
    if experiment_report.get("profile_results", {}).get(SELECTED_PROFILE) is None:
        raise SystemExit("selected profile is absent from the approved experiment report")
    if experiment_report.get("incremental_usd") != 0 or experiment_report.get("paid_fallback_used"):
        raise SystemExit("experiment did not satisfy the zero-cost policy")

    canonical = {
        item["chunk_id"]: item
        for line in args.chunks_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for item in [json.loads(line)]
    }
    if not canonical or {item["profile_id"] for item in canonical.values()} != {SELECTED_PROFILE}:
        raise SystemExit("canonical selected-profile child set is empty or mixes profiles")
    canonical_record_ids = {item["record_id"] for item in canonical.values()}
    if len(canonical_record_ids) != 1:
        raise SystemExit("canonical selected-profile child set must contain one record_id")
    canonical_record_id = next(iter(canonical_record_ids))

    experiment = sqlite3.connect(f"file:{args.experiment_db}?mode=ro", uri=True)
    experiment.row_factory = sqlite3.Row
    approved = experiment.execute(
        "SELECT * FROM chunks WHERE profile_id=? AND review_status='approved' ORDER BY chunk_id",
        (SELECTED_PROFILE,),
    ).fetchall()
    if {row["chunk_id"] for row in approved} != set(canonical):
        raise SystemExit("approved experiment vectors do not match canonical child chunks")

    connection = sqlite3.connect(args.main_db)
    connection.row_factory = sqlite3.Row
    before_rows = connection.execute("SELECT count(*) FROM embedding_chunks").fetchone()[0]
    schema_migrated = migrate_chunk_schema(connection)
    ensure_column(connection, "embedding_queries", "expected_status", "TEXT NOT NULL DEFAULT 'unspecified'")
    ensure_column(connection, "embedding_queries", "review_status", "TEXT NOT NULL DEFAULT 'candidate'")
    ensure_column(connection, "embedding_queries", "profile_id", "TEXT NOT NULL DEFAULT ''")

    embedded_at = experiment_report["embedded_at"]
    display_name = plant_record["display_name"]
    by_id = {row["chunk_id"]: row for row in approved}
    for chunk_id, chunk in sorted(canonical.items()):
        row = by_id[chunk_id]
        if row["text_sha256"] != chunk["text_sha256"] or row["source_text"] != chunk["source_text"]:
            raise SystemExit(f"experiment/canonical source mismatch: {chunk_id}")
        connection.execute(
            """
            INSERT INTO embedding_chunks (
              chunk_id, parent_chunk_id, profile_id, source_id, source_filename, volume,
              pdf_page, record_id, scientific_name, display_name,
              embedding_input_display_name, evidence_type, chunk_kind, parent_text_sha256,
              text_sha256, source_text, target_token_units, overlap_token_units,
              token_unit_start, token_unit_end, char_start, char_end, embedding_json,
              dimensions, model, task_type, embedded_at, review_status, provider,
              vector_space_id, prompt_contract_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'text', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'prompt_prefix_document', ?, 'approved', ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
              parent_chunk_id=excluded.parent_chunk_id,
              profile_id=excluded.profile_id,
              source_filename=excluded.source_filename,
              display_name=excluded.display_name,
              embedding_input_display_name=excluded.embedding_input_display_name,
              parent_text_sha256=excluded.parent_text_sha256,
              text_sha256=excluded.text_sha256,
              source_text=excluded.source_text,
              target_token_units=excluded.target_token_units,
              overlap_token_units=excluded.overlap_token_units,
              token_unit_start=excluded.token_unit_start,
              token_unit_end=excluded.token_unit_end,
              char_start=excluded.char_start,
              char_end=excluded.char_end,
              embedding_json=excluded.embedding_json,
              dimensions=excluded.dimensions,
              model=excluded.model,
              embedded_at=excluded.embedded_at,
              review_status='approved',
              provider=excluded.provider,
              vector_space_id=excluded.vector_space_id,
              prompt_contract_version=excluded.prompt_contract_version
            """,
            (
                chunk_id, chunk["parent_chunk_id"], SELECTED_PROFILE, chunk["source_id"],
                chunk["source_filename"], chunk["volume"], chunk["pdf_page"], chunk["record_id"],
                chunk["scientific_name"], display_name, chunk.get("display_name", ""),
                chunk["chunk_kind"], chunk["parent_text_sha256"], chunk["text_sha256"],
                chunk["source_text"], chunk["target_token_units"], chunk["overlap_token_units"],
                chunk["token_unit_start"], chunk["token_unit_end"], chunk["char_start"],
                chunk["char_end"], row["embedding_json"], int(profile["dimensions"]),
                profile["model"], embedded_at, profile["provider"], profile["vector_space_id"],
                profile["prompt_contract"]["version"],
            ),
        )

    expected_status = {item["query_id"]: item["expected_status"] for item in tests["queries"]}
    experiment_queries = experiment.execute("SELECT * FROM queries ORDER BY query_id").fetchall()
    if {row["query_id"] for row in experiment_queries} != set(expected_status):
        raise SystemExit("experiment query set does not match the acceptance fixture")
    for row in experiment_queries:
        connection.execute(
            """
            INSERT INTO embedding_queries (
              query_id, question, embedding_json, dimensions, model, task_type, embedded_at,
              provider, vector_space_id, prompt_contract_version, expected_status,
              review_status, profile_id
            ) VALUES (?, ?, ?, ?, ?, 'prompt_prefix_question_answering', ?, ?, ?, ?, ?, 'approved', ?)
            ON CONFLICT(query_id) DO UPDATE SET
              question=excluded.question,
              embedding_json=excluded.embedding_json,
              dimensions=excluded.dimensions,
              model=excluded.model,
              task_type=excluded.task_type,
              embedded_at=excluded.embedded_at,
              provider=excluded.provider,
              vector_space_id=excluded.vector_space_id,
              prompt_contract_version=excluded.prompt_contract_version,
              expected_status=excluded.expected_status,
              review_status='approved',
              profile_id=excluded.profile_id
            """,
            (
                row["query_id"], row["question"], row["embedding_json"], int(profile["dimensions"]),
                profile["model"], embedded_at, profile["provider"], profile["vector_space_id"],
                profile["prompt_contract"]["version"], expected_status[row["query_id"]],
                SELECTED_PROFILE,
            ),
        )

    scope_rows = connection.execute(
        "SELECT source_id, record_id, min(pdf_page), max(pdf_page) FROM embedding_chunks "
        "WHERE profile_id=? AND review_status='approved' GROUP BY source_id, record_id ORDER BY source_id, record_id",
        (SELECTED_PROFILE,),
    ).fetchall()
    active_scope = "; ".join(
        f"{row[0]} {row[1]} PDF pages {row[2]}-{row[3]}" for row in scope_rows
    )
    now = datetime.now(TZ).isoformat(timespec="seconds")
    meta = {
        "schema_version": "2",
        "active_chunk_profile": SELECTED_PROFILE,
        "retrieval_grouping": "max_hybrid_score_per_parent_chunk_id_before_top_k",
        "active_profile_scope": active_scope,
        "updated_at": now,
    }
    connection.executemany(
        "INSERT INTO embedding_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        meta.items(),
    )
    connection.commit()
    after_rows = connection.execute("SELECT count(*) FROM embedding_chunks").fetchone()[0]
    child_rows = connection.execute(
        "SELECT count(*) FROM embedding_chunks WHERE profile_id=? AND review_status='approved'",
        (SELECTED_PROFILE,),
    ).fetchone()[0]
    parent_rows = connection.execute(
        "SELECT count(DISTINCT parent_chunk_id) FROM embedding_chunks WHERE profile_id=? AND review_status='approved'",
        (SELECTED_PROFILE,),
    ).fetchone()[0]
    contract_digest = hashlib.sha256(
        "\n".join(
            f"{item['chunk_id']}|{item['parent_chunk_id']}|{item['text_sha256']}|{profile['vector_space_id']}"
            for item in sorted(canonical.values(), key=lambda value: value["chunk_id"])
        ).encode("utf-8")
    ).hexdigest()
    previous = load_json(args.report) if args.report.is_file() else {}
    report = {
        "schema_version": "1.0",
        "run_id": f"main-index-child-migration-{canonical_record_id}-{now[:10]}",
        "updated_at": now,
        "execution_count": int(previous.get("execution_count", 0)) + 1,
        "schema_migrated_this_run": schema_migrated,
        "schema_migrated_ever": bool(previous.get("schema_migrated_ever")) or schema_migrated,
        "selected_profile": SELECTED_PROFILE,
        "vector_space_id": profile["vector_space_id"],
        "main_rows_before": before_rows,
        "main_rows_after": after_rows,
        "approved_child_rows": child_rows,
        "distinct_parent_pages": parent_rows,
        "approved_query_rows": len(experiment_queries),
        "contract_digest": contract_digest,
        "source_experiment_report": str(args.experiment_report.resolve()),
        "canonical_chunks_jsonl": str(args.chunks_jsonl.resolve()),
        "main_database": str(args.main_db.resolve()),
        "external_model_calls": 0,
        "incremental_usd": 0,
        "paid_fallback_used": False
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    connection.close()
    experiment.close()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
