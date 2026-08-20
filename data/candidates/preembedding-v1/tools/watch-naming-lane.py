#!/usr/bin/env python3
"""Poll integration candidates and keep Taiwan naming staging caught up.

The watcher writes only naming staging/check artifacts. It exits only after the
integration manifest is complete and both naming and integration validators pass.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[2]
TOOLS = ROOT / "tools"
CHECKS = ROOT / "naming/checks"
MANIFEST = ROOT / "integration/embedding-ready-candidate-manifest.json"
PIDFILE = CHECKS / "watcher.pid"
STATUS = CHECKS / "watcher-status.json"
TZ = ZoneInfo("Asia/Taipei")


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def read_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def fingerprint(manifest: dict) -> str:
    state = {
        "status": manifest.get("status"),
        "candidate_count": manifest.get("candidate_count"),
        "candidates": [
            {
                "entry_id": item.get("entry_id"),
                "candidate_sha256": item.get("candidate_sha256"),
                "validation_check_sha256": item.get("validation_check_sha256"),
                "source_disposition_sha256": item.get("source_disposition_sha256"),
                "review_status": item.get("review_status"),
            }
            for item in manifest.get("candidates", [])
        ],
    }
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def run_json(command: list[str]) -> tuple[int, dict | None, str]:
    process = subprocess.run(command, cwd=PROJECT, text=True, capture_output=True)
    output = process.stdout.strip()
    parsed = None
    if output:
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            pass
    error = process.stderr.strip()
    return process.returncode, parsed, error


def write_status(data: dict) -> None:
    CHECKS.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=12)
    args = parser.parse_args()
    if args.interval < 5 or args.batch_size < 1:
        raise SystemExit("interval must be >= 5 and batch size must be positive")

    CHECKS.mkdir(parents=True, exist_ok=True)
    if PIDFILE.is_file():
        try:
            old_pid = int(PIDFILE.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = 0
        if old_pid and pid_alive(old_pid):
            raise SystemExit(f"naming watcher already running: pid {old_pid}")
    PIDFILE.write_text(f"{os.getpid()}\n", encoding="utf-8")

    started_at = now()
    prior_fingerprint = None
    iterations = 0
    try:
        while True:
            manifest = read_manifest()
            current_fingerprint = fingerprint(manifest)
            changed = current_fingerprint != prior_fingerprint
            previous = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.is_file() else {}
            latest_lane = previous.get("last_lane")
            lane_status_path = CHECKS / "lane-status.json"
            if lane_status_path.is_file():
                latest_lane = json.loads(lane_status_path.read_text(encoding="utf-8"))
            status = {
                "schema_version": "1.0",
                "watcher_status": "running",
                "pid": os.getpid(),
                "started_at": started_at,
                "updated_at": now(),
                "poll_interval_seconds": args.interval,
                "integration_status": manifest.get("status"),
                "integration_candidate_count": manifest.get("candidate_count", 0),
                "integration_fingerprint": current_fingerprint,
                "fingerprint_changed": changed,
                "iterations": iterations,
                "canonical_writes": False,
                "last_lane_exit_code": previous.get("last_lane_exit_code"),
                "last_lane": latest_lane,
                "last_lane_error": previous.get("last_lane_error"),
            }
            if changed:
                code, lane, error = run_json([
                    sys.executable,
                    str(TOOLS / "run-naming-lane.py"),
                    "--batch-size",
                    str(args.batch_size),
                ])
                iterations += 1
                status.update({
                    "iterations": iterations,
                    "last_lane_exit_code": code,
                    "last_lane": lane,
                    "last_lane_error": error or None,
                })
                write_status(status)
                if code != 0:
                    time.sleep(args.interval)
                    continue
                prior_fingerprint = current_fingerprint

            if manifest.get("status") == "complete":
                code, integration, error = run_json([
                    sys.executable,
                    str(PROJECT / "scripts/validate-preembedding-integration-artifacts.py"),
                    "--root",
                    str(ROOT),
                ])
                status.update({
                    "integration_validator_exit_code": code,
                    "integration_validation": integration,
                    "integration_validator_error": error or None,
                })
                lane = status.get("last_lane") or json.loads((CHECKS / "lane-status.json").read_text(encoding="utf-8"))
                if code == 0 and lane.get("validation", {}).get("valid") and not lane.get("pending_entries"):
                    status["watcher_status"] = "complete"
                    status["completed_at"] = now()
                    write_status(status)
                    return
                write_status(status)

            write_status(status)
            time.sleep(args.interval)
    finally:
        if PIDFILE.is_file() and PIDFILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PIDFILE.unlink()


if __name__ == "__main__":
    main()
