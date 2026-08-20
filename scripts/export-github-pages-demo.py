#!/usr/bin/env python3
"""Export approved Köhler records into a public, summary-only demo dataset.

Author: Codex (GPT-5)
Date: 2026-08-13
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
RECORDS_DIR = ROOT / "data" / "records"
OUTPUT = ROOT / "demo-site" / "data" / "knowledge.json"


def citation_for(record: dict, evidence_index: int) -> dict:
    evidence = record["book_evidence"][evidence_index]
    return {
        "source_id": evidence["source_id"],
        "pdf_page": evidence["pdf_page"],
        "printed_page": evidence.get("printed_page"),
        "evidence_type": evidence["evidence_type"],
    }


def public_record(record: dict) -> dict:
    resolution = record["name_resolution"]
    taxon = record["book_taxon"]
    name_sources = [
        {
            "authority": source["authority"],
            "url": source["url"],
            "result": source["result"],
        }
        for source in resolution.get("sources", [])
    ]
    aliases = [
        taxon.get("scientific_name"),
        resolution.get("accepted_scientific_name"),
        *taxon.get("aliases", []),
        *taxon.get("book_common_names", []),
    ]
    aliases = list(dict.fromkeys(item for item in aliases if item))

    sections = []
    for section in record["sections"]:
        if not section.get("zh_tw_rendering"):
            continue
        sections.append(
            {
                "section_type": section["section_type"],
                "zh_tw_rendering": section["zh_tw_rendering"],
                "citations": [
                    citation_for(record, index)
                    for index in section["evidence_indexes"]
                ],
            }
        )

    return {
        "record_id": record["record_id"],
        "display_name": record["display_name"],
        "book_scientific_name": taxon["scientific_name"],
        "accepted_scientific_name": resolution.get("accepted_scientific_name"),
        "aliases": aliases,
        "name_status": resolution["status"],
        "taiwan_occurrence_status": resolution.get("taiwan_occurrence_status"),
        "name_sources": name_sources,
        "sections": sections,
        "warnings": record.get("warnings", []),
    }


def main() -> None:
    records = []
    for path in sorted(RECORDS_DIR.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("review_status") != "sample_reviewed":
            continue
        records.append(public_record(record))

    now = datetime.now(ZoneInfo("Asia/Taipei")).replace(microsecond=0).isoformat()
    payload = {
        "meta": {
            "version": "0.1.0",
            "generated_at": now,
            "language": "zh-TW",
            "answer_language": "zh-TW",
            "approved_records": len(records),
            "image_reasoning": False,
            "medical_advice": False,
            "source_policy": "Kohler book facts plus separate Taiwan public-name metadata",
            "author": "Nio (Master)",
            "generated_by": "Codex (GPT-5)",
        },
        "records": records,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"exported {len(records)} approved records to {OUTPUT}")


if __name__ == "__main__":
    main()
