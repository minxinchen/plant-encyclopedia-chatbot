#!/usr/bin/env python3
"""Export the staged full-book corpus as nine uploadable Google Gem files.

The eight knowledge files follow the frozen pre-embedding shard ownership.  The
instructions file tells a Classic Gem which text is authoritative and keeps
external Taiwan-name metadata separate from facts printed in Köhler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
DEFAULT_OUTPUT = DEFAULT_ROOT / "exports/google-gem/fullbook-beta"
SHARDS = tuple(f"S{i:02d}" for i in range(1, 9))


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_sha(value: dict[str, Any]) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def source_page(record: dict[str, Any]) -> tuple[str, int]:
    for section in record.get("sections", []):
        locator = section.get("source_locator") or {}
        if locator.get("source_id") and locator.get("pdf_page"):
            return str(locator["source_id"]), int(locator["pdf_page"])
    for evidence in record.get("book_evidence", []):
        if evidence.get("source_id") and evidence.get("pdf_page"):
            return str(evidence["source_id"]), int(evidence["pdf_page"])
    raise ValueError(f"record has no source page: {record.get('record_id')}")


def shard_for(source_id: str, page: int) -> str:
    bounds = {
        "kohler-volume-1": ((1, 120, "S01"), (121, 410, "S02")),
        "kohler-volume-2": ((1, 280, "S03"), (281, 504, "S04"), (505, 738, "S05")),
        "kohler-volume-3": ((1, 204, "S06"), (205, 536, "S07")),
        "kohler-volume-4": ((1, 90, "S08"),),
    }
    for start, end, shard in bounds.get(source_id, ()):
        if start <= page <= end:
            return shard
    raise ValueError(f"page outside frozen shard ownership: {source_id} p{page}")


def candidate_records(root: Path) -> list[tuple[Path, dict[str, Any], str]]:
    manifest = read_json(root / "records-candidate/manifest.json")
    base = root / "records-candidate"
    rows: list[tuple[Path, dict[str, Any], str]] = []
    for item in manifest.get("records", []):
        path = base / item["path"]
        rows.append((path, read_json(path), "machine_extracted_candidate"))
    return rows


def approved_records() -> list[tuple[Path, dict[str, Any], str]]:
    return [(path, read_json(path), "approved_baseline") for path in sorted((LAB / "data/records").glob("*.json"))]


def title(record: dict[str, Any]) -> str:
    display = record.get("display_name") or "臺灣名稱尚未解析"
    scientific = (record.get("book_taxon") or {}).get("scientific_name") or "未標示"
    return f"{display}｜{scientific}"


def display_name_scope(record: dict[str, Any], source_kind: str) -> str:
    resolution = record.get("name_resolution") or {}
    if not record.get("display_name"):
        return "unresolved"
    explicit = resolution.get("display_name_source_scope")
    if explicit:
        return str(explicit)
    if source_kind == "approved_baseline":
        return "taiwan_public_name"
    return "unclassified_staging"


def naming_lines(record: dict[str, Any], source_kind: str) -> list[str]:
    resolution = record.get("name_resolution") or {}
    status = resolution.get("terminal_status") or resolution.get("status") or "unresolved"
    lines = [
        f"- 中文顯示名：{record.get('display_name') or '尚未解析；不得猜譯'}",
        f"- 顯示名來源層級：{display_name_scope(record, source_kind)}",
        f"- 名稱解析狀態：{status}",
        f"- 接受學名（外部名稱資料）：{resolution.get('accepted_scientific_name') or '未確認'}",
        f"- 臺灣出現狀態：{resolution.get('taiwan_occurrence_status') or '未主張'}",
    ]
    sources = resolution.get("sources") or []
    if sources:
        lines.append("- 名稱證據（只證明名稱／分類，不證明書中內容）：")
        for source in sources:
            authority = source.get("authority") or source.get("source_id") or "未標示來源"
            url = source.get("url") or source.get("record_url") or ""
            lines.append(f"  - {authority}: {url}".rstrip())
    return lines


def render_record(path: Path, record: dict[str, Any], source_kind: str) -> tuple[str, dict[str, Any]]:
    source_id, page = source_page(record)
    expected_shard = shard_for(source_id, page)
    owner_shard = record.get("owner_shard")
    if owner_shard and owner_shard != expected_shard:
        raise ValueError(f"owner shard mismatch for {path.name}: {owner_shard} != {expected_shard}")
    raw_sha = sha256_bytes(path.read_bytes())
    taxon = record.get("book_taxon") or {}
    aliases = taxon.get("aliases") or []
    lines = [
        f"## {title(record)}",
        "",
        f"- record-id: {record.get('record_id') or record.get('entry_id') or path.stem}",
        f"- record-file-sha256: {raw_sha}",
        f"- record-status: {source_kind}",
        f"- 書中學名：{taxon.get('scientific_name') or '未標示'} {taxon.get('authorship') or ''}".rstrip(),
        f"- 書中異名：{'; '.join(aliases) if aliases else '未列'}",
        *naming_lines(record, source_kind),
        "",
        "> 規則：下列 Köhler 原文才是書中事實；臺灣名稱證據不可用來補寫療效、用途、分布或圖像特徵。",
        "",
    ]
    section_entries: list[dict[str, Any]] = []
    for index, section in enumerate(record.get("sections", [])):
        original = section.get("original_text") or ""
        exact_sha = section.get("exact_text_sha256") or sha256_bytes(original.encode())
        locator = section.get("source_locator") or {}
        section_page = int(locator.get("pdf_page") or page)
        section_source = str(locator.get("source_id") or source_id)
        section_type = section.get("section_type") or "unknown"
        lines.extend([
            f"### section {index + 1}: {section_type}",
            f"- citation: {section_source} PDF p.{section_page}",
            f"- exact-text-sha256: {exact_sha}",
            "- Köhler 原文（唯一內容事實來源）：",
            "",
            "```text",
            original.rstrip(),
            "```",
        ])
        lines.append("")
        section_entries.append({
            "section_type": section_type,
            "source_id": section_source,
            "pdf_page": section_page,
            "exact_text_sha256": exact_sha,
        })
    entry = {
        "record_id": record.get("record_id") or record.get("entry_id") or path.stem,
        "source_kind": source_kind,
        "source_path": str(path.relative_to(LAB)),
        "record_file_sha256": raw_sha,
        "shard": expected_shard,
        "source_id": source_id,
        "start_pdf_page": page,
        "section_count": len(section_entries),
        "display_name_source_scope": display_name_scope(record, source_kind),
        "sections": section_entries,
    }
    return "\n".join(lines).rstrip() + "\n", entry


def instructions_text() -> str:
    return """# Köhler 植物圖鑑聊天機器人：Gem 指令

你是 Köhler 四冊植物圖鑑的封閉語料聊天機器人。你只能根據八個 `knowledge-sNN.md` 檔案中的「Köhler 原文」回答植物內容。

## 必守規則

1. 書中沒有明確記載就回答「這本書目前沒有可支持此答案的記載」，不可用常識、網路資料或模型記憶補寫。
2. 中文顯示名與名稱證據只用來命名植物；不可把外部來源的療效、毒性、分布、形態或用途當成書中事實。名稱來源層級為 `non_taiwan_traditional_fallback` 時，必須明說是「非臺灣繁中備援名」，不可稱為臺灣公開名。
3. 臺灣名稱尚未解析時，保留書中學名；不可自行創造中文譯名。繁體中文優先，英文問題以英文回答；不要主動改用簡體中文。
4. 每個事實句末附來源，例如 `[kohler-volume-2 PDF p.192]`。如果同句使用多頁，就逐頁列出。
5. 回答中的繁中或英文翻譯必須忠實轉譯所引用的 Köhler 原文；不得把譯文延伸成原文沒有的療效、因果或安全結論。
6. 這是歷史植物文獻整理，不是醫療建議。可以回答「書中歷史上如何記載」，但不得提供個人劑量、診斷、替代處方或鼓勵自行使用有毒植物。
7. 使用者詢問非本書植物、現代藥物品牌、星座配對或無來源圖像推論時，明確拒答並說明範圍。
8. 圖版、顏色、版面或肉眼辨識內容若未在提供的文字證據中明列，就回答目前沒有通過驗證的圖像證據。

## 回答格式

- 先直接回答問題。
- 接著用一小段說明書中記載，逐句加頁碼引用。
- 最後加：`僅為 Köhler 歷史文獻摘要，不構成醫療建議。`
"""


def complete_status(root: Path) -> tuple[str, list[str]]:
    missing: list[str] = []
    for relative in (
        "checks/consolidated-v2-summary.json",
        "records-candidate/manifest.json",
        "naming/checks/validation-latest.json",
        "chunks-candidate/manifest.json",
    ):
        if not (root / relative).exists():
            missing.append(relative)
    if missing:
        return "incremental", missing
    consolidated = read_json(root / "checks/consolidated-v2-summary.json")
    records = read_json(root / "records-candidate/manifest.json")
    naming = read_json(root / "naming/checks/validation-latest.json")
    chunks = read_json(root / "chunks-candidate/manifest.json")
    expected = consolidated.get("total_candidates")
    if consolidated.get("status") != "PASS" or consolidated.get("unresolved_content_holds") != 0:
        missing.append("consolidated v2 staging not complete")
    if records.get("record_count") != expected or records.get("missing_naming_entry_ids"):
        missing.append("record projection not complete")
    if naming.get("valid") is not True or naming.get("artifacts") != expected:
        missing.append("naming projection not complete")
    if chunks.get("entry_count") != expected or chunks.get("chunk_count", 0) <= 0:
        missing.append("chunk projection not complete")
    return ("complete" if not missing else "incremental"), missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[str]] = {shard: [] for shard in SHARDS}
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for path, record, source_kind in candidate_records(args.root) + approved_records():
        rendered, entry = render_record(path, record, source_kind)
        identity = (entry["source_id"], entry["start_pdf_page"], entry["record_id"])
        if identity in seen:
            raise ValueError(f"duplicate exported record: {identity}")
        seen.add(identity)
        grouped[entry["shard"]].append(rendered)
        entries.append(entry)

    file_entries: list[dict[str, Any]] = []
    instructions = instructions_text().encode()
    (args.output / "gem-instructions.md").write_bytes(instructions)
    file_entries.append({
        "path": "gem-instructions.md", "role": "instructions",
        "sha256": sha256_bytes(instructions), "bytes": len(instructions),
    })
    for shard in SHARDS:
        header = (
            f"# Köhler 全書知識 shard {shard}\n\n"
            "此檔只含 Köhler 書中原文與獨立的臺灣名稱 metadata。回答內容時只以標示為「Köhler 原文」的文字為事實來源。\n\n"
        )
        payload = (header + "\n".join(grouped[shard])).encode()
        filename = f"knowledge-{shard.lower()}.md"
        (args.output / filename).write_bytes(payload)
        file_entries.append({
            "path": filename, "role": "knowledge", "shard": shard,
            "sha256": sha256_bytes(payload), "bytes": len(payload),
            "record_count": sum(1 for row in entries if row["shard"] == shard),
        })

    status, blockers = complete_status(args.root)
    unclassified = [
        row["record_id"] for row in entries
        if row["source_kind"] == "machine_extracted_candidate"
        and row["display_name_source_scope"] == "unclassified_staging"
    ]
    if unclassified:
        status = "incremental"
        blockers.append(f"unclassified display-name source scope: {len(unclassified)}")
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": now(),
        "status": status,
        "upload_instructions": "Upload gem-instructions.md plus all eight knowledge-sNN.md files (9 files total). Do not upload manifest.json.",
        "uploadable_file_count": 9,
        "record_count": len(entries),
        "candidate_record_count": sum(row["source_kind"] == "machine_extracted_candidate" for row in entries),
        "approved_baseline_record_count": sum(row["source_kind"] == "approved_baseline" for row in entries),
        "completion_blockers": blockers,
        "files": file_entries,
        "records": sorted(entries, key=lambda row: (row["shard"], row["source_id"], row["start_pdf_page"], row["record_id"])),
        "contains_vectors": False,
        "contains_api_keys": False,
        "unclassified_display_name_source_scope_count": len(unclassified),
    }
    manifest["manifest_sha256"] = canonical_sha(manifest)
    temporary = args.output / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.output / "manifest.json")
    print(json.dumps({
        "status": status, "record_count": len(entries),
        "output": str(args.output), "manifest_sha256": manifest["manifest_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
