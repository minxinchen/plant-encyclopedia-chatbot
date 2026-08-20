#!/usr/bin/env python3
"""Freeze the four-volume corpus into mutually exclusive local-agent shards.

The output is maker-only staging. It never changes source PDFs, canonical records,
chunks, embeddings, or SQLite databases.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")
BINOMIAL = re.compile(
    r"\b([A-Z][A-Za-z]+\s+[A-Z][A-Za-z-]+|[A-Z][A-Za-z]+\s+[a-z][A-Za-z-]+)\b"
)
SHARDS = (
    ("S01", "kohler-volume-1", 1, 1, 120),
    ("S02", "kohler-volume-1", 1, 121, 410),
    ("S03", "kohler-volume-2", 2, 1, 280),
    ("S04", "kohler-volume-2", 2, 281, 504),
    ("S05", "kohler-volume-2", 2, 505, 738),
    ("S06", "kohler-volume-3", 3, 1, 204),
    ("S07", "kohler-volume-3", 3, 205, 536),
    ("S08", "kohler-volume-4", 4, 1, 90),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(rendered, encoding="utf-8")
    return sha256_text(rendered)


def compact_heading(text: str, family_at: int) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text[:family_at].splitlines() if line.strip()]
    return " | ".join(lines[:6])[:500]


def title_from_heading(heading: str) -> str | None:
    match = BINOMIAL.search(heading)
    return match.group(0) if match else None


def owner_for(source_id: str, page: int) -> str:
    for shard_id, shard_source, _, start, end in SHARDS:
        if source_id == shard_source and start <= page <= end:
            return shard_id
    raise ValueError(f"no shard owns {source_id}:p{page}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fulltext-db", type=Path, default=LAB / "data/fulltext/kohler-pages.sqlite")
    parser.add_argument("--main-db", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    parser.add_argument("--source-manifest", type=Path, default=LAB / "data/source-manifest.json")
    parser.add_argument("--output-root", type=Path, default=LAB / "data/candidates/preembedding-v1")
    parser.add_argument("--max-entry-pages", type=int, default=6)
    parser.add_argument("--skip-source-hash", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_entry_pages <= 12:
        raise SystemExit("max-entry-pages must be between 1 and 12")

    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    sources = {}
    for item in source_manifest["files"]:
        source_path = Path(item["path"])
        if not source_path.is_file() or source_path.stat().st_size != item["bytes"]:
            raise SystemExit(f"source unavailable or byte-size mismatch: {source_path}")
        sources[item["source_id"]] = {
            "source_id": item["source_id"],
            "volume": item["volume"],
            "pages": item["pages"],
            "bytes": item["bytes"],
            "sha256": None if args.skip_source_hash else sha256_file(source_path),
            "source_locator": f"volume-{item['volume']}",
        }

    with sqlite3.connect(f"file:{args.main_db}?mode=ro", uri=True) as db:
        approved_pages = {
            (row[0], row[1])
            for row in db.execute(
                "SELECT DISTINCT source_id, pdf_page FROM embedding_chunks WHERE review_status='approved'"
            )
        }

    with sqlite3.connect(f"file:{args.fulltext_db}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        page_rows = [dict(row) for row in db.execute(
            "SELECT source_id, volume, pdf_page, page_type, best_text, best_method, "
            "character_count, word_count, line_count, alpha_ratio, short_line_ratio, quality, "
            "needs_ocr, ocr_reason, ocr_strategy, ocr_priority, ocr_status, review_status "
            "FROM pages ORDER BY source_id, pdf_page"
        )]

    pages_by_key = {(row["source_id"], row["pdf_page"]): row for row in page_rows}
    headings: dict[str, list[dict]] = {}
    for row in page_rows:
        family_at = row["best_text"].find("Familie:")
        if family_at < 0 or family_at > 1000:
            continue
        heading_excerpt = compact_heading(row["best_text"], family_at)
        title = title_from_heading(heading_excerpt)
        if not title:
            continue
        headings.setdefault(row["source_id"], []).append({
            "source_id": row["source_id"],
            "volume": row["volume"],
            "start_pdf_page": row["pdf_page"],
            "book_taxon_candidate": title,
            "heading_excerpt": heading_excerpt,
            "heading_text_sha256": sha256_text(heading_excerpt),
            "start_page_quality": row["quality"],
        })

    entries = []
    for source_id, group in headings.items():
        volume_pages = sources[source_id]["pages"]
        for index, heading in enumerate(group):
            next_heading = group[index + 1] if index + 1 < len(group) else None
            end_page = (next_heading["start_pdf_page"] - 1) if next_heading else volume_pages
            span = end_page - heading["start_pdf_page"] + 1
            pages = list(range(heading["start_pdf_page"], end_page + 1))
            selected_rows = [pages_by_key[(source_id, page)] for page in pages]
            page_qualities = Counter(row["quality"] for row in selected_rows)
            overlaps = [page for page in pages if (source_id, page) in approved_pages]
            if next_heading is None:
                disposition = "hold_terminal_no_next_heading"
            elif overlaps:
                disposition = "already_approved_overlap"
            elif span > args.max_entry_pages:
                disposition = "hold_span_over_limit"
            elif any(row["quality"] not in {"clean", "usable"} for row in selected_rows):
                disposition = "hold_page_quality"
            else:
                disposition = "eligible_local_structure"
            entry_id = f"{source_id}:p{heading['start_pdf_page']:04d}-p{end_page:04d}"
            entries.append({
                "schema_version": "1.0",
                "entry_id": entry_id,
                "owner_shard": owner_for(source_id, heading["start_pdf_page"]),
                **heading,
                "end_pdf_page": end_page,
                "pdf_pages": pages,
                "page_count": span,
                "next_entry_pdf_page": next_heading["start_pdf_page"] if next_heading else None,
                "next_entry_taxon_candidate": next_heading["book_taxon_candidate"] if next_heading else None,
                "page_quality_counts": dict(page_qualities),
                "approved_overlap_pages": overlaps,
                "disposition": disposition,
                "review_status": "candidate",
                "taiwan_name_status": "unresolved",
                "requires_visual_review": bool(
                    any(row["needs_ocr"] for row in selected_rows)
                    or disposition in {"hold_terminal_no_next_heading", "hold_page_quality"}
                ),
            })

    root = args.output_root
    shard_summaries = []
    global_work_ids: set[str] = set()
    for shard_id, source_id, volume, start_page, end_page in SHARDS:
        shard_root = root / "shards" / shard_id
        frozen_pages = []
        for row in page_rows:
            if row["source_id"] != source_id or not start_page <= row["pdf_page"] <= end_page:
                continue
            frozen = {key: value for key, value in row.items() if key != "best_text"}
            frozen["text"] = row["best_text"]
            frozen["text_sha256"] = sha256_text(row["best_text"])
            frozen["owner_shard"] = shard_id
            frozen_pages.append(frozen)
        frozen_entries = [entry for entry in entries if entry["owner_shard"] == shard_id]
        ocr_pages = [page for page in frozen_pages if page["needs_ocr"]]
        tasks = []
        for page in ocr_pages:
            work_id = f"ocr:{page['source_id']}:p{page['pdf_page']:04d}:v1"
            tasks.append({
                "schema_version": "1.0",
                "work_id": work_id,
                "stage": "ocr_candidate",
                "owner_shard": shard_id,
                "source_id": page["source_id"],
                "volume": page["volume"],
                "pdf_pages": [page["pdf_page"]],
                "input_text_sha256": page["text_sha256"],
                "status": "planned",
                "route": {"maker": "apple-vision-ocr", "fallback": "hold_for_visual_review"},
                "forbidden": ["canonical_sqlite_write", "source_pdf_write", "external_api"],
            })
        for entry in frozen_entries:
            stage = "local_structure" if entry["disposition"] == "eligible_local_structure" else "boundary_review"
            work_id = f"{stage}:{entry['entry_id']}:v1"
            tasks.append({
                "schema_version": "1.0",
                "work_id": work_id,
                "stage": stage,
                "owner_shard": shard_id,
                "source_id": entry["source_id"],
                "volume": entry["volume"],
                "entry_id": entry["entry_id"],
                "pdf_pages": entry["pdf_pages"],
                "status": "planned",
                "route": {
                    "maker": "local-llm-candidate" if stage == "local_structure" else "deterministic-plus-agent-review",
                    "reviewer": "deterministic-source-span-validator",
                },
                "forbidden": [
                    "taiwan_name_invention", "review_promotion", "canonical_sqlite_write",
                    "source_pdf_write", "external_api", "image_claim",
                ],
            })
        duplicates = global_work_ids.intersection(task["work_id"] for task in tasks)
        if duplicates:
            raise SystemExit(f"duplicate work ids: {sorted(duplicates)}")
        global_work_ids.update(task["work_id"] for task in tasks)
        for relative in (
            "maker/structure-drafts", "maker/ocr-candidates", "maker/chunks-candidate",
            "maker/test-drafts", "checks",
        ):
            (shard_root / relative).mkdir(parents=True, exist_ok=True)
        hashes = {
            "pages_jsonl_sha256": write_jsonl(shard_root / "inputs/pages.jsonl", frozen_pages),
            "entries_jsonl_sha256": write_jsonl(shard_root / "inputs/entries.jsonl", frozen_entries),
            "ocr_jsonl_sha256": write_jsonl(shard_root / "inputs/ocr-pages.jsonl", ocr_pages),
            "work_items_jsonl_sha256": write_jsonl(shard_root / "inputs/work-items.jsonl", tasks),
        }
        disposition_counts = Counter(entry["disposition"] for entry in frozen_entries)
        shard_manifest = {
            "schema_version": "1.0",
            "shard_id": shard_id,
            "source_id": source_id,
            "volume": volume,
            "pdf_page_range": [start_page, end_page],
            "page_count": len(frozen_pages),
            "entry_count": len(frozen_entries),
            "eligible_local_structure_count": disposition_counts["eligible_local_structure"],
            "ocr_page_count": len(ocr_pages),
            "work_item_count": len(tasks),
            "disposition_counts": dict(disposition_counts),
            "input_hashes": hashes,
            "write_scope": str(shard_root / "maker"),
            "canonical_write_allowed": False,
        }
        write_json(shard_root / "manifest.json", shard_manifest)
        shard_summaries.append(shard_manifest)

    source_receipt = sorted(sources.values(), key=lambda item: item["volume"])
    write_json(root / "source-receipt.json", {"schema_version": "1.0", "sources": source_receipt})
    summary = {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "pipeline_id": "preembedding-v1",
        "purpose": "local-only maker staging before embedding",
        "limits": {
            "maximum_entry_pages": args.max_entry_pages,
            "maximum_concurrent_text_workers": 2,
            "maximum_concurrent_ocr_workers": 1,
            "maximum_concurrent_model_requests": 1,
        },
        "totals": {
            "pages": sum(item["page_count"] for item in shard_summaries),
            "detected_entries": len(entries),
            "eligible_local_structure_entries": sum(item["eligible_local_structure_count"] for item in shard_summaries),
            "eligible_candidate_pages": sum(
                entry["page_count"] for entry in entries if entry["disposition"] == "eligible_local_structure"
            ),
            "ocr_pages": sum(item["ocr_page_count"] for item in shard_summaries),
            "approved_pages_excluded": len(approved_pages),
            "work_items": sum(item["work_item_count"] for item in shard_summaries),
        },
        "disposition_counts": dict(Counter(entry["disposition"] for entry in entries)),
        "shards": shard_summaries,
        "canonical_write_allowed": False,
        "embedding_calls_allowed": False,
        "external_api_calls_allowed": False,
        "promotion_rule": "Only the single integrator may promote deterministic-pass staging artifacts after Taiwan-name and visual gates.",
    }
    write_json(root / "manifest.json", summary)
    print(json.dumps(summary["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
