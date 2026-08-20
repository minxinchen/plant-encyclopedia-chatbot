#!/usr/bin/env python3
"""Validate staging embedding checkpoint and portable export against frozen jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    jobs = {
        item["job_id"]: item for item in (
            json.loads(line) for line in
            (args.root / "embedding-jobs/gemini-embedding-jobs.jsonl").read_text().splitlines() if line
        )
    }
    result_dir = args.root / "embedding-results"
    db_path = result_dir / "checkpoint.sqlite"
    errors: list[str] = []
    if not db_path.exists():
        result = {"status": "WAITING", "jobs": len(jobs), "results": 0, "errors": []}
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(2 if args.require_complete else 0)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = list(db.execute("SELECT * FROM embedding_results ORDER BY job_id"))
    for row in rows:
        job = jobs.get(row["job_id"])
        if job is None:
            errors.append(f"unknown_job:{row['job_id']}")
            continue
        for field in ("chunk_id", "job_sha256", "embedding_input_sha256", "model",
                      "dimensions", "vector_space_id"):
            if row[field] != job[field]:
                errors.append(f"chain_drift:{row['job_id']}:{field}")
        try:
            vector = json.loads(row["embedding_json"])
        except json.JSONDecodeError:
            errors.append(f"invalid_vector_json:{row['job_id']}")
            continue
        if len(vector) != row["dimensions"] or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
            errors.append(f"invalid_vector:{row['job_id']}")
        if hashlib.sha256(row["embedding_json"].encode()).hexdigest() != row["embedding_sha256"]:
            errors.append(f"vector_hash_drift:{row['job_id']}")
        norm = math.sqrt(sum(float(v) * float(v) for v in vector))
        if not 0.95 <= norm <= 1.05:
            errors.append(f"unexpected_768_norm:{row['job_id']}:{norm:.6f}")
    if len({row["job_id"] for row in rows}) != len(rows):
        errors.append("duplicate_job_result")
    manifest_path = result_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        unhashed = dict(manifest)
        stored = unhashed.pop("manifest_sha256", None)
        if stored != canonical_hash(unhashed):
            errors.append("manifest_hash_drift")
        if manifest.get("completed_jobs") != len(rows) or manifest.get("expected_jobs") != len(jobs):
            errors.append("manifest_count_drift")
        if manifest.get("paid_fallback_used") is not False or manifest.get("incremental_usd") != 0:
            errors.append("zero_cost_policy_violation")
    complete = len(rows) == len(jobs) and not errors
    if args.require_complete and not complete:
        errors.append("results_incomplete")
    result = {
        "status": "PASS" if not errors else "FAIL",
        "jobs": len(jobs), "results": len(rows), "complete": complete, "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False))
    db.close()
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
