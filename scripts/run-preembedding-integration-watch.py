#!/usr/bin/env python3
"""Refresh deterministic structure integration as the local maker progresses.

The watcher reads the local batch status and writes only the preembedding-v1
checks/structure/integration staging owned by this lane. It performs no model,
embedding, network, canonical, source PDF, or Taiwan-name operation.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
TERMINAL_BATCH_STATES = {"complete", "failed", "stopped_no_progress"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(path: Path, value: dict) -> None:
    value["updated_at"] = now()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(command: list[str]) -> dict:
    completed = subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=180)
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        raise RuntimeError(output[-1200:] or f"exit {completed.returncode}")
    return json.loads(output.splitlines()[-1])


def refresh(root: Path, maker_complete: bool, final_ready: bool) -> tuple[dict, dict, dict, dict, dict]:
    integration_command = [
        "python3", str(LAB / "scripts/validate-preembedding-structure-integration.py"),
        "--root", str(root),
    ]
    artifact_command = [
        "python3", str(LAB / "scripts/validate-preembedding-integration-artifacts.py"),
        "--root", str(root),
    ]
    audit_command = [
        "python3", str(LAB / "scripts/build-preembedding-completion-audit.py"),
        "--root", str(root),
    ]
    continuation_command = [
        "python3", str(LAB / "scripts/validate-preembedding-continuation-receipts.py"),
        "--root", str(root),
    ]
    source_command = [
        "python3", str(LAB / "scripts/validate-preembedding-source-receipt.py"),
        "--root", str(root),
    ]
    source_check_path = root / "checks/source-receipt-validation.json"
    existing_source = read_json(source_check_path) if source_check_path.is_file() else {}
    source_full_hash_already_verified = (
        existing_source.get("status") == "PASS"
        and existing_source.get("full_hash_verified") is True
    )
    if maker_complete:
        integration_command.append("--require-maker-complete")
        if not source_full_hash_already_verified:
            source_command.extend(["--rehash-source-pdfs", "--require-full-hash"])
    if final_ready:
        artifact_command.append("--require-complete")
        audit_command.append("--require-complete")
    integration = run(integration_command)
    source = existing_source if maker_complete and source_full_hash_already_verified else run(source_command)
    audit = run(audit_command)
    validation = run(artifact_command)
    continuation = run(continuation_command)
    return integration, source, audit, validation, continuation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 300:
        raise SystemExit("poll-seconds must be between 5 and 300")

    status_path = args.root / "checks/integration-watch-status.json"
    state = {
        "schema_version": "1.0",
        "started_at": now(),
        "status": "starting",
        "observed_batch_started_at": None,
        "observed_batch_processed": -1,
        "refresh_count": 0,
        "transient_errors": [],
        "safety": {
            "model_calls": False,
            "embedding_calls": False,
            "external_api_calls": False,
            "canonical_writes": False,
            "taiwan_name_resolution": False,
            "layout_or_plate_approval": False,
        },
    }
    write_status(status_path, state)

    while True:
        batch = read_json(args.root / "batch-status.json")
        batch_started_at = batch.get("started_at")
        processed = int(batch.get("processed_this_run", 0))
        batch_status = batch.get("status", "unknown")
        should_refresh = (
            state["observed_batch_started_at"] != batch_started_at
            or processed != state["observed_batch_processed"]
            or batch_status in TERMINAL_BATCH_STATES
            or state["refresh_count"] == 0
        )
        if should_refresh:
            try:
                prior_continuation_passes = int(state.get("continuation_receipts_passed", 0))
                prior_recovery_passes = int(state.get("content_recovery_receipts_passed", 0))
                final_ready = (
                    batch_status == "complete"
                    and prior_continuation_passes == 41
                    and prior_recovery_passes == 9
                )
                integration, source, audit, validation, continuation = refresh(
                    args.root,
                    batch_status == "complete",
                    final_ready,
                )
                complete = bool(validation["complete"])
                if batch_status == "complete" and not complete:
                    watch_status = "awaiting_postbatch_receipts"
                else:
                    watch_status = "complete" if complete else "watching"
                state.update({
                    "status": watch_status,
                    "observed_batch_started_at": batch_started_at,
                    "observed_batch_processed": processed,
                    "observed_batch_status": batch_status,
                    "refresh_count": state["refresh_count"] + 1,
                    "detected_entries": validation["detected_entries"],
                    "terminal_entries": validation["terminal_entries"],
                    "nonterminal_entries": validation["nonterminal_entries"],
                    "maker_receipts_checked": validation["maker_receipts_checked"],
                    "maker_receipts_passed": integration["maker_receipts_passed"],
                    "maker_receipts_needs_review": integration["maker_receipts_needs_review"],
                    "needs_review_entry_ids": integration["needs_review_entry_ids"],
                    "changed_strategy_repairs": validation["changed_strategy_repairs"],
                    "continuation_packages": validation["continuation_packages"],
                    "continuation_receipts_passed": continuation["deterministic_pass"],
                    "continuation_receipts_remaining": continuation["remaining"],
                    "continuation_receipts_needs_review": continuation["needs_review"],
                    "content_recovery_packages": integration["content_recovery_packages"],
                    "content_recovery_receipts_passed": integration["content_recovery_receipts_passed"],
                    "content_recovery_receipts_needs_review": integration["content_recovery_receipts_needs_review"],
                    "unresolved_content_holds": integration["unresolved_content_holds"],
                    "embedding_ready_text_candidates": validation["embedding_ready_text_candidates"],
                    "complete": validation["complete"],
                    "last_integration_summary_sha256": integration["summary_sha256"],
                    "last_completion_audit_sha256": audit["audit_sha256"],
                    "last_source_validation_sha256": source["check_sha256"],
                    "source_pdfs_full_hash_verified": source["full_hash_verified"],
                    "completion_requirements_achieved": audit["achieved_requirements"],
                    "completion_requirements_total": audit["total_requirements"],
                })
            except Exception as exc:
                state["status"] = "terminal_validation_failed" if batch_status in TERMINAL_BATCH_STATES else "transient_validation_error"
                state["transient_errors"].append({"at": now(), "batch_processed": processed, "error": str(exc)[:1200]})
                state["transient_errors"] = state["transient_errors"][-20:]
                write_status(status_path, state)
                if batch_status in {"failed", "stopped_no_progress"}:
                    raise
            write_status(status_path, state)

        if args.once or state.get("complete"):
            break
        if batch_status in {"failed", "stopped_no_progress"}:
            raise SystemExit(f"maker batch ended without completion: {batch_status}")
        time.sleep(args.poll_seconds)

    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
