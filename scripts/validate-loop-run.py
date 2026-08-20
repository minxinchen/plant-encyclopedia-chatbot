#!/usr/bin/env python3
"""Validate loop output and enforce the zero-incremental-cost policy."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def main() -> None:
    lab = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("run", nargs="?", type=Path, default=lab / "reports/loop-run-sample-2026-08-03.json")
    parser.add_argument("--schema", type=Path, default=lab / "schemas/loop-run.schema.json")
    args = parser.parse_args()
    data = json.loads(args.run.read_text(encoding="utf-8"))
    required = {"schema_version", "run_id", "started_at", "finished_at", "status", "cost", "input", "stages", "review_summary"}
    missing = sorted(required - data.keys())
    if missing:
        raise SystemExit(f"FAIL missing required fields: {', '.join(missing)}")
    if data["schema_version"] != "1.0" or data["status"] not in {"approved", "needs_review", "failed"}:
        raise SystemExit("FAIL invalid schema version or status")
    datetime.fromisoformat(data["started_at"])
    datetime.fromisoformat(data["finished_at"])
    if not data["stages"] or any(s.get("status") not in {"passed", "needs_review", "failed", "skipped"} for s in data["stages"]):
        raise SystemExit("FAIL invalid or empty stages")
    if data["cost"]["incremental_usd"] != 0 or data["cost"]["paid_fallback_used"]:
        raise SystemExit("FAIL paid usage detected")
    retry_targets = [s["retry_target"] for s in data["stages"] if s["retry_target"]]
    print(json.dumps({"valid": True, "status": data["status"], "retry_targets": retry_targets}, ensure_ascii=False))


if __name__ == "__main__":
    main()
