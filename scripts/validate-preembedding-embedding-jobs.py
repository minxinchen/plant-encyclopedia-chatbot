#!/usr/bin/env python3
"""Validate portable, vector-free embedding job staging."""

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    chunks = {
        row["chunk_id"]: row
        for row in (
            json.loads(line)
            for line in (args.root / "chunks-candidate/section-aware-512-100-v1.jsonl")
            .read_text(encoding="utf-8").splitlines() if line
        )
    }
    chunk_manifest = json.loads((args.root / "chunks-candidate/manifest.json").read_text(encoding="utf-8"))
    jobs_path = args.root / "embedding-jobs/gemini-embedding-jobs.jsonl"
    jobs = [json.loads(line) for line in jobs_path.read_text(encoding="utf-8").splitlines() if line]
    manifest = json.loads((args.root / "embedding-jobs/manifest.json").read_text(encoding="utf-8"))
    errors = []
    counts = Counter(job["chunk_id"] for job in jobs)
    errors.extend(f"duplicate_chunk_job:{key}" for key, count in counts.items() if count > 1)
    if set(counts) != set(chunks):
        errors.append("job_chunk_coverage_mismatch")
    for job in jobs:
        chunk = chunks.get(job["chunk_id"])
        if chunk is None:
            errors.append(f"unknown_chunk:{job['chunk_id']}")
            continue
        if job["chunk_sha256"] != chunk["chunk_sha256"]:
            errors.append(f"chunk_chain_mismatch:{job['chunk_id']}")
        if sha256_text(job["embedding_input"]) != job["embedding_input_sha256"]:
            errors.append(f"embedding_input_hash_mismatch:{job['chunk_id']}")
        if job["embedding_input_sha256"] != chunk["embedding_input_sha256"]:
            errors.append(f"embedding_input_chain_mismatch:{job['chunk_id']}")
        stored = job.get("job_sha256")
        unhashed = dict(job)
        unhashed.pop("job_sha256", None)
        if stored != sha256_json(unhashed):
            errors.append(f"job_hash_mismatch:{job['chunk_id']}")
        if job.get("status") != "planned" or job.get("embedding") is not None:
            errors.append(f"premature_vector_state:{job['chunk_id']}")
        if job.get("external_call_performed") or job.get("incremental_usd") != 0:
            errors.append(f"unexpected_external_call:{job['chunk_id']}")
        for field in ("model", "dimensions", "vector_space_id"):
            if job.get(field) != manifest.get(field):
                errors.append(f"profile_drift:{job['chunk_id']}:{field}")
    if manifest["job_count"] != len(jobs) or manifest["job_count"] != chunk_manifest["chunk_count"]:
        errors.append("manifest_count_mismatch")
    if manifest["chunk_manifest_sha256"] != chunk_manifest["summary_sha256"]:
        errors.append("chunk_manifest_chain_mismatch")
    if manifest.get("status") != "planned_no_external_calls" or manifest.get("external_calls") != 0:
        errors.append("manifest_premature_external_state")
    unhashed_manifest = dict(manifest)
    stored_manifest_hash = unhashed_manifest.pop("manifest_sha256", None)
    if stored_manifest_hash != sha256_json(unhashed_manifest):
        errors.append("manifest_hash_mismatch")
    result = {
        "status": "PASS" if not errors else "FAIL",
        "chunks": len(chunks),
        "jobs": len(jobs),
        "external_calls": manifest.get("external_calls"),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
