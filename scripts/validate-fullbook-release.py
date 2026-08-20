#!/usr/bin/env python3
"""Validate the promoted full-book main index and all portable release gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_MAIN = LAB / "data/index/plant-embeddings.sqlite"
DEFAULT_PROMOTION = LAB / "reports/fullbook-main-index-promotion.json"
DEFAULT_ACCEPTANCE = LAB / "reports/fullbook-beta-chat-acceptance.json"
DEFAULT_REPORT = LAB / "reports/fullbook-release-validation.json"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=1800)
    return {
        "passed": result.returncode == 0,
        "exit_code": result.returncode,
        "output": (result.stdout or result.stderr).strip()[-5000:],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main", type=Path, default=DEFAULT_MAIN)
    parser.add_argument("--promotion", type=Path, default=DEFAULT_PROMOTION)
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_ACCEPTANCE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, observed: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "observed": observed})

    check("main_exists", args.main.is_file(), str(args.main))
    check("promotion_report_exists", args.promotion.is_file(), str(args.promotion))
    check("acceptance_report_exists", args.acceptance.is_file(), str(args.acceptance))
    promotion = json.loads(args.promotion.read_text()) if args.promotion.is_file() else {}
    acceptance = json.loads(args.acceptance.read_text()) if args.acceptance.is_file() else {}
    main_hash = sha256_file(args.main) if args.main.is_file() else None
    check("promotion_pass", promotion.get("status") == "PASS", promotion.get("status"))
    check("promotion_main_hash", main_hash is not None and promotion.get("new_main_sha256") == main_hash,
          {"main": main_hash, "promotion": promotion.get("new_main_sha256")})
    check("acceptance_pass", acceptance.get("status") == "PASS", acceptance.get("status"))
    check("accepted_database_hash", main_hash is not None and acceptance.get("database_sha256") == main_hash,
          {"main": main_hash, "acceptance": acceptance.get("database_sha256")})
    check("bounded_free_tier_calls", acceptance.get("external_embedding_calls") == 6
          and acceptance.get("external_generation_calls") == 4
          and acceptance.get("incremental_usd") == 0
          and acceptance.get("paid_fallback_used") is False,
          {key: acceptance.get(key) for key in (
              "external_embedding_calls", "external_generation_calls", "incremental_usd", "paid_fallback_used"
          )})

    if args.main.is_file():
        db = sqlite3.connect(f"file:{args.main}?mode=ro", uri=True)
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        meta = dict(db.execute("SELECT key,value FROM embedding_meta"))
        total = db.execute("SELECT count(*) FROM embedding_chunks").fetchone()[0]
        approved = db.execute("SELECT count(*) FROM embedding_chunks WHERE review_status='approved'").fetchone()[0]
        beta = db.execute("SELECT count(*) FROM embedding_chunks WHERE review_status='machine_extracted_beta'").fetchone()[0]
        has_fts = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedding_chunks_fts'"
        ).fetchone() is not None
        fts = db.execute("SELECT count(*) FROM embedding_chunks_fts").fetchone()[0] if has_fts else 0
        records = db.execute("SELECT count(DISTINCT record_id) FROM embedding_chunks").fetchone()[0]
        has_name_metadata = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='record_name_metadata'"
        ).fetchone() is not None
        name_metadata_rows = db.execute("SELECT count(*) FROM record_name_metadata").fetchone()[0] if has_name_metadata else 0
        unclassified_name_rows = db.execute(
            "SELECT count(*) FROM record_name_metadata WHERE display_name_source_scope='unclassified_staging'"
        ).fetchone()[0] if has_name_metadata else 0
        db.close()
        check("sqlite_integrity", integrity == "ok", integrity)
        check("fts_coverage", has_fts and fts == total, {"table_exists": has_fts, "fts": fts, "total": total})
        check("active_profile", meta.get("active_chunk_profile") == "section-aware-512-100-v1",
              meta.get("active_chunk_profile"))
        check("active_review_statuses", meta.get("active_review_statuses") == "approved,machine_extracted_beta",
              meta.get("active_review_statuses"))
        check("promotion_counts", promotion.get("total_chunks") == total
              and promotion.get("approved_chunks") == approved
              and promotion.get("machine_extracted_beta_chunks") == beta,
              {"total": total, "approved": approved, "beta": beta, "records": records})
        check("record_name_metadata_coverage", has_name_metadata and name_metadata_rows == records,
              {"table_exists": has_name_metadata, "rows": name_metadata_rows, "records": records})
        check("record_name_metadata_classified", has_name_metadata and unclassified_name_rows == 0,
              {"unclassified_rows": unclassified_name_rows})

    policy = run(["python3", "scripts/test-fullbook-beta-chat-policy.py"])
    check("offline_chat_policy", policy["passed"], policy)
    synthetic_index = run(["python3", "scripts/test-fullbook-beta-index-synthetic.py"])
    check("fullbook_index_synthetic_adversarial", synthetic_index["passed"], synthetic_index)
    gem = run(["python3", "scripts/validate-fullbook-google-gem-pack.py", "--require-complete"])
    check("google_gem_pack", gem["passed"], gem)
    gem_adversarial = run(["python3", "scripts/test-fullbook-google-gem-pack-adversarial.py"])
    check("google_gem_pack_adversarial", gem_adversarial["passed"], gem_adversarial)

    report = {
        "schema_version": "1.0", "validated_at": now(),
        "status": "PASS" if all(item["passed"] for item in checks) else "FAIL",
        "main_database": str(args.main), "main_database_sha256": main_hash,
        "checks": checks,
        "local_api_url": "http://127.0.0.1:18765",
        "n8n_adapter_workflow_id": "PEChatApi001",
        "google_gem_upload_directory": "data/candidates/preembedding-v1/exports/google-gem/fullbook-beta",
    }
    if args.write:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
