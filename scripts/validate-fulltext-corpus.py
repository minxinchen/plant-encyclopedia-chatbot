#!/usr/bin/env python3
"""Validate full-book page coverage, FTS integrity and OCR queue linkage."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, default=LAB / "data/source-manifest.json")
    parser.add_argument("--database", type=Path, default=LAB / "data/fulltext/kohler-pages.sqlite")
    parser.add_argument("--manifest", type=Path, default=LAB / "data/fulltext/extraction-manifest.json")
    parser.add_argument("--ocr-queue", type=Path, default=LAB / "data/fulltext/ocr-queue.jsonl")
    args = parser.parse_args()

    sources = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    extraction = json.loads(args.manifest.read_text(encoding="utf-8"))
    queue = [json.loads(line) for line in args.ocr_queue.read_text(encoding="utf-8").splitlines() if line]
    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)

    expected_total = int(sources["totals"]["pages"])
    actual_total = connection.execute("SELECT count(*) FROM pages").fetchone()[0]
    fts_total = connection.execute("SELECT count(*) FROM pages_fts").fetchone()[0]
    if actual_total != expected_total or fts_total != expected_total:
        raise SystemExit(f"FAIL page totals expected={expected_total} pages={actual_total} fts={fts_total}")

    for source in sources["files"]:
        if not Path(source["path"]).is_file():
            raise SystemExit(f"FAIL source unavailable: {source['path']}")
        count = connection.execute("SELECT count(*) FROM pages WHERE source_id=?", (source["source_id"],)).fetchone()[0]
        if count != source["pages"]:
            raise SystemExit(f"FAIL {source['source_id']} expected={source['pages']} actual={count}")

    queued_rows = connection.execute("SELECT count(*) FROM pages WHERE needs_ocr=1").fetchone()[0]
    if queued_rows != len(queue) or extraction["ocr_queue_count"] != len(queue):
        raise SystemExit(
            f"FAIL OCR queue mismatch database={queued_rows} queue={len(queue)} manifest={extraction['ocr_queue_count']}"
        )
    for item in queue:
        row = connection.execute(
            "SELECT needs_ocr, ocr_reason, ocr_strategy FROM pages WHERE source_id=? AND pdf_page=?",
            (item["source_id"], item["pdf_page"]),
        ).fetchone()
        if not row or row[0] != 1 or row[1:] != (item["reason"], item["strategy"]):
            raise SystemExit(f"FAIL invalid queue linkage: {item['source_id']} page {item['pdf_page']}")

    probes = {}
    for term in ("Cibotium", "Cinnamomum"):
        probes[term] = connection.execute(
            "SELECT source_id, pdf_page FROM pages_fts WHERE pages_fts MATCH ? LIMIT 3", (term,)
        ).fetchall()
        if not probes[term]:
            raise SystemExit(f"FAIL FTS probe returned no result: {term}")

    quality = dict(connection.execute("SELECT quality, count(*) FROM pages GROUP BY quality").fetchall())
    connection.close()
    print(json.dumps({
        "valid": True,
        "pages": actual_total,
        "fts_pages": fts_total,
        "ocr_queue": len(queue),
        "quality": quality,
        "probes": probes,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
