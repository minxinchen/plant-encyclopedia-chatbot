#!/usr/bin/env python3
"""Resume all frozen local pre-embedding maker shards, one model request at a time.

This runner writes only staging candidates and a resumable status projection. It
does not write canonical records, chunks, embeddings, Taiwan names, or approvals.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
WORKSTATION = LAB.parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
DEFAULT_MODEL = WORKSTATION / "services/qwen35-mlx/models/Qwen3.5-27B-4bit"
SHARDS = [f"S{i:02d}" for i in range(1, 9)]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_status(path: Path, payload: dict) -> None:
    payload["updated_at"] = now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def endpoint_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status == 200
    except Exception:
        return False


def run_one(args: argparse.Namespace, shard: str) -> dict:
    command = [
        "python3", str(LAB / "scripts/run-local-preembedding-worker.py"), shard,
        "--root", str(args.root), "--endpoint", args.endpoint,
        "--model", str(args.model), "--limit", "1", "--max-tokens", str(args.max_tokens),
    ]
    completed = subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=args.request_timeout)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip()[-1000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18080/v1/chat/completions")
    parser.add_argument("--models-endpoint", default="http://127.0.0.1:18080/v1/models")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--request-timeout", type=int, default=900)
    parser.add_argument("--max-consecutive-runner-errors", type=int, default=3)
    parser.add_argument("--keep-service-on", action="store_true")
    args = parser.parse_args()

    status_path = args.root / "batch-status.json"
    state = {
        "schema_version": "1.0",
        "started_at": now(),
        "status": "starting",
        "model": str(args.model),
        "endpoint": args.endpoint,
        "current_shard": None,
        "processed_this_run": 0,
        "pass_this_run": 0,
        "needs_review_this_run": 0,
        "runner_errors": [],
        "remaining_by_shard": {},
    }
    write_status(status_path, state)

    subprocess.run(
        ["python3", str(LAB / "scripts/validate-local-preembedding-shards.py"), str(args.root)],
        cwd=LAB,
        check=True,
    )
    if not endpoint_ready(args.models_endpoint):
        subprocess.run([str(WORKSTATION / "service"), "qwen", "on"], cwd=WORKSTATION, check=True)
    if not endpoint_ready(args.models_endpoint):
        raise SystemExit("local model endpoint is unavailable")

    consecutive_errors = 0
    try:
        while True:
            made_progress = False
            all_done = True
            for shard in SHARDS:
                state["current_shard"] = shard
                state["status"] = "running"
                write_status(status_path, state)
                try:
                    result = run_one(args, shard)
                    consecutive_errors = 0
                except Exception as exc:
                    consecutive_errors += 1
                    state["runner_errors"].append({"at": now(), "shard": shard, "error": str(exc)[:1000]})
                    write_status(status_path, state)
                    if consecutive_errors >= args.max_consecutive_runner_errors:
                        raise
                    continue

                remaining = result["remaining"]
                state["remaining_by_shard"][shard] = remaining
                if result["processed"]:
                    made_progress = True
                    state["processed_this_run"] += result["processed"]
                    for item in result["results"]:
                        key = "pass_this_run" if item["status"] == "pass" else "needs_review_this_run"
                        state[key] += 1
                if remaining > 0:
                    all_done = False
                write_status(status_path, state)

            if all_done or not made_progress:
                state["status"] = "complete" if all_done else "stopped_no_progress"
                state["current_shard"] = None
                write_status(status_path, state)
                break
    except Exception as exc:
        state["status"] = "failed"
        state["fatal_error"] = f"{type(exc).__name__}:{str(exc)[:1000]}"
        write_status(status_path, state)
        raise
    finally:
        if not args.keep_service_on:
            subprocess.run([str(WORKSTATION / "service"), "qwen", "off"], cwd=WORKSTATION, check=False)

    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
