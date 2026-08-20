#!/usr/bin/env python3
"""Refresh source-exact record staging whenever integration or naming changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [root / "integration/embedding-ready-candidate-manifest.json"]
    paths.extend(sorted((root / "naming/staging").glob("*.naming.json")))
    for path in paths:
        digest.update(path.name.encode())
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=300)
    return completed.returncode, (completed.stdout or completed.stderr).strip()[-4000:]


def write_status(path: Path, state: dict) -> None:
    state["updated_at"] = now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    status_path = args.root / "records-candidate/watch-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "1.0",
        "started_at": now(),
        "status": "running",
        "refresh_count": 0,
        "last_fingerprint": None,
        "last_build": None,
        "last_validation": None,
        "errors": [],
    }
    write_status(status_path, state)
    while True:
        current = fingerprint(args.root)
        if current != state["last_fingerprint"]:
            build_code, build_output = run([
                "python3", str(LAB / "scripts/build-preembedding-records.py"), "--root", str(args.root)
            ])
            validate_code, validate_output = run([
                "python3", str(LAB / "scripts/validate-preembedding-records.py"),
                "--root", str(args.root), "--require-caught-up",
            ])
            state["refresh_count"] += 1
            state["last_fingerprint"] = current
            state["last_build"] = {"exit_code": build_code, "output": build_output}
            state["last_validation"] = {"exit_code": validate_code, "output": validate_output}
            if build_code:
                state["errors"].append({"at": now(), "stage": "build", "output": build_output})
            state["status"] = "caught_up" if not build_code and not validate_code else "awaiting_upstream"
            write_status(status_path, state)

        integration = json.loads(
            (args.root / "integration/embedding-ready-candidate-manifest.json").read_text(encoding="utf-8")
        )
        if integration.get("status") == "complete":
            code, output = run([
                "python3", str(LAB / "scripts/validate-preembedding-records.py"),
                "--root", str(args.root), "--require-caught-up",
            ])
            if code == 0:
                state["status"] = "complete"
                state["last_validation"] = {"exit_code": code, "output": output}
                write_status(status_path, state)
                break
        time.sleep(max(args.interval, 5))


if __name__ == "__main__":
    main()
