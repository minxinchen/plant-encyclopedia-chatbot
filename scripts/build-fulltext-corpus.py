#!/usr/bin/env python3
"""Extract every PDF page into a rebuildable SQLite full-text corpus."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")


def extract_volume(item: dict) -> tuple[dict, list[str], float]:
    source = Path(item["path"])
    if not source.is_file():
        raise FileNotFoundError(source)
    started = datetime.now(TZ)
    proc = subprocess.run(
        ["pdftotext", "-layout", str(source), "-"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=3600,
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    expected = int(item["pages"])
    if len(pages) != expected:
        raise RuntimeError(
            f"{item['source_id']} page boundary mismatch: expected {expected}, extracted {len(pages)}"
        )
    elapsed = (datetime.now(TZ) - started).total_seconds()
    return item, pages, elapsed


def classify_page(text: str, pdf_page: int, total_pages: int) -> dict:
    stripped = text.strip()
    characters = len(stripped)
    words = re.findall(r"\b[^\W_]+(?:[-'][^\W_]+)*\b", stripped, flags=re.UNICODE)
    nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]
    short_lines = [line for line in nonempty_lines if len(line) <= 4]
    short_line_ratio = len(short_lines) / len(nonempty_lines) if nonempty_lines else 1.0
    alpha_count = sum(char.isalpha() for char in stripped)
    alpha_ratio = alpha_count / characters if characters else 0.0

    if characters < 8:
        quality = "empty"
    elif characters < 120 or len(words) < 18:
        quality = "low_text"
    elif short_line_ratio > 0.38:
        quality = "fragmented"
    else:
        quality = "usable"

    lowered = stripped.lower()
    has_plate_marker = bool(re.search(r"\btafel\s+[ivxlcdm0-9]+\b", lowered))
    has_scientific_caption = bool(
        re.search(r"\b[A-Z][a-z]{3,}\s+[a-z][a-z-]{2,}\b", stripped)
    )
    if quality == "empty":
        page_type = "blank_or_image"
    elif has_plate_marker or (characters < 320 and has_scientific_caption):
        page_type = "plate_or_caption"
    elif pdf_page <= 12 and any(marker in lowered for marker in ("inhalt", "verzeichnis", "register", "band ")):
        page_type = "frontmatter_or_index"
    elif any(marker in lowered for marker in ("sachregister", "namenregister", "literatur-verzeichnis")):
        page_type = "index"
    else:
        page_type = "text"

    needs_ocr = quality in {"empty", "low_text", "fragmented"} or page_type == "plate_or_caption"
    if quality == "fragmented":
        ocr_reason = "layout_fragmentation"
        ocr_strategy = "full_page_layout_ocr"
        priority = 1
    elif quality == "low_text":
        ocr_reason = "insufficient_embedded_text"
        ocr_strategy = "full_page_ocr"
        priority = 2
    elif page_type == "plate_or_caption":
        ocr_reason = "plate_caption"
        ocr_strategy = "caption_ocr"
        priority = 3
    elif quality == "empty":
        ocr_reason = "blank_or_image_check"
        ocr_strategy = "visual_text_check"
        priority = 4 if pdf_page < total_pages - 10 else 5
    else:
        ocr_reason = None
        ocr_strategy = None
        priority = None

    return {
        "character_count": characters,
        "word_count": len(words),
        "line_count": len(nonempty_lines),
        "alpha_ratio": round(alpha_ratio, 4),
        "short_line_ratio": round(short_line_ratio, 4),
        "quality": quality,
        "page_type": page_type,
        "needs_ocr": int(needs_ocr),
        "ocr_reason": ocr_reason,
        "ocr_strategy": ocr_strategy,
        "ocr_priority": priority,
    }


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        DROP TABLE IF EXISTS pages_fts;
        DROP TABLE IF EXISTS pages;
        DROP TABLE IF EXISTS corpus_meta;

        CREATE TABLE pages (
          id INTEGER PRIMARY KEY,
          source_id TEXT NOT NULL,
          volume INTEGER NOT NULL,
          pdf_page INTEGER NOT NULL,
          source_path TEXT NOT NULL,
          page_type TEXT NOT NULL,
          embedded_text TEXT NOT NULL,
          ocr_text TEXT,
          best_text TEXT NOT NULL,
          best_method TEXT NOT NULL,
          character_count INTEGER NOT NULL,
          word_count INTEGER NOT NULL,
          line_count INTEGER NOT NULL,
          alpha_ratio REAL NOT NULL,
          short_line_ratio REAL NOT NULL,
          quality TEXT NOT NULL,
          needs_ocr INTEGER NOT NULL,
          ocr_reason TEXT,
          ocr_strategy TEXT,
          ocr_priority INTEGER,
          ocr_status TEXT NOT NULL DEFAULT 'pending',
          review_status TEXT NOT NULL DEFAULT 'candidate',
          UNIQUE(source_id, pdf_page)
        );

        CREATE VIRTUAL TABLE pages_fts USING fts5(
          best_text,
          source_id UNINDEXED,
          volume UNINDEXED,
          pdf_page UNINDEXED,
          tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TABLE corpus_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, default=LAB / "data/source-manifest.json")
    parser.add_argument("--database", type=Path, default=LAB / "data/fulltext/kohler-pages.sqlite")
    parser.add_argument("--manifest", type=Path, default=LAB / "data/fulltext/extraction-manifest.json")
    parser.add_argument("--ocr-queue", type=Path, default=LAB / "data/fulltext/ocr-queue.jsonl")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    sources = sorted(source_manifest["files"], key=lambda item: item["volume"])
    args.database.parent.mkdir(parents=True, exist_ok=True)

    extracted: dict[str, tuple[dict, list[str], float]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(sources)))) as executor:
        futures = {executor.submit(extract_volume, item): item for item in sources}
        for future in as_completed(futures):
            item, pages, elapsed = future.result()
            extracted[item["source_id"]] = (item, pages, elapsed)
            print(json.dumps({
                "source_id": item["source_id"],
                "pages": len(pages),
                "elapsed_seconds": round(elapsed, 1),
            }))

    connection = sqlite3.connect(args.database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    create_schema(connection)
    counters: dict[str, Counter] = {}
    queue: list[dict] = []

    for source in sources:
        item, page_texts, elapsed = extracted[source["source_id"]]
        counts = Counter()
        for page_number, text in enumerate(page_texts, 1):
            metrics = classify_page(text, page_number, int(item["pages"]))
            cursor = connection.execute(
                """
                INSERT INTO pages (
                  source_id, volume, pdf_page, source_path, page_type,
                  embedded_text, ocr_text, best_text, best_method,
                  character_count, word_count, line_count, alpha_ratio,
                  short_line_ratio, quality, needs_ocr, ocr_reason,
                  ocr_strategy, ocr_priority, ocr_status, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'embedded_text', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate')
                """,
                (
                    item["source_id"], item["volume"], page_number, item["path"], metrics["page_type"],
                    text, text, metrics["character_count"], metrics["word_count"], metrics["line_count"],
                    metrics["alpha_ratio"], metrics["short_line_ratio"], metrics["quality"], metrics["needs_ocr"],
                    metrics["ocr_reason"], metrics["ocr_strategy"], metrics["ocr_priority"],
                    "queued" if metrics["needs_ocr"] else "not_required",
                ),
            )
            row_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO pages_fts(rowid, best_text, source_id, volume, pdf_page) VALUES (?, ?, ?, ?, ?)",
                (row_id, text, item["source_id"], item["volume"], page_number),
            )
            counts[metrics["quality"]] += 1
            counts[metrics["page_type"]] += 1
            if metrics["needs_ocr"]:
                queue.append({
                    "source_id": item["source_id"],
                    "volume": item["volume"],
                    "pdf_page": page_number,
                    "source_path": item["path"],
                    "priority": metrics["ocr_priority"],
                    "reason": metrics["ocr_reason"],
                    "strategy": metrics["ocr_strategy"],
                    "embedded_character_count": metrics["character_count"],
                    "status": "queued",
                })
        counts["pages"] = len(page_texts)
        counts["elapsed_seconds"] = round(elapsed, 1)
        counters[item["source_id"]] = counts

    built_at = datetime.now(TZ).isoformat(timespec="seconds")
    connection.execute("INSERT INTO corpus_meta(key, value) VALUES ('built_at', ?)", (built_at,))
    connection.execute("INSERT INTO corpus_meta(key, value) VALUES ('schema_version', '1.0')")
    connection.commit()
    connection.execute("PRAGMA optimize")
    connection.close()

    queue.sort(key=lambda item: (item["priority"], item["volume"], item["pdf_page"]))
    args.ocr_queue.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in queue),
        encoding="utf-8",
    )

    result = {
        "schema_version": "1.0",
        "built_at": built_at,
        "source_manifest": str(args.source_manifest.resolve()),
        "database": str(args.database.resolve()),
        "extraction_method": "one-pass Poppler pdftotext -layout per volume, split by PDF page boundary",
        "total_pages": sum(counter["pages"] for counter in counters.values()),
        "total_sources": len(sources),
        "ocr_queue_count": len(queue),
        "ocr_complete": len(queue) == 0,
        "volumes": [
            {"source_id": item["source_id"], "volume": item["volume"], **dict(counters[item["source_id"]])}
            for item in sources
        ],
    }
    args.manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "complete",
        "pages": result["total_pages"],
        "ocr_queue": result["ocr_queue_count"],
        "database": str(args.database),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
