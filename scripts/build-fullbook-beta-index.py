#!/usr/bin/env python3
"""Build a disposable full-book beta index from validated staging vectors.

The current approved subset is copied byte-for-byte at row level. New full-book
rows retain ``machine_extracted_beta`` status so the production index is not
silently broadened before retrieval and answer-gate acceptance.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
DEFAULT_MAIN = LAB / "data/index/plant-embeddings.sqlite"
DEFAULT_OUTPUT = LAB / "data/index/staging/plant-embeddings-fullbook-beta.sqlite"
PROFILE_ID = "section-aware-512-100-v1"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE embedding_chunks (
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
        );
        CREATE INDEX idx_embedding_chunks_active_profile_parent
          ON embedding_chunks(profile_id, review_status, parent_chunk_id);
        CREATE INDEX idx_embedding_chunks_source_page
          ON embedding_chunks(source_id, pdf_page);
        CREATE INDEX idx_embedding_chunks_record ON embedding_chunks(record_id);
        CREATE TABLE embedding_queries (
          query_id TEXT PRIMARY KEY,
          question TEXT NOT NULL,
          embedding_json TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          model TEXT NOT NULL,
          task_type TEXT NOT NULL,
          embedded_at TEXT NOT NULL,
          provider TEXT NOT NULL DEFAULT '',
          vector_space_id TEXT NOT NULL DEFAULT '',
          prompt_contract_version TEXT NOT NULL DEFAULT '',
          expected_status TEXT NOT NULL DEFAULT 'unspecified',
          review_status TEXT NOT NULL DEFAULT 'candidate',
          profile_id TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE embedding_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE index_build_provenance (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE record_name_metadata (
          record_id TEXT PRIMARY KEY,
          display_name TEXT,
          display_name_source_scope TEXT NOT NULL,
          accepted_scientific_name TEXT,
          book_scientific_name TEXT NOT NULL,
          naming_artifact_sha256 TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE embedding_chunks_fts USING fts5(
          chunk_id UNINDEXED, scientific_name, display_name, source_text,
          tokenize='unicode61 remove_diacritics 2'
        );
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--approved-main", type=Path, default=DEFAULT_MAIN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result_manifest = json.loads((args.root / "embedding-results/manifest.json").read_text())
    if result_manifest.get("status") != "complete":
        raise SystemExit("embedding results are not complete")
    profile = json.loads((LAB / "config/embedding-profile.json").read_text())
    chunks = {
        row["chunk_id"]: row for row in (
            json.loads(line) for line in
            (args.root / "chunks-candidate/section-aware-512-100-v1.jsonl").read_text().splitlines()
            if line
        )
    }
    result_db = sqlite3.connect(
        f"file:{args.root / 'embedding-results/checkpoint.sqlite'}?mode=ro", uri=True
    )
    result_db.row_factory = sqlite3.Row
    vectors = {row["chunk_id"]: row for row in result_db.execute("SELECT * FROM embedding_results")}
    if set(vectors) != set(chunks):
        raise SystemExit("vector/chunk coverage mismatch")
    source_manifest = json.loads((LAB / "data/source-manifest.json").read_text())
    filenames = {item["source_id"]: Path(item["path"]).name for item in source_manifest["files"]}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    db = sqlite3.connect(temporary)
    db.row_factory = sqlite3.Row
    create_schema(db)
    approved = sqlite3.connect(f"file:{args.approved_main}?mode=ro", uri=True)
    approved.row_factory = sqlite3.Row
    approved_columns = [item[1] for item in approved.execute("PRAGMA table_info(embedding_chunks)")]
    insert_columns = [item[1] for item in db.execute("PRAGMA table_info(embedding_chunks)")]
    if approved_columns != insert_columns:
        raise SystemExit("approved main schema drift")
    placeholders = ",".join("?" for _ in insert_columns)
    approved_rows = list(approved.execute(
        "SELECT * FROM embedding_chunks WHERE profile_id=? AND review_status='approved' ORDER BY chunk_id",
        (PROFILE_ID,),
    ))
    db.executemany(
        f"INSERT INTO embedding_chunks VALUES ({placeholders})",
        [tuple(row[column] for column in insert_columns) for row in approved_rows],
    )
    for row in db.execute(
        "SELECT record_id,MAX(display_name) display_name,MAX(scientific_name) scientific_name "
        "FROM embedding_chunks GROUP BY record_id"
    ):
        db.execute(
            "INSERT INTO record_name_metadata VALUES (?,?,?,?,?,?)",
            (row["record_id"], row["display_name"], "taiwan_public_name",
             row["scientific_name"], row["scientific_name"], "approved-baseline-record"),
        )
    query_columns = [item[1] for item in db.execute("PRAGMA table_info(embedding_queries)")]
    existing_query_columns = [item[1] for item in approved.execute("PRAGMA table_info(embedding_queries)")]
    if query_columns != existing_query_columns:
        raise SystemExit("approved query schema drift")
    query_rows = list(approved.execute("SELECT * FROM embedding_queries WHERE review_status='approved'"))
    db.executemany(
        f"INSERT INTO embedding_queries VALUES ({','.join('?' for _ in query_columns)})",
        [tuple(row[column] for column in query_columns) for row in query_rows],
    )

    embedded_at = result_manifest["updated_at"]
    candidate_name_metadata: dict[str, tuple[Any, ...]] = {}
    for chunk_id, chunk in sorted(chunks.items()):
        vector = vectors[chunk_id]
        if vector["job_sha256"] is None or vector["embedding_input_sha256"] != chunk["embedding_input_sha256"]:
            raise SystemExit(f"vector provenance mismatch: {chunk_id}")
        parent_id = (
            f"{chunk['entry_id']}:s{chunk['section_index']:02d}:"
            f"p{chunk['pdf_page']:04d}:x{chunk['source_span_index']:02d}"
        )
        record_id = "preembedding-" + chunk["entry_id"].replace(":", "-")
        metadata = (
            record_id, chunk["display_name_zh_tw"],
            chunk.get("display_name_source_scope", "unclassified_staging"),
            chunk["accepted_scientific_name"], chunk["book_taxon_candidate"],
            chunk["naming_artifact_sha256"],
        )
        if record_id in candidate_name_metadata and candidate_name_metadata[record_id] != metadata:
            raise SystemExit(f"inconsistent name metadata across chunks: {record_id}")
        candidate_name_metadata[record_id] = metadata
        values = (
            chunk_id, parent_id, PROFILE_ID, chunk["source_id"], filenames[chunk["source_id"]],
            chunk["volume"], chunk["pdf_page"], record_id,
            chunk["accepted_scientific_name"] or chunk["book_taxon_candidate"],
            chunk["display_name_zh_tw"], chunk["display_name_zh_tw"] or "", "text",
            "section-aware-child" if chunk["token_unit_count"] >= chunk["target_token_units"] else "section-within-target",
            chunk["source_span_sha256"], chunk["text_sha256"], chunk["source_text"],
            chunk["target_token_units"], chunk["overlap_token_units"],
            chunk["token_unit_start"], chunk["token_unit_end"],
            chunk["source_quote_char_start"], chunk["source_quote_char_end"],
            vector["embedding_json"], vector["dimensions"], vector["model"],
            "prompt_prefix_document", embedded_at, "machine_extracted_beta",
            profile["provider"], vector["vector_space_id"], profile["prompt_contract"]["version"],
        )
        db.execute(f"INSERT INTO embedding_chunks VALUES ({placeholders})", values)

    db.executemany(
        "INSERT INTO record_name_metadata VALUES (?,?,?,?,?,?)",
        [candidate_name_metadata[key] for key in sorted(candidate_name_metadata)],
    )

    db.execute(
        "INSERT INTO embedding_chunks_fts(chunk_id,scientific_name,display_name,source_text) "
        "SELECT chunk_id,scientific_name,COALESCE(display_name,''),source_text FROM embedding_chunks"
    )
    meta = {
        "schema_version": "3-beta",
        "active_chunk_profile": PROFILE_ID,
        "active_review_statuses": "approved,machine_extracted_beta",
        "model": profile["model"], "dimensions": str(profile["dimensions"]),
        "provider": profile["provider"], "vector_space_id": profile["vector_space_id"],
        "prompt_contract_version": profile["prompt_contract"]["version"],
        "retrieval_grouping": "max_hybrid_score_per_parent_chunk_id_before_top_k",
        "lexical_index": "embedding_chunks_fts/fts5",
        "updated_at": now(),
    }
    db.executemany("INSERT INTO embedding_meta VALUES (?,?)", meta.items())
    provenance = {
        "approved_main_sha256": sha256_file(args.approved_main),
        "approved_rows": str(len(approved_rows)),
        "staging_chunk_manifest_sha256": json.loads(
            (args.root / "chunks-candidate/manifest.json").read_text()
        )["summary_sha256"],
        "embedding_result_manifest_sha256": result_manifest["manifest_sha256"],
        "beta_rows": str(len(chunks)),
        "build_status": "staging_not_promoted",
    }
    db.executemany("INSERT INTO index_build_provenance VALUES (?,?)", provenance.items())
    db.commit()
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit(f"temporary index integrity failed: {integrity}")
    db.execute("VACUUM")
    db.close(); approved.close(); result_db.close()
    os.replace(temporary, args.output)
    try:
        database_label = str(args.output.relative_to(LAB))
    except ValueError:
        database_label = str(args.output)
    report = {
        "schema_version": "1.0", "built_at": now(), "status": "staging_not_promoted",
        "database": database_label, "approved_rows": len(approved_rows),
        "beta_rows": len(chunks), "total_rows": len(approved_rows) + len(chunks),
        "database_sha256": sha256_file(args.output), "canonical_main_modified": False,
    }
    report_path = args.output.with_suffix(".manifest.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
