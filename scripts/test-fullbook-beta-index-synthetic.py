#!/usr/bin/env python3
"""No-API synthetic E2E test of the current full-book beta index builder."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
SOURCE_ROOT = LAB / "data/candidates/preembedding-v1"
BUILDER = LAB / "scripts/build-fullbook-beta-index.py"
VALIDATOR = LAB / "scripts/validate-fullbook-beta-index.py"
MAIN = LAB / "data/index/plant-embeddings.sqlite"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=300)


def make_checkpoint(path: Path, chunks: list[dict]) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE embedding_results (
          job_id TEXT PRIMARY KEY, chunk_id TEXT NOT NULL UNIQUE,
          job_sha256 TEXT NOT NULL, embedding_input_sha256 TEXT NOT NULL,
          model TEXT NOT NULL, dimensions INTEGER NOT NULL,
          vector_space_id TEXT NOT NULL, embedding_json TEXT NOT NULL,
          embedding_sha256 TEXT NOT NULL, embedded_at TEXT NOT NULL,
          http_batch_id TEXT NOT NULL
        );
        CREATE TABLE execution_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    # Deterministic unit vector; the test validates transport, coverage and
    # normalization contracts, not semantic quality.
    vector = json.dumps([1.0] + [0.0] * 767, separators=(",", ":"))
    rows = [(
        f"embed:{chunk['chunk_id']}", chunk["chunk_id"], "synthetic-job-sha256",
        chunk["embedding_input_sha256"], "gemini-embedding-2", 768,
        "gemini-embedding-2__768__qa-prefix-v1", vector, "synthetic-vector-sha256",
        "2026-08-13T00:00:00+08:00", "synthetic-no-api",
    ) for chunk in chunks]
    db.executemany("INSERT INTO embedding_results VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO execution_meta VALUES ('http_batch_calls','0')")
    db.commit()
    db.close()


def main() -> None:
    chunks = [
        json.loads(line) for line in
        (SOURCE_ROOT / "chunks-candidate/section-aware-512-100-v1.jsonl").read_text().splitlines()
        if line
    ]
    if not chunks:
        raise SystemExit("current staging chunks are empty")
    results: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="kohler-beta-synthetic-") as directory:
        root = Path(directory) / "root"
        (root / "chunks-candidate").mkdir(parents=True)
        (root / "embedding-results").mkdir(parents=True)
        shutil.copy2(
            SOURCE_ROOT / "chunks-candidate/section-aware-512-100-v1.jsonl",
            root / "chunks-candidate/section-aware-512-100-v1.jsonl",
        )
        shutil.copy2(SOURCE_ROOT / "chunks-candidate/manifest.json", root / "chunks-candidate/manifest.json")
        (root / "embedding-results/manifest.json").write_text(json.dumps({
            "schema_version": "1.0", "status": "complete",
            "updated_at": "2026-08-13T00:00:00+08:00",
            "manifest_sha256": "synthetic-no-api-manifest",
        }))
        make_checkpoint(root / "embedding-results/checkpoint.sqlite", chunks)
        beta = Path(directory) / "fullbook-beta.sqlite"
        build = run([
            "python3", str(BUILDER), "--root", str(root),
            "--approved-main", str(MAIN), "--output", str(beta),
        ])
        results["build"] = build.returncode == 0
        validate = run([
            "python3", str(VALIDATOR), "--root", str(root),
            "--approved-main", str(MAIN), "--beta", str(beta),
        ])
        results["validate"] = validate.returncode == 0
        if beta.exists():
            db = sqlite3.connect(beta)
            record_count = db.execute(
                "SELECT count(DISTINCT record_id) FROM embedding_chunks"
            ).fetchone()[0]
            metadata_count = db.execute("SELECT count(*) FROM record_name_metadata").fetchone()[0]
            fallback_count = db.execute(
                "SELECT count(*) FROM record_name_metadata "
                "WHERE display_name_source_scope='non_taiwan_traditional_fallback'"
            ).fetchone()[0]
            results["name_metadata_coverage"] = metadata_count == record_count
            results["non_taiwan_fallback_preserved"] = fallback_count >= 2
            first = db.execute(
                "SELECT chunk_id FROM embedding_chunks WHERE review_status='machine_extracted_beta' LIMIT 1"
            ).fetchone()
            if first:
                db.execute("UPDATE embedding_chunks SET source_text='tampered' WHERE chunk_id=?", first)
                db.commit()
            db.close()
            adversarial = run([
                "python3", str(VALIDATOR), "--root", str(root),
                "--approved-main", str(MAIN), "--beta", str(beta),
            ])
            results["source_drift_rejected"] = adversarial.returncode != 0
        else:
            results["source_drift_rejected"] = False
    result = {
        "status": "PASS" if all(results.values()) else "FAIL",
        "current_staging_chunks": len(chunks), "checks": results,
        "external_calls": 0,
    }
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
