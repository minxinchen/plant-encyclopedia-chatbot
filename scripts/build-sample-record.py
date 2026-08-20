#!/usr/bin/env python3
"""Build the first plant record from already reviewed sample evidence."""

from __future__ import annotations

import json
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def main() -> None:
    pages = json.loads((LAB / "data/volume-4-prototype-pages.json").read_text(encoding="utf-8"))["pages"]
    names = json.loads((LAB / "data/sample-name-resolution.json").read_text(encoding="utf-8"))["records"]
    page = {item["pdf_page"]: item for item in pages}
    name = next(item for item in names if item["query_scientific_name"] == "Cibotium barometz")

    p30 = page[30]["text"]
    taxonomy_end = p30.index("Beschreibung:")
    p31 = page[31]["text"]
    distribution_start = p31.index("Yorkommeii")
    distribution_tail = "und die malayische Region."
    distribution_end = p31.index(distribution_tail, distribution_start) + len(distribution_tail)
    p32 = page[32]["text"]
    anatomy_start = p32.index("Anatomie:")
    anatomy_tail = "Inhalte her."
    anatomy_end = p32.index(anatomy_tail, anatomy_start) + len(anatomy_tail)
    p79 = page[79]["text"]

    record = {
        "record_id": "kohler-v4-cibotium-barometz",
        "book_taxon": {
            "scientific_name": "Cibotium Barometz",
            "authorship": "(L.) J. Smith",
            "aliases": ["Aspidium Barometz", "Nephrodium Barometz", "Dicksonia Barometz"],
            "book_common_names": ["Baranetz-Baumfarn", "Baromez-Baumfarn"],
        },
        "display_name": name["display_name_zh_tw"],
        "name_resolution": {
            "status": "taiwan_catalogue_preferred",
            "query_name": name["query_scientific_name"],
            "accepted_scientific_name": "Cibotium barometz (L.) J.Sm.",
            "taiwan_occurrence_status": name["taiwan_occurrence_status"],
            "checked_at": "2026-08-03T12:57:00+08:00",
            "sources": [
                {
                    "authority": source["authority"],
                    "url": source["url"],
                    "source_id": None,
                    "result": source["result"],
                }
                for source in name["sources"]
            ],
        },
        "book_evidence": [
            {"source_id": "kohler-volume-4", "pdf_page": 30, "printed_page": "11", "evidence_type": "text", "ocr_quality": "usable"},
            {"source_id": "kohler-volume-4", "pdf_page": 31, "printed_page": None, "evidence_type": "text", "ocr_quality": "poor"},
            {"source_id": "kohler-volume-4", "pdf_page": 79, "printed_page": None, "evidence_type": "plate", "ocr_quality": "poor"},
            {"source_id": "kohler-volume-4", "pdf_page": 32, "printed_page": "11", "evidence_type": "text", "ocr_quality": "usable"},
        ],
        "sections": [
            {
                "section_type": "taxonomy",
                "original_text": p30[:taxonomy_end].strip(),
                "normalized_text": None,
                "zh_tw_rendering": None,
                "evidence_indexes": [0],
            },
            {
                "section_type": "description",
                "original_text": p30[taxonomy_end:].strip(),
                "normalized_text": None,
                "zh_tw_rendering": "本書描述其具有短而強壯的地生莖，葉長可達二公尺以上；葉柄基部密覆長而金黃色的鱗片狀毛。細部形態仍以德文原文及圖版為準。",
                "evidence_indexes": [0],
            },
            {
                "section_type": "anatomy",
                "original_text": p32[anatomy_start:anatomy_end].strip(),
                "normalized_text": "Anatomie: Die Hauptmenge der als Penawar Djambi bezeichneten Sorte liefert Cibotium Barometz. Sie bildet glänzend goldgelbe bis gelbbräunliche, 3–7 cm lange, ziemlich gerade, aus einer einfachen Zellreihe bestehende Haare. Die einzelnen Zellen sind 400–600 µm lang und 20–45 µm breit. Ihre Querwände sind stark wellig und alle Wände sehr dünn. Beim Trocknen fallen die Zellen häufig zusammen, sodass die Haare bandartig erscheinen. Der ungeformte Inhalt wird mit Alkali orangerot.",
                "zh_tw_rendering": "本書在解剖段落指出，Penawar Djambi 品種主要由 Cibotium Barometz 提供；其毛呈亮金黃色至黃褐色，長 3–7 公分，相當筆直，由單列細胞構成。單一細胞長 400–600 微米、寬 20–45 微米；細胞橫壁強烈波狀且各壁很薄，乾燥時常因細胞塌陷而呈帶狀。其無定形內容物遇鹼會轉為橙紅色。",
                "evidence_indexes": [3],
            },
            {
                "section_type": "distribution",
                "original_text": p31[distribution_start:distribution_end].strip(),
                "normalized_text": "Cibotium Barometz ist ein mächtiger Waldfarn des östlichen Asiens. Er wächst von Assam und dem südlichen China als nördliche Verbreitungsgrenze südlich bis Formosa und die malayische Region.",
                "zh_tw_rendering": "本書記載阿薩姆與中國南部為其分布北界，向南延伸至福爾摩沙及馬來地區。",
                "evidence_indexes": [1],
            },
            {
                "section_type": "plate_description",
                "original_text": p79.strip(),
                "normalized_text": "Tafel 11. Cibotium Barometz (L.) J. Smith.",
                "zh_tw_rendering": "圖版 11：Cibotium barometz。",
                "evidence_indexes": [2],
            },
        ],
        "review_status": "sample_reviewed",
        "warnings": [
            "PDF page 31 embedded text has scrambled reading order; distribution was adjudicated from the page image.",
            "Only the opening Anatomie paragraph on PDF page 32 is promoted; its constituents, historical medicinal use and upholstery-use paragraphs remain unreviewed and excluded.",
            "This record is a bounded prototype, not evidence that the full volume has been processed.",
        ],
    }
    output = LAB / "data/records/cibotium-barometz.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
