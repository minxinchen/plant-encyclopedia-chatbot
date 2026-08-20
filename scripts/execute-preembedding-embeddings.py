#!/usr/bin/env python3
"""Execute final Gemini embedding jobs with a resumable local checkpoint.

The default mode is read-only gate inspection.  ``--execute`` is deliberately
required before any external request can be made.  Results remain staging-only
until the separate full-book index rebuild validates and promotes them.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
DEFAULT_ENV = LAB.parents[1] / "secrets/plant-encyclopedia.env.local"
BLOCKING_INPUT_DISPOSITIONS = {
    "hold_page_quality",
    "hold_terminal_no_next_heading",
    "hold_span_over_limit",
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_env_value(path: Path, key: str) -> str:
    if os.environ.get(key, "").strip():
        return os.environ[key].strip()
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def finalization_gate(root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, observed: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "observed": observed})

    consolidated = read_json(root / "checks/consolidated-v2-summary.json")
    continuation = read_json(root / "checks/continuation-v2-integration-summary.json")
    recovery = read_json(root / "checks/recovery-v2-summary.json")
    promoted = read_json(root / "integration/embedding-ready-candidate-manifest.json")
    ocr = read_json(root / "consolidated-ocr-staging-manifest.json")["summary"]
    naming = read_json(root / "naming/checks/validation-latest.json")
    records = read_json(root / "records-candidate/manifest.json")
    chunks = read_json(root / "chunks-candidate/manifest.json")
    jobs = read_json(root / "embedding-jobs/manifest.json")
    source_validation = read_json(root / "checks/source-receipt-validation.json")

    expected_candidates = consolidated.get("total_candidates")
    check("consolidated_v2", consolidated.get("status") == "PASS", consolidated.get("status"))
    check("continuation_v2", continuation.get("complete") is True,
          {"complete": continuation.get("complete"), "passed": continuation.get("package_checks_passed")})
    check("recovery_v2", recovery.get("complete") is True,
          {"complete": recovery.get("complete"), "passed": recovery.get("package_checks_passed")})
    check("unresolved_content_holds", consolidated.get("unresolved_content_holds") == 0,
          consolidated.get("unresolved_content_holds"))
    check("promoted_staging_coverage", promoted.get("candidate_count") == expected_candidates,
          promoted.get("candidate_count"))
    check("ocr_complete", ocr.get("complete") is True and ocr.get("invalid_terminal_count") == 0,
          {"complete": ocr.get("complete"), "pending": ocr.get("pending_count"),
           "invalid": ocr.get("invalid_terminal_count")})
    check("ocr_lane_qualified", ocr.get("qualification_status") == "pass",
          ocr.get("qualification_status"))
    check("naming_complete", naming.get("valid") is True
          and naming.get("eligible_embedding_ready_candidates") == expected_candidates
          and naming.get("artifacts") == expected_candidates,
          {"valid": naming.get("valid"), "eligible": naming.get("eligible_embedding_ready_candidates"),
           "artifacts": naming.get("artifacts")})
    check("records_complete", records.get("source_candidate_count") == expected_candidates
          and records.get("record_count") == expected_candidates
          and not records.get("missing_naming_entry_ids"), records.get("record_count"))
    check("chunks_complete", chunks.get("source_candidate_count") == expected_candidates
          and chunks.get("entry_count") == expected_candidates
          and chunks.get("chunk_count", 0) > 0
          and not chunks.get("missing_naming_entry_ids"), chunks.get("chunk_count"))
    check("embedding_jobs_complete", jobs.get("status") == "planned_no_external_calls"
          and jobs.get("job_count") == chunks.get("chunk_count")
          and jobs.get("chunk_manifest_sha256") == chunks.get("summary_sha256"),
          {"status": jobs.get("status"), "jobs": jobs.get("job_count")})
    check("source_pdf_full_hash", source_validation.get("full_hash_verified") is True,
          source_validation.get("full_hash_verified"))
    return {
        "schema_version": "1.0",
        "checked_at": now(),
        "ready": all(item["passed"] for item in checks),
        "checks": checks,
    }


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS embedding_results (
          job_id TEXT PRIMARY KEY,
          chunk_id TEXT NOT NULL UNIQUE,
          job_sha256 TEXT NOT NULL,
          embedding_input_sha256 TEXT NOT NULL,
          model TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          vector_space_id TEXT NOT NULL,
          embedding_json TEXT NOT NULL,
          embedding_sha256 TEXT NOT NULL,
          embedded_at TEXT NOT NULL,
          http_batch_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS execution_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )


def batch_embed(api_key: str, model: str, dimensions: int, jobs: list[dict[str, Any]]) -> list[list[float]]:
    requests = [{
        "model": f"models/{model}",
        "content": {"parts": [{"text": item["embedding_input"]}]},
        "outputDimensionality": dimensions,
    } for item in jobs]
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents",
        data=json.dumps({"requests": requests}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(jobs):
        raise RuntimeError("Gemini batch response count mismatch")
    vectors: list[list[float]] = []
    for item in embeddings:
        values = item.get("values")
        if not isinstance(values, list) or len(values) != dimensions:
            raise RuntimeError("Gemini batch response dimensions mismatch")
        vector = [float(value) for value in values]
        if not all(math.isfinite(value) for value in vector):
            raise RuntimeError("Gemini batch response contains non-finite values")
        vectors.append(vector)
    return vectors


def export_results(root: Path, db: sqlite3.Connection, expected_jobs: int, http_calls: int,
                   complete: bool) -> dict[str, Any]:
    output_dir = root / "embedding-results"
    rows = db.execute("SELECT * FROM embedding_results ORDER BY job_id").fetchall()
    columns = [item[0] for item in db.execute("SELECT * FROM embedding_results LIMIT 0").description]
    output = output_dir / "gemini-embedding-results.jsonl"
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            item = dict(zip(columns, row))
            item["embedding"] = json.loads(item.pop("embedding_json"))
            stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(output)
    manifest = {
        "schema_version": "1.0",
        "updated_at": now(),
        "status": "complete" if complete else "in_progress",
        "expected_jobs": expected_jobs,
        "completed_jobs": len(rows),
        "http_batch_calls": http_calls,
        "incremental_usd": 0,
        "paid_fallback_used": False,
        "checkpoint_database": "checkpoint.sqlite",
        "portable_export": "gemini-embedding-results.jsonl",
        "results_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    path = output_dir / "manifest.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--max-http-requests", type=int, default=100)
    parser.add_argument("--max-retries", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 50:
        raise SystemExit("batch-size must be between 1 and 50")

    gate = finalization_gate(args.root)
    if not args.execute:
        print(json.dumps(gate, ensure_ascii=False))
        raise SystemExit(0 if gate["ready"] else 2)
    if not gate["ready"]:
        raise SystemExit("finalization gate is not ready")

    validate = subprocess.run(
        ["python3", str(LAB / "scripts/validate-preembedding-embedding-jobs.py"),
         "--root", str(args.root)], cwd=LAB, capture_output=True, text=True,
    )
    if validate.returncode:
        raise SystemExit(f"embedding job validation failed: {validate.stdout or validate.stderr}")
    jobs = read_jsonl(args.root / "embedding-jobs/gemini-embedding-jobs.jsonl")
    profile = read_json(LAB / "config/embedding-profile.json")
    if any(
        item["model"] != profile["model"]
        or item["dimensions"] != profile["dimensions"]
        or item["vector_space_id"] != profile["vector_space_id"]
        for item in jobs
    ):
        raise SystemExit("embedding job profile drift")
    api_key = load_env_value(args.env_file, "GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not loaded")

    output_dir = args.root / "embedding-results"
    output_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output_dir / "checkpoint.sqlite")
    create_schema(db)
    existing = {
        row[0]: row[1] for row in db.execute("SELECT job_id, job_sha256 FROM embedding_results")
    }
    drift = [item["job_id"] for item in jobs if item["job_id"] in existing and existing[item["job_id"]] != item["job_sha256"]]
    if drift:
        raise SystemExit(f"checkpoint job drift; rebuild a new vector space: {drift[:3]}")
    pending = [item for item in jobs if item["job_id"] not in existing]
    prior_calls = int(dict(db.execute("SELECT key, value FROM execution_meta")).get("http_batch_calls", "0"))
    calls_this_run = 0

    while pending:
        if prior_calls + calls_this_run >= args.max_http_requests:
            manifest = export_results(args.root, db, len(jobs), prior_calls + calls_this_run, False)
            print(json.dumps({**manifest, "pause_reason": "max_http_requests"}, ensure_ascii=False))
            raise SystemExit(3)
        batch = pending[:args.batch_size]
        vectors = None
        last_error = ""
        for attempt in range(args.max_retries + 1):
            try:
                vectors = batch_embed(api_key, profile["model"], int(profile["dimensions"]), batch)
                calls_this_run += 1
                break
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:600]
                last_error = f"HTTP {error.code}: {detail}"
                if error.code not in {429, 500, 502, 503, 504} or attempt == args.max_retries:
                    break
            except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                last_error = str(error)
                if attempt == args.max_retries:
                    break
            time.sleep(min(60, (2 ** attempt) + random.random()))
        if vectors is None:
            manifest = export_results(args.root, db, len(jobs), prior_calls + calls_this_run, False)
            print(json.dumps({**manifest, "pause_reason": "request_error", "error": last_error}, ensure_ascii=False))
            raise SystemExit(4)

        embedded_at = now()
        batch_id = sha256_text("|".join(item["job_id"] for item in batch))[:20]
        with db:
            for item, vector in zip(batch, vectors):
                vector_json = json.dumps(vector, separators=(",", ":"))
                db.execute(
                    "INSERT INTO embedding_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (item["job_id"], item["chunk_id"], item["job_sha256"],
                     item["embedding_input_sha256"], item["model"], item["dimensions"],
                     item["vector_space_id"], vector_json, sha256_text(vector_json),
                     embedded_at, batch_id),
                )
            db.execute(
                "INSERT INTO execution_meta(key,value) VALUES('http_batch_calls',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(prior_calls + calls_this_run),),
            )
        pending = pending[len(batch):]
        export_results(args.root, db, len(jobs), prior_calls + calls_this_run, not pending)

    manifest = export_results(args.root, db, len(jobs), prior_calls + calls_this_run, True)
    db.close()
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
