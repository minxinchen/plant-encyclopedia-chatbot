#!/usr/bin/env python3
"""Apply source-backed gates to a generated plant record without third-party packages."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    lab = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("record", nargs="?", type=Path, default=lab / "data/records/cibotium-barometz.json")
    args = parser.parse_args()
    record = json.loads(args.record.read_text(encoding="utf-8"))
    schema = json.loads((lab / "schemas/plant-record.schema.json").read_text(encoding="utf-8"))
    page_text: dict[tuple[str, int], list[str]] = {}
    prototype_path = lab / "data/volume-4-prototype-pages.json"
    if prototype_path.is_file():
        pages = json.loads(prototype_path.read_text(encoding="utf-8"))["pages"]
        for page in pages:
            page_text.setdefault((page["source_id"], page["pdf_page"]), []).append(page["text"])
    corpus_path = lab / "data/fulltext/kohler-pages.sqlite"
    with sqlite3.connect(corpus_path) as connection:
        for source_id, pdf_page, text in connection.execute(
            "SELECT source_id, pdf_page, best_text FROM pages"
        ):
            page_text.setdefault((source_id, pdf_page), []).append(text)

    required = {"record_id", "book_taxon", "display_name", "name_resolution", "book_evidence", "sections", "review_status"}
    if missing := sorted(required - record.keys()):
        raise SystemExit(f"FAIL missing fields: {', '.join(missing)}")
    if not record["name_resolution"]["sources"] or not record["display_name"]:
        raise SystemExit("FAIL display name lacks public-source evidence")
    allowed_section_types = set(schema["properties"]["sections"]["items"]["properties"]["section_type"]["enum"])
    for section in record["sections"]:
        if section["section_type"] not in allowed_section_types:
            raise SystemExit(f"FAIL unknown section_type: {section['section_type']}")
        if not section["evidence_indexes"]:
            raise SystemExit(f"FAIL section lacks evidence: {section['section_type']}")
        for index in section["evidence_indexes"]:
            if index >= len(record["book_evidence"]):
                raise SystemExit(f"FAIL evidence index out of range: {section['section_type']}")
            evidence = record["book_evidence"][index]
            sources = page_text.get((evidence["source_id"], evidence["pdf_page"]), [])
            if not any(section["original_text"] in source for source in sources):
                raise SystemExit(f"FAIL original text not found on cited page: {section['section_type']}")
    if not any(e["evidence_type"] in {"plate", "caption"} for e in record["book_evidence"]):
        raise SystemExit("FAIL no plate or caption evidence")
    print(json.dumps({"valid": True, "record_id": record["record_id"], "sections": len(record["sections"]), "evidence_pages": [e["pdf_page"] for e in record["book_evidence"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
