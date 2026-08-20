#!/usr/bin/env python3
"""Build a source-exact plant record from a declarative page-range contract.

author: Codex (GPT-5)
date: 2026-08-12
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def extract_range(text: str, section: dict) -> str:
    start = text.find(section["start_marker"])
    if start < 0:
        raise ValueError(f"start marker missing: {section['section_type']} {section['start_marker']!r}")
    if section.get("end_marker") is None:
        end = len(text)
    else:
        end = text.find(section["end_marker"], start + len(section["start_marker"]))
        if end < 0:
            raise ValueError(f"end marker missing: {section['section_type']} {section['end_marker']!r}")
    original = text[start:end].strip()
    if not original:
        raise ValueError(f"empty section: {section['section_type']}")
    return original


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", type=Path)
    parser.add_argument("--fulltext-db", type=Path, default=LAB / "data/fulltext/kohler-pages.sqlite")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    output = args.output or LAB / "data/records" / f"{contract['record_slug']}.json"
    source = sqlite3.connect(f"file:{args.fulltext_db}?mode=ro", uri=True)
    pages = {
        row[0]: row[1]
        for row in source.execute(
            "SELECT pdf_page, best_text FROM pages WHERE source_id=? AND pdf_page BETWEEN ? AND ?",
            (contract["source_id"], min(contract["pdf_pages"]), max(contract["pdf_pages"])),
        )
    }
    source.close()
    if set(pages) != set(contract["pdf_pages"]):
        raise SystemExit("contract pages are missing from the fulltext corpus")

    sections = []
    for section in contract["sections"]:
        sections.append({
            "section_type": section["section_type"],
            "original_text": extract_range(pages[section["pdf_page"]], section),
            "normalized_text": section.get("normalized_text"),
            "zh_tw_rendering": section.get("zh_tw_rendering"),
            "evidence_indexes": section["evidence_indexes"],
        })
    record = {
        "record_id": contract["record_id"],
        "book_taxon": contract["book_taxon"],
        "display_name": contract["display_name"],
        "name_resolution": contract["name_resolution"],
        "book_evidence": contract["book_evidence"],
        "sections": sections,
        "review_status": contract["review_status"],
        "warnings": contract.get("warnings", []),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "record_id": record["record_id"],
        "sections": len(sections),
        "source_exact": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
