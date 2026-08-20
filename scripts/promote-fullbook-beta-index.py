#!/usr/bin/env python3
"""Atomically promote an accepted full-book beta SQLite index to main.

Default mode is a read-only promotion plan. ``--execute`` requires a PASS live
chat report tied to the exact beta bytes, validates both databases, creates a
byte-identical rollback copy, and only then performs one atomic replacement.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_BETA = LAB / "data/index/staging/plant-embeddings-fullbook-beta.sqlite"
DEFAULT_MAIN = LAB / "data/index/plant-embeddings.sqlite"
DEFAULT_ACCEPTANCE = LAB / "reports/fullbook-beta-chat-acceptance.json"
DEFAULT_REPORT = LAB / "reports/fullbook-main-index-promotion.json"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def integrity(path: Path) -> str:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    value = db.execute("PRAGMA integrity_check").fetchone()[0]
    db.close()
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--beta", type=Path, default=DEFAULT_BETA)
    parser.add_argument("--main", type=Path, default=DEFAULT_MAIN)
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_ACCEPTANCE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    checks = []

    def check(name: str, passed: bool, observed: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "observed": observed})

    check("beta_exists", args.beta.is_file(), str(args.beta))
    check("main_exists", args.main.is_file(), str(args.main))
    check("acceptance_exists", args.acceptance.is_file(), str(args.acceptance))
    acceptance = json.loads(args.acceptance.read_text()) if args.acceptance.is_file() else {}
    beta_hash = sha256_file(args.beta) if args.beta.is_file() else None
    main_hash = sha256_file(args.main) if args.main.is_file() else None
    check("acceptance_pass", acceptance.get("status") == "PASS", acceptance.get("status"))
    check("acceptance_zero_cost", acceptance.get("incremental_usd") == 0
          and acceptance.get("paid_fallback_used") is False,
          {"incremental_usd": acceptance.get("incremental_usd"),
           "paid_fallback_used": acceptance.get("paid_fallback_used")})
    check("acceptance_beta_hash", beta_hash is not None
          and acceptance.get("database_sha256") == beta_hash,
          {"accepted": acceptance.get("database_sha256"), "current": beta_hash})
    check("beta_integrity", args.beta.is_file() and integrity(args.beta) == "ok",
          integrity(args.beta) if args.beta.is_file() else "missing")
    provenance = {}
    if args.beta.is_file():
        db = sqlite3.connect(f"file:{args.beta}?mode=ro", uri=True)
        provenance = dict(db.execute("SELECT key,value FROM index_build_provenance"))
        meta = dict(db.execute("SELECT key,value FROM embedding_meta"))
        has_name_metadata = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='record_name_metadata'"
        ).fetchone() is not None
        records = db.execute("SELECT count(DISTINCT record_id) FROM embedding_chunks").fetchone()[0]
        name_rows = db.execute("SELECT count(*) FROM record_name_metadata").fetchone()[0] if has_name_metadata else 0
        unclassified = db.execute(
            "SELECT count(*) FROM record_name_metadata WHERE display_name_source_scope='unclassified_staging'"
        ).fetchone()[0] if has_name_metadata else -1
        db.close()
        check("beta_status_contract", meta.get("active_review_statuses") == "approved,machine_extracted_beta",
              meta.get("active_review_statuses"))
        check("name_metadata_coverage", has_name_metadata and name_rows == records,
              {"table_exists": has_name_metadata, "rows": name_rows, "records": records})
        check("name_metadata_classified", unclassified == 0, unclassified)
    check("main_source_hash", provenance.get("approved_main_sha256") == main_hash,
          {"provenance": provenance.get("approved_main_sha256"), "current": main_hash})
    plan = {"schema_version": "1.0", "checked_at": now(),
            "ready": all(item["passed"] for item in checks), "checks": checks}
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False))
        raise SystemExit(0 if plan["ready"] else 2)
    if not plan["ready"]:
        raise SystemExit("main index promotion gate is not ready")

    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup_dir = args.main.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"plant-embeddings-before-fullbook-beta-{stamp}.sqlite"
    shutil.copy2(args.main, backup)
    if sha256_file(backup) != main_hash or integrity(backup) != "ok":
        raise SystemExit("rollback copy verification failed")
    incoming = args.main.with_suffix(args.main.suffix + ".incoming")
    shutil.copy2(args.beta, incoming)
    if sha256_file(incoming) != beta_hash or integrity(incoming) != "ok":
        raise SystemExit("incoming main index verification failed")
    os.replace(incoming, args.main)
    if sha256_file(args.main) != beta_hash or integrity(args.main) != "ok":
        raise SystemExit("promoted main index verification failed")
    db = sqlite3.connect(f"file:{args.main}?mode=ro", uri=True)
    total = db.execute("SELECT count(*) FROM embedding_chunks").fetchone()[0]
    approved = db.execute(
        "SELECT count(*) FROM embedding_chunks WHERE review_status='approved'"
    ).fetchone()[0]
    beta = db.execute(
        "SELECT count(*) FROM embedding_chunks WHERE review_status='machine_extracted_beta'"
    ).fetchone()[0]
    db.close()
    try:
        rollback_label = str(backup.relative_to(LAB))
    except ValueError:
        rollback_label = str(backup)
    try:
        acceptance_label = str(args.acceptance.relative_to(LAB))
    except ValueError:
        acceptance_label = str(args.acceptance)
    report = {
        "schema_version": "1.0", "promoted_at": now(), "status": "PASS",
        "previous_main_sha256": main_hash, "new_main_sha256": beta_hash,
        "rollback_database": rollback_label,
        "acceptance_report": acceptance_label,
        "total_chunks": total, "approved_chunks": approved, "machine_extracted_beta_chunks": beta,
        "sqlite_integrity": "ok", "atomic_replace": True, "rollback_verified": True,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
