#!/usr/bin/env python3
"""Validate full-book beta index provenance, row coverage and vector contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
DEFAULT_MAIN = LAB / "data/index/plant-embeddings.sqlite"
DEFAULT_BETA = LAB / "data/index/staging/plant-embeddings-fullbook-beta.sqlite"
PROFILE = "section-aware-512-100-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--approved-main", type=Path, default=DEFAULT_MAIN)
    parser.add_argument("--beta", type=Path, default=DEFAULT_BETA)
    args = parser.parse_args()
    errors: list[str] = []
    if not args.beta.exists():
        raise SystemExit("beta index does not exist")
    chunks = {
        row["chunk_id"]: row for row in (
            json.loads(line) for line in
            (args.root / "chunks-candidate/section-aware-512-100-v1.jsonl").read_text().splitlines()
            if line
        )
    }
    approved = sqlite3.connect(f"file:{args.approved_main}?mode=ro", uri=True)
    approved.row_factory = sqlite3.Row
    expected_approved = {
        row["chunk_id"]: row for row in approved.execute(
            "SELECT * FROM embedding_chunks WHERE profile_id=? AND review_status='approved'", (PROFILE,)
        )
    }
    beta = sqlite3.connect(f"file:{args.beta}?mode=ro", uri=True)
    beta.row_factory = sqlite3.Row
    if beta.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        errors.append("sqlite_integrity")
    rows = {row["chunk_id"]: row for row in beta.execute("SELECT * FROM embedding_chunks")}
    approved_rows = {key: row for key, row in rows.items() if row["review_status"] == "approved"}
    candidate_rows = {key: row for key, row in rows.items() if row["review_status"] == "machine_extracted_beta"}
    if set(approved_rows) != set(expected_approved):
        errors.append("approved_row_coverage")
    for key, expected in expected_approved.items():
        actual = approved_rows.get(key)
        if actual and any(actual[field] != expected[field] for field in ("text_sha256", "embedding_json", "vector_space_id")):
            errors.append(f"approved_row_drift:{key}")
    if set(candidate_rows) != set(chunks):
        errors.append("beta_chunk_coverage")
    for key, chunk in chunks.items():
        row = candidate_rows.get(key)
        if row is None:
            continue
        if row["text_sha256"] != chunk["text_sha256"] or row["source_text"] != chunk["source_text"]:
            errors.append(f"source_drift:{key}")
        if row["profile_id"] != PROFILE or row["vector_space_id"] != "gemini-embedding-2__768__qa-prefix-v1":
            errors.append(f"vector_space_drift:{key}")
        vector = json.loads(row["embedding_json"])
        norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        if len(vector) != 768 or not 0.95 <= norm <= 1.05:
            errors.append(f"vector_invalid:{key}")
    fts_count = beta.execute("SELECT count(*) FROM embedding_chunks_fts").fetchone()[0]
    if fts_count != len(rows):
        errors.append("fts_coverage")
    has_name_metadata = beta.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='record_name_metadata'"
    ).fetchone() is not None
    if not has_name_metadata:
        errors.append("record_name_metadata_missing")
        name_rows = {}
    else:
        name_rows = {
            row["record_id"]: row for row in beta.execute("SELECT * FROM record_name_metadata")
        }
        expected_record_ids = {row["record_id"] for row in rows.values()}
        if set(name_rows) != expected_record_ids:
            errors.append("record_name_metadata_coverage")
        for chunk in chunks.values():
            record_id = "preembedding-" + chunk["entry_id"].replace(":", "-")
            metadata = name_rows.get(record_id)
            if metadata is None:
                continue
            expected_scope = chunk.get("display_name_source_scope", "unclassified_staging")
            if (
                metadata["display_name"] != chunk.get("display_name_zh_tw")
                or metadata["display_name_source_scope"] != expected_scope
                or metadata["accepted_scientific_name"] != chunk.get("accepted_scientific_name")
                or metadata["book_scientific_name"] != chunk.get("book_taxon_candidate")
                or metadata["naming_artifact_sha256"] != chunk.get("naming_artifact_sha256")
            ):
                errors.append(f"record_name_metadata_drift:{record_id}")
    meta = dict(beta.execute("SELECT key,value FROM embedding_meta"))
    if meta.get("active_review_statuses") != "approved,machine_extracted_beta":
        errors.append("active_status_contract")
    provenance = dict(beta.execute("SELECT key,value FROM index_build_provenance"))
    if provenance.get("approved_main_sha256") != sha256_file(args.approved_main):
        errors.append("approved_main_hash_drift")
    result = {
        "status": "PASS" if not errors else "FAIL", "approved_rows": len(approved_rows),
        "beta_rows": len(candidate_rows), "total_rows": len(rows), "fts_rows": fts_count,
        "name_metadata_rows": len(name_rows),
        "canonical_main_modified": False, "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False))
    beta.close(); approved.close()
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
