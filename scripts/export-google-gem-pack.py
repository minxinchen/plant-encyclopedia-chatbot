#!/usr/bin/env python3
"""Export approved plant evidence as a Google Drive / Classic Gem knowledge file."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=Path, default=LAB / "data/records")
    parser.add_argument("--profile", type=Path, default=LAB / "config/embedding-profile.json")
    parser.add_argument("--chunks", type=Path, default=LAB / "data/chunks")
    parser.add_argument("--output", type=Path, default=LAB / "exports/google-gem/approved-evidence-pack.md")
    args = parser.parse_args()

    lines = [
        "# 植物圖文百科：已核准證據包",
        "",
        f"產生時間：{datetime.now(TZ).isoformat(timespec='seconds')}",
        "",
        "## 回答規則",
        "",
        "- 預設使用臺灣繁體中文；英文提問可以用英文回答，但第一次出現植物時仍標示臺灣顯示名稱與學名。",
        "- 植物事實只能來自本檔的書中證據，回答時標示冊別與 PDF 頁碼。",
        "- 阿斯匹靈、metformin 等不屬於科勒書中植物證據的藥物問題，直接說明超出範圍，不使用模型常識或網路補答。",
        "- 臺灣中文名是外部名稱 metadata，不得用來補寫書中未記載的事實。",
        "- 植物名稱以臺灣公開資料優先；簡體中文名稱只能是最後 fallback。",
        "- 找不到已核准證據時回答「現有已核准證據未找到」；不可依模型常識猜測。",
        "- 歷史藥用記載只可描述為書中記載，不得改寫成現代醫療建議。",
        "- embedding 相似只是候選排序；沒有頁面文字支持時不得回答。",
        "",
    ]

    record_count = 0
    display_name_by_record_slug: dict[str, str] = {}
    for path in sorted(args.records.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("review_status") not in {"sample_reviewed", "approved"}:
            continue
        record_count += 1
        display_name_by_record_slug[path.stem] = record.get("display_name") or ""
        display_name_by_record_slug[record["record_id"]] = record.get("display_name") or ""
        taxon = record["book_taxon"]
        lines.extend([
            f"## {record.get('display_name') or '中文名尚未解析'}｜{taxon['scientific_name']}",
            "",
            f"- record_id：`{record['record_id']}`",
            f"- 書中學名：{taxon['scientific_name']} {taxon.get('authorship', '')}".rstrip(),
            f"- 臺灣顯示名稱：{record.get('display_name') or '尚未解析'}",
            f"- 名稱狀態：{record.get('name_resolution', {}).get('status', 'unresolved')}",
            "",
        ])
        evidence = record.get("book_evidence", [])
        for section in record.get("sections", []):
            refs = [evidence[index] for index in section.get("evidence_indexes", [])]
            citations = ", ".join(
                f"{ref['source_id']} PDF p.{ref['pdf_page']}" for ref in refs
            ) or "未提供"
            lines.extend([
                f"### {section['section_type']}",
                "",
                f"引用：{citations}",
                "",
                section.get("zh_tw_rendering") or "（尚無臺灣繁中譯寫；請依原文回答，禁止以簡體譯名冒充臺灣名稱。）",
                "",
                "原文：",
                "",
                section.get("normalized_text") or section.get("original_text", ""),
                "",
            ])

    chunk_count = 0
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    canonical_paths = profile["canonical_chunk_contract"]["approved_profile_paths"]
    for relative_path in canonical_paths:
        chunks_path = LAB / relative_path
        if not chunks_path.is_file():
            raise SystemExit(f"missing canonical approved chunk package: {chunks_path}")
        chunks = [
            json.loads(raw_line) for raw_line in chunks_path.read_text(encoding="utf-8").splitlines()
            if raw_line.strip()
        ]
        if not chunks:
            raise SystemExit(f"empty canonical approved chunk package: {chunks_path}")
        record_ids = {chunk["record_id"] for chunk in chunks}
        scientific_names = {chunk["scientific_name"] for chunk in chunks}
        if len(record_ids) != 1 or len(scientific_names) != 1:
            raise SystemExit(f"mixed record package: {chunks_path}")
        record_id = next(iter(record_ids))
        scientific_name = next(iter(scientific_names))
        display_name = display_name_by_record_slug.get(record_id)
        lines.extend([
            f"## 原文檢索片段｜{scientific_name}",
            "",
            f"- record_id：`{record_id}`",
            f"- 臺灣顯示名稱：{display_name or '尚未解析'}",
            f"- vector_space_id：`{profile['vector_space_id']}`（只供重建檢索，不是事實來源）",
            f"- canonical_package：`{relative_path}`",
            "",
        ])
        for chunk in chunks:
            chunk_count += 1
            lines.extend([
                f"### {chunk['source_id']} PDF p.{chunk['pdf_page']}",
                "",
                f"chunk_id：`{chunk['chunk_id']}`  ",
                f"text_sha256：`{chunk['text_sha256']}`",
                "",
                chunk["source_text"],
                "",
            ])

    if record_count == 0 and chunk_count == 0:
        raise SystemExit("no approved evidence available for export")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "complete",
        "approved_records": record_count,
        "approved_page_chunks": chunk_count,
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
