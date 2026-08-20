#!/usr/bin/env python3
"""Finalize embeddings, beta acceptance, and one atomic full-book main rebuild."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write(path: Path, value: dict) -> None:
    value["updated_at"] = now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def run(command: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=LAB, capture_output=True, text=True, timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    status_path = args.root / "embedding-results/watch-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "1.0", "started_at": now(), "status": "waiting_for_upstream",
        "gate": None, "execution": None, "validation": None,
        "index_build": None, "index_validation": None,
        "chat_acceptance": None, "main_promotion": None,
        "gem_export": None, "gem_validation": None, "release_validation": None,
        "consecutive_errors": 0, "errors": [],
    }
    write(status_path, state)
    while True:
        gate_result = run([
            "python3", str(LAB / "scripts/execute-preembedding-embeddings.py"),
            "--root", str(args.root),
        ], timeout=300)
        try:
            gate = json.loads(gate_result.stdout)
        except json.JSONDecodeError:
            gate = {"ready": False, "error": (gate_result.stdout or gate_result.stderr)[-2000:]}
        state["gate"] = gate
        if not gate.get("ready"):
            state["status"] = "waiting_for_upstream"
            write(status_path, state)
            time.sleep(max(5, args.interval))
            continue

        state["status"] = "embedding"
        write(status_path, state)
        completed = run([
            "python3", str(LAB / "scripts/execute-preembedding-embeddings.py"),
            "--root", str(args.root), "--execute", "--batch-size", str(args.batch_size),
        ], timeout=24 * 3600)
        state["execution"] = {
            "exit_code": completed.returncode,
            "output": (completed.stdout or completed.stderr)[-4000:],
        }
        if completed.returncode:
            state["consecutive_errors"] += 1
            state["errors"].append({"at": now(), **state["execution"]})
            state["status"] = "paused_after_error" if state["consecutive_errors"] >= 3 else "retry_wait"
            write(status_path, state)
            if state["consecutive_errors"] >= 3:
                break
            time.sleep(max(60, args.interval))
            continue

        validation = run([
            "python3", str(LAB / "scripts/validate-preembedding-embedding-results.py"),
            "--root", str(args.root), "--require-complete",
        ], timeout=1800)
        state["validation"] = {
            "exit_code": validation.returncode,
            "output": (validation.stdout or validation.stderr)[-4000:],
        }
        if validation.returncode == 0:
            state["status"] = "building_beta_index"
            write(status_path, state)
            index_build = run([
                "python3", str(LAB / "scripts/build-fullbook-beta-index.py"),
                "--root", str(args.root),
            ], timeout=3600)
            state["index_build"] = {
                "exit_code": index_build.returncode,
                "output": (index_build.stdout or index_build.stderr)[-4000:],
            }
            if index_build.returncode == 0:
                index_validation = run([
                    "python3", str(LAB / "scripts/validate-fullbook-beta-index.py"),
                    "--root", str(args.root),
                ], timeout=1800)
                state["index_validation"] = {
                    "exit_code": index_validation.returncode,
                    "output": (index_validation.stdout or index_validation.stderr)[-4000:],
                }
                if index_validation.returncode == 0:
                    state["status"] = "running_live_chat_acceptance"
                    write(status_path, state)
                    acceptance = run([
                        "python3", str(LAB / "scripts/test-fullbook-beta-chat-acceptance.py"),
                        "--execute",
                    ], timeout=3600)
                    state["chat_acceptance"] = {
                        "exit_code": acceptance.returncode,
                        "output": (acceptance.stdout or acceptance.stderr)[-6000:],
                    }
                    if acceptance.returncode:
                        # Do not repeatedly spend the bounded free-tier call
                        # budget on the same semantic/citation failure.  The
                        # persistent goal audits this durable status and may
                        # restart after a code or data repair.
                        state["status"] = "chat_acceptance_failed_needs_review"
                        state["errors"].append({"at": now(), **state["chat_acceptance"]})
                        write(status_path, state)
                        break
                    state["status"] = "promoting_main_index"
                    write(status_path, state)
                    promotion = run([
                        "python3", str(LAB / "scripts/promote-fullbook-beta-index.py"),
                        "--execute",
                    ], timeout=3600)
                    state["main_promotion"] = {
                        "exit_code": promotion.returncode,
                        "output": (promotion.stdout or promotion.stderr)[-6000:],
                    }
                    if promotion.returncode:
                        state["status"] = "main_promotion_failed_needs_review"
                        state["errors"].append({"at": now(), **state["main_promotion"]})
                        write(status_path, state)
                        break
                    state["status"] = "exporting_google_gem_pack"
                    write(status_path, state)
                    gem_export = run([
                        "python3", str(LAB / "scripts/export-fullbook-google-gem-pack.py"),
                        "--root", str(args.root),
                    ], timeout=3600)
                    state["gem_export"] = {
                        "exit_code": gem_export.returncode,
                        "output": (gem_export.stdout or gem_export.stderr)[-6000:],
                    }
                    if gem_export.returncode:
                        state["status"] = "gem_export_failed_needs_review"
                        state["errors"].append({"at": now(), **state["gem_export"]})
                        write(status_path, state)
                        break
                    gem_validation = run([
                        "python3", str(LAB / "scripts/validate-fullbook-google-gem-pack.py"),
                        "--root", str(args.root), "--require-complete",
                    ], timeout=1800)
                    state["gem_validation"] = {
                        "exit_code": gem_validation.returncode,
                        "output": (gem_validation.stdout or gem_validation.stderr)[-6000:],
                    }
                    if gem_validation.returncode:
                        state["status"] = "gem_validation_failed_needs_review"
                        state["errors"].append({"at": now(), **state["gem_validation"]})
                        write(status_path, state)
                        break
                    release_validation = run([
                        "python3", str(LAB / "scripts/validate-fullbook-release.py"), "--write",
                    ], timeout=1800)
                    state["release_validation"] = {
                        "exit_code": release_validation.returncode,
                        "output": (release_validation.stdout or release_validation.stderr)[-6000:],
                    }
                    if release_validation.returncode:
                        state["status"] = "release_validation_failed_needs_review"
                        state["errors"].append({"at": now(), **state["release_validation"]})
                        write(status_path, state)
                        break
                    state["status"] = "complete_main_index_and_google_gem_pack"
                    state["consecutive_errors"] = 0
                    write(status_path, state)
                    break
            failure = state["index_validation"] or state["index_build"]
            state["consecutive_errors"] += 1
            state["errors"].append({"at": now(), **failure})
            state["status"] = "index_validation_failed"
            write(status_path, state)
            if state["consecutive_errors"] >= 3:
                break
            time.sleep(max(60, args.interval))
            continue
        state["consecutive_errors"] += 1
        state["errors"].append({"at": now(), **state["validation"]})
        state["status"] = "validation_failed"
        write(status_path, state)
        if state["consecutive_errors"] >= 3:
            break
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    main()
