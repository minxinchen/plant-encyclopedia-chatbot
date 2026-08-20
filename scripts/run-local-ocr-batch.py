#!/usr/bin/env python3
"""Resume all Apple Vision OCR staging queues with one local OCR slot."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
ROOT = LAB / "data/candidates/preembedding-v1"
STATUS = ROOT / "ocr-batch-status.json"
SHARDS = [f"S{i:02d}" for i in range(1, 9)]
CONSOLIDATOR = LAB / "scripts/consolidate-ocr-staging.py"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_status(state: dict) -> None:
    state["updated_at"] = now()
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATUS)


def run_one(shard: str) -> dict:
    completed = subprocess.run(
        ["python3", str(LAB / "scripts/run-local-ocr-worker.py"), shard, "--root", str(ROOT), "--limit", "1"],
        cwd=LAB,
        text=True,
        capture_output=True,
        timeout=700,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip()[-1000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


def refresh_manifest() -> None:
    completed = subprocess.run(
        [sys.executable, str(CONSOLIDATOR), "--write", "--check"],
        cwd=LAB,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip()[-1000:])


def main() -> None:
    state = {
        "schema_version": "1.0",
        "started_at": now(),
        "status": "running",
        "engine": "Apple Vision accurate de-DE + en-US",
        "current_shard": None,
        "processed_this_run": 0,
        "dispositions": {},
        "remaining_by_shard": {},
        "runner_errors": [],
    }
    write_status(state)
    while True:
        made_progress = False
        all_done = True
        for shard in SHARDS:
            state["current_shard"] = shard
            write_status(state)
            try:
                result = run_one(shard)
            except Exception as exc:
                state["runner_errors"].append({"at": now(), "shard": shard, "error": str(exc)[:1000]})
                write_status(state)
                continue
            state["remaining_by_shard"][shard] = result["remaining"]
            if result["processed"]:
                made_progress = True
                state["processed_this_run"] += result["processed"]
                for item in result["results"]:
                    key = item["disposition"]
                    state["dispositions"][key] = state["dispositions"].get(key, 0) + 1
            if result["remaining"] > 0:
                all_done = False
            write_status(state)
            if result["processed"]:
                try:
                    refresh_manifest()
                except Exception as exc:
                    state["runner_errors"].append({
                        "at": now(), "shard": shard, "stage": "manifest_refresh", "error": str(exc)[:1000]
                    })
                    write_status(state)
        if all_done or not made_progress:
            state["status"] = "complete" if all_done else "stopped_no_progress"
            state["current_shard"] = None
            write_status(state)
            try:
                refresh_manifest()
            except Exception as exc:
                state["runner_errors"].append({
                    "at": now(), "shard": None, "stage": "manifest_refresh", "error": str(exc)[:1000]
                })
                write_status(state)
            break
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
