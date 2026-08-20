#!/usr/bin/env python3
"""Rank bounded OCR-clean plant entries for the next production batch.

The selector is deliberately conservative: a candidate must start with a taxon heading,
end at the next heading within the configured page limit, use only usable/clean pages, and
not overlap an already approved parent page.

author: Codex (GPT-5)
date: 2026-08-12
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")
BINOMIAL = re.compile(r"\b([A-Z][A-Za-z]+\s+[A-Z][A-Za-z-]+|[A-Z][A-Za-z]+\s+[a-z][A-Za-z-]+)\b")


def compact_heading(text: str, family_at: int) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text[:family_at].splitlines() if line.strip()]
    return " | ".join(lines[:6])[:280]


def title_from_heading(heading: str) -> str | None:
    match = BINOMIAL.search(heading)
    return match.group(0) if match else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fulltext-db", type=Path, default=LAB / "data/fulltext/kohler-pages.sqlite")
    parser.add_argument("--main-db", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    parser.add_argument("--max-pages", type=int, default=6)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 6:
        raise SystemExit("max-pages must be between 1 and 6")

    source = sqlite3.connect(f"file:{args.fulltext_db}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    indexed = sqlite3.connect(f"file:{args.main_db}?mode=ro", uri=True)
    approved_pages = {
        (row[0], row[1])
        for row in indexed.execute(
            "SELECT DISTINCT source_id, pdf_page FROM embedding_chunks WHERE review_status='approved'"
        )
    }
    indexed.close()

    headings: dict[str, list[dict]] = {}
    rows = source.execute(
        "SELECT source_id, volume, pdf_page, best_text, quality, character_count, alpha_ratio, short_line_ratio "
        "FROM pages WHERE quality IN ('clean','usable') ORDER BY source_id, pdf_page"
    )
    for row in rows:
        family_at = row["best_text"].find("Familie:")
        if family_at < 0 or family_at > 1000:
            continue
        heading = compact_heading(row["best_text"], family_at)
        title = title_from_heading(heading)
        if title is None:
            continue
        headings.setdefault(row["source_id"], []).append({
            "source_id": row["source_id"],
            "volume": row["volume"],
            "start_pdf_page": row["pdf_page"],
            "book_taxon_candidate": title,
            "heading_excerpt": heading,
        })

    candidates = []
    for source_id, group in headings.items():
        for position, heading in enumerate(group[:-1]):
            next_page = group[position + 1]["start_pdf_page"]
            span = next_page - heading["start_pdf_page"]
            if not 1 <= span <= args.max_pages:
                continue
            page_end = next_page - 1
            page_rows = source.execute(
                "SELECT pdf_page, quality, character_count, alpha_ratio, short_line_ratio, best_text "
                "FROM pages WHERE source_id=? AND pdf_page BETWEEN ? AND ? ORDER BY pdf_page",
                (source_id, heading["start_pdf_page"], page_end),
            ).fetchall()
            if len(page_rows) != span or any(row["quality"] not in {"clean", "usable"} for row in page_rows):
                continue
            pages = [row["pdf_page"] for row in page_rows]
            if any((source_id, page) in approved_pages for page in pages):
                continue
            mean_alpha = sum(row["alpha_ratio"] for row in page_rows) / span
            mean_short = sum(row["short_line_ratio"] for row in page_rows) / span
            mean_chars = sum(row["character_count"] for row in page_rows) / span
            boundary_text = page_rows[-1]["best_text"][-1200:]
            has_closing_section = any(
                marker in boundary_text
                for marker in ("Drogen und Praparate", "Drogen und Präparate", "Tafelbeschreibung", "Literatur")
            )
            score = (
                100
                + 24 * mean_alpha
                - 28 * mean_short
                - 2.5 * abs(span - 3)
                + (5 if has_closing_section else 0)
                + min(mean_chars / 2500, 3)
            )
            candidates.append({
                **heading,
                "end_pdf_page": page_end,
                "pdf_pages": pages,
                "page_count": span,
                "next_entry_pdf_page": next_page,
                "next_entry_taxon_candidate": group[position + 1]["book_taxon_candidate"],
                "mean_alpha_ratio": round(mean_alpha, 6),
                "mean_short_line_ratio": round(mean_short, 6),
                "mean_character_count": round(mean_chars),
                "closing_section_marker": has_closing_section,
                "score": round(score, 6),
                "status": "candidate_requires_name_and_visual_review",
            })
    source.close()
    candidates.sort(key=lambda item: (-item["score"], item["page_count"], item["source_id"], item["start_pdf_page"]))
    result = {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "selection_contract": {
            "maximum_adjacent_pages": args.max_pages,
            "source_quality": ["clean", "usable"],
            "requires_next_entry_boundary": True,
            "excludes_approved_pages": True,
            "still_requires": ["Taiwan name resolution", "PDF image review", "source-range validation"],
        },
        "candidate_count": len(candidates),
        "candidates": candidates[: args.limit],
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
