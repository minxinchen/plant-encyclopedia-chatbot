#!/usr/bin/env python3
"""Wait for primary Qwen, then finish continuation and recovery packages.

The status file remains the dependency surface consumed by the Chandra and
finalization watchers.  ``complete`` now means both 41 continuation receipts
and nine deterministic content-recovery receipts passed, so Chandra cannot
take the shared memory/model slot between the two lanes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(command: list[str], timeout: int = 1200) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=timeout)
    return completed.returncode, (completed.stdout or completed.stderr).strip()[-6000:]


def write_status(path: Path, state: dict) -> None:
    state["updated_at"] = now()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--max-consecutive-errors", type=int, default=3)
    args = parser.parse_args()
    status_path = args.root / "checks/continuation-watch-status.json"
    state = {
        "schema_version": "1.0", "started_at": now(), "status": "waiting_for_primary_batch",
        "iterations": 0, "receipts_passed": 0, "remaining": 50,
        "continuation_receipts_passed": 0, "continuation_remaining": 41,
        "recovery_receipts_passed": 0, "recovery_remaining": 9,
        "unresolved_content_holds": 8, "needs_review": 0,
        "consecutive_errors": 0, "consecutive_no_progress": 0, "errors": [],
        "safety": {"external_model_calls": 0, "canonical_writes": False, "embedding_calls": False},
    }
    write_status(status_path, state)
    while True:
        primary = read_json(args.root / "batch-status.json")
        state["primary_batch_status"] = primary.get("status")
        state["primary_batch_processed"] = primary.get("processed_this_run", 0)
        if primary.get("status") in {"failed", "stopped_no_progress"}:
            state["status"] = "blocked_by_primary_batch"
            write_status(status_path, state)
            raise SystemExit("primary local structure batch did not complete")
        if primary.get("status") != "complete":
            state["status"] = "waiting_for_primary_batch"
            write_status(status_path, state)
            time.sleep(max(args.interval, 5))
            continue

        # Refresh both deterministic projections before selecting the next
        # lane.  This also makes a restart inherit already-valid receipts.
        continuation_code, continuation_output = run([
            "python3", str(LAB / "scripts/validate-preembedding-continuation-receipts.py"),
            "--root", str(args.root),
        ], timeout=300)
        integration_code, integration_output = run([
            "python3", str(LAB / "scripts/validate-preembedding-structure-integration.py"),
            "--root", str(args.root), "--require-maker-complete",
        ], timeout=300)
        if continuation_code or integration_code:
            state["consecutive_errors"] += 1
            state["errors"].append({
                "at": now(), "stage": "dependency_refresh",
                "output": (continuation_output + "\n" + integration_output)[-6000:],
            })
            state["errors"] = state["errors"][-20:]
            state["status"] = "retrying_dependency_refresh"
            write_status(status_path, state)
            if state["consecutive_errors"] >= args.max_consecutive_errors:
                state["status"] = "failed_after_retries"
                write_status(status_path, state)
                raise SystemExit("post-batch dependency refresh failed repeatedly")
            time.sleep(max(args.interval, 5))
            continue
        continuation_validation = json.loads(continuation_output.splitlines()[-1])
        integration = json.loads(integration_output.splitlines()[-1])
        state["primary_receipts_checked"] = integration["maker_receipts_checked"]
        state["primary_receipts_needs_review"] = integration["maker_receipts_needs_review"]
        state["primary_needs_review_entry_ids"] = integration["needs_review_entry_ids"]
        if (
            integration["maker_receipts_checked"] != 231
            or integration["maker_receipts_needs_review"] != 0
        ):
            # A primary ``complete`` status only proves every request ended.
            # The post-batch lanes must not hide a deterministic structure
            # failure; leave the watcher resumably waiting for an explicit
            # source-exact changed-strategy repair.
            state["status"] = "waiting_for_primary_validation_repair"
            write_status(status_path, state)
            time.sleep(max(args.interval, 5))
            continue
        state["continuation_receipts_passed"] = continuation_validation["deterministic_pass"]
        state["continuation_remaining"] = continuation_validation["remaining"]
        state["recovery_receipts_passed"] = integration["content_recovery_receipts_passed"]
        state["recovery_remaining"] = 9 - integration["content_recovery_receipts_passed"]
        state["unresolved_content_holds"] = integration["unresolved_content_holds"]
        state["receipts_passed"] = (
            state["continuation_receipts_passed"] + state["recovery_receipts_passed"]
        )
        state["remaining"] = state["continuation_remaining"] + state["recovery_remaining"]
        state["needs_review"] = (
            continuation_validation["needs_review"]
            + integration["content_recovery_receipts_needs_review"]
        )
        if (
            state["continuation_remaining"] == 0
            and state["recovery_remaining"] == 0
            and state["needs_review"] == 0
            and state["unresolved_content_holds"] == 0
        ):
            validation_code, validation_output = run([
                "python3", str(LAB / "scripts/validate-preembedding-integration-artifacts.py"),
                "--root", str(args.root),
            ], timeout=300)
            if validation_code:
                state["consecutive_errors"] += 1
                state["errors"].append({
                    "at": now(), "stage": "final_independent_validator",
                    "output": validation_output,
                })
                state["status"] = "retrying_final_validation"
                write_status(status_path, state)
                if state["consecutive_errors"] >= args.max_consecutive_errors:
                    state["status"] = "failed_after_retries"
                    write_status(status_path, state)
                    raise SystemExit("post-batch independent validation failed repeatedly")
                time.sleep(max(args.interval, 5))
                continue
            state["last_independent_validation"] = json.loads(validation_output.splitlines()[-1])
            state["status"] = "complete"
            state["active_lane"] = None
            write_status(status_path, state)
            # Chandra waits on this complete receipt chain; release the shared
            # Qwen memory slot only after all 50 deterministic passes exist.
            subprocess.run(
                [str(LAB.parents[1] / "service"), "qwen", "off"],
                cwd=LAB.parents[1], check=False, capture_output=True, text=True,
            )
            break

        lane = "continuation" if state["continuation_remaining"] else "recovery"
        before_progress = (state["continuation_receipts_passed"], state["recovery_receipts_passed"])
        state["active_lane"] = lane
        state["status"] = f"running_{lane}"
        write_status(status_path, state)
        command = [
            "python3", str(LAB / "scripts/run-local-preembedding-continuations.py"),
            "--root", str(args.root), "--execute", "--start-service", "--keep-service-on", "--limit", "1",
            "--model", primary["model"], "--lane", lane,
        ]
        code, output = run(command)
        state["iterations"] += 1
        if code:
            state["consecutive_errors"] += 1
            state["errors"].append({"at": now(), "stage": "maker", "output": output})
            state["errors"] = state["errors"][-20:]
            state["status"] = "retrying_changed_strategy"
            write_status(status_path, state)
            if state["consecutive_errors"] >= args.max_consecutive_errors:
                state["status"] = "failed_after_retries"
                write_status(status_path, state)
                raise SystemExit("continuation maker failed repeatedly")
            time.sleep(max(args.interval, 5))
            continue
        state["consecutive_errors"] = 0

        validate_code, validate_output = run([
            "python3", str(LAB / "scripts/validate-preembedding-continuation-receipts.py"),
            "--root", str(args.root),
        ], timeout=300)
        integration_code, integration_output = run([
            "python3", str(LAB / "scripts/validate-preembedding-structure-integration.py"),
            "--root", str(args.root), "--require-maker-complete",
        ], timeout=300)
        if validate_code or integration_code:
            state["consecutive_errors"] += 1
            state["errors"].append({
                "at": now(), "stage": "validator",
                "output": (validate_output + "\n" + integration_output)[-6000:],
            })
            state["status"] = "retrying_validation"
            write_status(status_path, state)
            continue
        validation = json.loads(validate_output.splitlines()[-1])
        integration = json.loads(integration_output.splitlines()[-1])
        state["continuation_receipts_passed"] = validation["deterministic_pass"]
        state["continuation_remaining"] = validation["remaining"]
        state["recovery_receipts_passed"] = integration["content_recovery_receipts_passed"]
        state["recovery_remaining"] = 9 - integration["content_recovery_receipts_passed"]
        state["unresolved_content_holds"] = integration["unresolved_content_holds"]
        state["receipts_passed"] = (
            state["continuation_receipts_passed"] + state["recovery_receipts_passed"]
        )
        state["remaining"] = state["continuation_remaining"] + state["recovery_remaining"]
        state["needs_review"] = (
            validation["needs_review"] + integration["content_recovery_receipts_needs_review"]
        )
        state["last_validation"] = {
            "continuation": validation,
            "integration_summary_sha256": integration["summary_sha256"],
        }
        after_progress = (state["continuation_receipts_passed"], state["recovery_receipts_passed"])
        if after_progress == before_progress:
            state["consecutive_no_progress"] += 1
        else:
            state["consecutive_no_progress"] = 0
        if state["consecutive_no_progress"] >= args.max_consecutive_errors:
            state["status"] = "failed_after_retries"
            state["errors"].append({
                "at": now(), "stage": lane,
                "output": "deterministic pass count did not advance after repeated changed-strategy retries",
            })
            write_status(status_path, state)
            raise SystemExit(f"{lane} maker did not make deterministic progress")
        state["status"] = f"running_{lane}"
        write_status(status_path, state)
        time.sleep(1)
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
