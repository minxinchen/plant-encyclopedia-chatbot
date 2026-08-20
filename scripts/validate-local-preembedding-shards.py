#!/usr/bin/env python3
"""Validate frozen local-agent pre-embedding shards and write-scope isolation.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=LAB / "data/candidates/preembedding-v1")
    args = parser.parse_args()
    manifest = json.loads((args.root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("canonical_write_allowed") or manifest.get("external_api_calls_allowed"):
        fail("maker staging must forbid canonical and external writes")

    page_keys = set()
    entry_ids = set()
    work_ids = set()
    eligible_pages = set()
    ocr_keys = set()
    dispositions = Counter()
    for shard in manifest["shards"]:
        shard_root = args.root / "shards" / shard["shard_id"]
        local_manifest = json.loads((shard_root / "manifest.json").read_text(encoding="utf-8"))
        input_files = {
            "pages_jsonl_sha256": "pages.jsonl",
            "entries_jsonl_sha256": "entries.jsonl",
            "ocr_jsonl_sha256": "ocr-pages.jsonl",
            "work_items_jsonl_sha256": "work-items.jsonl",
        }
        for name, expected in local_manifest["input_hashes"].items():
            path = shard_root / "inputs" / input_files[name]
            if sha256_file(path) != expected:
                fail(f"input hash mismatch: {path}")
        pages = read_jsonl(shard_root / "inputs/pages.jsonl")
        entries = read_jsonl(shard_root / "inputs/entries.jsonl")
        ocr_pages = read_jsonl(shard_root / "inputs/ocr-pages.jsonl")
        tasks = read_jsonl(shard_root / "inputs/work-items.jsonl")
        if len(pages) != shard["page_count"] or len(entries) != shard["entry_count"]:
            fail(f"manifest count mismatch: {shard['shard_id']}")
        for page in pages:
            key = (page["source_id"], page["pdf_page"])
            if key in page_keys or page["owner_shard"] != shard["shard_id"]:
                fail(f"duplicate or wrong page owner: {key}")
            if hashlib.sha256(page["text"].encode("utf-8")).hexdigest() != page["text_sha256"]:
                fail(f"page text hash mismatch: {key}")
            page_keys.add(key)
        for entry in entries:
            if entry["entry_id"] in entry_ids or entry["owner_shard"] != shard["shard_id"]:
                fail(f"duplicate or wrong entry owner: {entry['entry_id']}")
            entry_ids.add(entry["entry_id"])
            dispositions[entry["disposition"]] += 1
            if entry["disposition"] == "eligible_local_structure":
                eligible_pages.update((entry["source_id"], page) for page in entry["pdf_pages"])
        for page in ocr_pages:
            key = (page["source_id"], page["pdf_page"])
            if key in ocr_keys or not page["needs_ocr"]:
                fail(f"duplicate or invalid OCR page: {key}")
            ocr_keys.add(key)
        for task in tasks:
            if task["work_id"] in work_ids or task["owner_shard"] != shard["shard_id"]:
                fail(f"duplicate or wrong work owner: {task['work_id']}")
            if not set(task["forbidden"]).issuperset({"canonical_sqlite_write", "external_api"}):
                fail(f"missing safety boundary: {task['work_id']}")
            work_ids.add(task["work_id"])

    totals = manifest["totals"]
    observed = {
        "pages": len(page_keys),
        "detected_entries": len(entry_ids),
        "eligible_local_structure_entries": dispositions["eligible_local_structure"],
        "eligible_candidate_pages": len(eligible_pages),
        "ocr_pages": len(ocr_keys),
        "work_items": len(work_ids),
    }
    for key, value in observed.items():
        if totals[key] != value:
            fail(f"global count mismatch for {key}: manifest={totals[key]} observed={value}")
    if observed["pages"] != 1774:
        fail("all 1,774 pages must have exactly one shard owner")
    print(json.dumps({"status": "PASS", **observed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
