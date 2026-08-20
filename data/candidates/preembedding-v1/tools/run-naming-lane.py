#!/usr/bin/env python3
"""Run one resumable Taiwan-naming lane iteration and persist its status.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TZ = ZoneInfo("Asia/Taipei")


def run(script: str, *args: str) -> dict:
    process = subprocess.run(
        [sys.executable, str(TOOLS / script), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(process.stdout)


def load_manifest() -> dict:
    return json.loads((ROOT / "integration/embedding-ready-candidate-manifest.json").read_text(encoding="utf-8"))


def sync_provenance() -> None:
    candidates = {item["entry_id"]: item for item in load_manifest().get("candidates", [])}
    for path in (ROOT / "naming/staging").glob("*.naming.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        candidate = candidates.get(data.get("entry_id"))
        if not candidate:
            continue
        link = data.setdefault("eligibility", {})
        link.update({
            "authority": "integration/embedding-ready-candidate-manifest.json",
            "integration_status": "embedding_ready_candidate",
            "candidate_sha256": candidate.get("candidate_sha256"),
            "validation_check_sha256": candidate.get("validation_check_sha256"),
            "source_disposition_sha256": candidate.get("source_disposition_sha256"),
            "review_status": candidate.get("review_status"),
        })
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scan() -> tuple[list[str], set[str], list[str]]:
    eligible_entries = [item["entry_id"] for item in load_manifest().get("candidates", [])]
    staged_entries = {
        json.loads(path.read_text(encoding="utf-8"))["entry_id"]
        for path in (ROOT / "naming/staging").glob("*.naming.json")
    }
    pending = sorted(set(eligible_entries) - staged_entries)
    return eligible_entries, staged_entries, pending


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=12)
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("batch size must be positive")

    taicol_runs = []
    sync_provenance()
    while True:
        result = run("resolve-taicol-naming.py", "--pending-only", "--limit", str(args.batch_size))
        taicol_runs.append(result)
        if result.get("selected", 0) < args.batch_size:
            break
    alias_runs = []
    while True:
        result = run("supplement-taicol-book-aliases.py", "--limit", str(args.batch_size))
        alias_runs.append(result)
        if result.get("processed", 0) < args.batch_size:
            break
    tai2_runs = []
    while True:
        result = run("supplement-tai2-naming.py", "--limit", str(args.batch_size))
        tai2_runs.append(result)
        if result.get("processed", 0) < args.batch_size:
            break
    sync_provenance()
    source_scope = run("backfill-naming-source-scope.py")
    validation = run("validate-naming-staging.py")

    eligible_entries, staged_entries, pending = scan()
    # Close the race where a maker artifact appears after validation but before
    # the final scan. A bounded stabilization loop consumes that new difference.
    for _ in range(3):
        if not pending:
            break
        result = run("resolve-taicol-naming.py", "--pending-only", "--limit", str(args.batch_size))
        taicol_runs.append(result)
        while result.get("selected", 0) == args.batch_size:
            result = run("resolve-taicol-naming.py", "--pending-only", "--limit", str(args.batch_size))
            taicol_runs.append(result)
        result = run("supplement-tai2-naming.py", "--limit", str(args.batch_size))
        tai2_runs.append(result)
        while result.get("processed", 0) == args.batch_size:
            result = run("supplement-tai2-naming.py", "--limit", str(args.batch_size))
            tai2_runs.append(result)
        result = run("supplement-taicol-book-aliases.py", "--limit", str(args.batch_size))
        alias_runs.append(result)
        while result.get("processed", 0) == args.batch_size:
            result = run("supplement-taicol-book-aliases.py", "--limit", str(args.batch_size))
            alias_runs.append(result)
        sync_provenance()
        source_scope = run("backfill-naming-source-scope.py")
        validation = run("validate-naming-staging.py")
        eligible_entries, staged_entries, pending = scan()
    maker_path = ROOT / "batch-status.json"
    maker = json.loads(maker_path.read_text(encoding="utf-8")) if maker_path.is_file() else {}
    if pending:
        lane_status = "pending"
    elif maker.get("status") == "complete":
        lane_status = "complete"
    else:
        lane_status = "caught_up_waiting_for_maker"
    status = {
        "schema_version": "1.0",
        "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "lane_status": lane_status,
        "maker_status": maker.get("status", "unknown"),
        "maker_updated_at": maker.get("updated_at"),
        "eligibility_authority": "integration/embedding-ready-candidate-manifest.json",
        "eligible_entries": len(eligible_entries),
        "staged_entries": len(staged_entries),
        "pending_entries": pending,
        "terminal_status_counts": validation["terminal_status_counts"],
        "display_name_source_scope_counts": validation["display_name_source_scope_counts"],
        "record_chunk_projection": validation["record_chunk_projection"],
        "source_scope_backfill": source_scope,
        "validation": validation,
        "taicol_created_this_iteration": sum(item.get("selected", 0) for item in taicol_runs),
        "taicol_book_alias_checked_this_iteration": sum(item.get("processed", 0) for item in alias_runs),
        "taicol_book_alias_promoted_this_iteration": sum(item.get("promoted", 0) for item in alias_runs),
        "tai2_checked_this_iteration": sum(item.get("processed", 0) for item in tai2_runs),
        "external_fact_policy": "naming and occurrence metadata only; no Köhler facts",
        "canonical_writes": False,
    }
    checks = ROOT / "naming/checks"
    checks.mkdir(parents=True, exist_ok=True)
    (checks / "lane-status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
