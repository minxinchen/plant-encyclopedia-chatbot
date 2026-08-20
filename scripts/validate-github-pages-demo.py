#!/usr/bin/env python3
"""Deterministic public-demo integrity and leakage checks.

Author: Codex (GPT-5)
Date: 2026-08-13
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "demo-site"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> None:
    expected = {
        "index.html",
        ".nojekyll",
        "README.md",
        "assets/app.js",
        "assets/styles.css",
        "data/knowledge.json",
        "version.json",
    }
    missing = [name for name in expected if not (SITE / name).is_file()]
    if missing:
        fail(f"missing public files: {missing}")

    payload = json.loads((SITE / "data/knowledge.json").read_text(encoding="utf-8"))
    records = payload["records"]
    if payload["meta"]["version"] != "0.1.0" or len(records) != 8:
        fail("expected version 0.1.0 and exactly 8 approved records")
    if payload["meta"]["image_reasoning"] is not False:
        fail("image reasoning must remain disabled")

    record_ids = set()
    for record in records:
        if record["record_id"] in record_ids:
            fail(f"duplicate record_id: {record['record_id']}")
        record_ids.add(record["record_id"])
        if not record["display_name"] or not record["sections"]:
            fail(f"incomplete record: {record['record_id']}")
        for section in record["sections"]:
            if not section["zh_tw_rendering"] or not section["citations"]:
                fail(f"uncited section: {record['record_id']} / {section['section_type']}")
            for citation in section["citations"]:
                if not citation["source_id"] or not isinstance(citation["pdf_page"], int):
                    fail(f"invalid citation: {record['record_id']}")

    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in SITE.rglob("*")
        if path.is_file()
    )
    forbidden_keys = ["original_text", "GITHUB_PAT", "GEMINI_API_KEY", "GOOGLE_API_KEY"]
    for key in forbidden_keys:
        if key in public_text:
            fail(f"forbidden key or source field leaked: {key}")
    secret_patterns = [
        r"ghp_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"AIza[0-9A-Za-z_-]{20,}",
        r"AQ\.[A-Za-z0-9_-]{20,}",
    ]
    for pattern in secret_patterns:
        if re.search(pattern, public_text):
            fail(f"possible secret matched: {pattern}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "version": payload["meta"]["version"],
                "records": len(records),
                "sections": sum(len(record["sections"]) for record in records),
                "secret_scan": "PASS",
                "original_text_exported": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
