#!/usr/bin/env python3
"""Resolve unresolved naming artifacts through scientific aliases printed in Köhler.

Only complete genus+species aliases are queried. A staging artifact is promoted only
when TaiCOL returns the exact alias at species rank, an accepted taxon, and a Taiwan
public Chinese name. Book facts remain separate from external naming metadata.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")
API = "https://api.taicol.tw/v2"


def compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def scientific_aliases(values: list[str]) -> list[str]:
    aliases = []
    for value in values:
        words = re.findall(r"[A-Za-z][A-Za-z.-]*", value)
        if len(words) < 2 or len(words[0].rstrip(".")) < 3 or len(words[1].rstrip(".")) < 3:
            continue
        alias = f"{words[0].rstrip('.')} {words[1].rstrip('.').lower()}"
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def get(endpoint: str, **params: str) -> tuple[str, dict]:
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "Kohler-Taiwan-Naming-Lane/1.0"})
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return url, json.load(response)
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"TaiCOL alias request failed after three attempts: {url}: {last_error}") from last_error


def flat(payload: dict) -> list[dict]:
    return [item for item in payload.get("data", []) if isinstance(item, dict)]


def process(path: Path) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("name_resolution", {}).get("terminal_status") != "unresolved":
        return False
    attempted = {
        compact(item.get("query_name"))
        for item in data.get("evidence", [])
        if item.get("source_id") == "taicol_book_alias"
    }
    aliases = [
        alias for alias in scientific_aliases(data.get("book_name", {}).get("aliases_candidates", []))
        if compact(alias) not in attempted
    ]
    if not aliases:
        return False

    checked_at = datetime.now(TZ).isoformat(timespec="seconds")
    promoted = None
    for alias in aliases:
        name_url, name_payload = get("nameMatch", name=alias)
        evidence = {
            "source_id": "taicol_book_alias",
            "authority": "臺灣物種名錄 TaiCOL",
            "query_name": alias,
            "query_basis": "scientific alias explicitly printed in the Köhler entry",
            "name_match_url": name_url,
            "retrieved_at": checked_at,
            "result_total": name_payload.get("info", {}).get("total", 0),
            "name_match_results": [],
            "assertion_scope": "external_naming_and_occurrence_metadata_only",
        }
        for match in flat(name_payload):
            taxon_id = match.get("taxon_id")
            detail_url, detail_payload = get("taxon", taxon_id=taxon_id) if taxon_id else (None, {})
            details = flat(detail_payload)
            detail = details[0] if details else {}
            summary = {
                "matched_name": match.get("matched_name"),
                "taxon_id": taxon_id,
                "taicol_name_status": match.get("taicol_name_status"),
                "accepted_name": match.get("accepted_name"),
                "taxon_url": f"https://taicol.tw/zh-hant/taxon/{taxon_id}" if taxon_id else None,
                "taxon_api_url": detail_url,
                "taxon": {key: detail.get(key) for key in (
                    "taxon_status", "simple_name", "name_author", "rank", "common_name_c",
                    "alternative_name_c", "synonyms", "is_in_taiwan", "alien_type",
                    "alien_status_note", "not_official", "updated_at",
                )},
            }
            evidence["name_match_results"].append(summary)
            if (
                compact(match.get("matched_name")) == compact(alias)
                and detail.get("rank") == "Species"
                and detail.get("taxon_status") == "accepted"
                and detail.get("common_name_c")
            ):
                promoted = (alias, taxon_id, detail)
        data.setdefault("evidence", []).append(evidence)
        if promoted:
            break

    if promoted:
        alias, taxon_id, detail = promoted
        alternatives = [item.strip() for item in (detail.get("alternative_name_c") or "").split(",") if item.strip()]
        resolution = data["name_resolution"]
        resolution.update({
            "terminal_status": "alias",
            "query_names": list(dict.fromkeys(resolution.get("query_names", []) + [alias])),
            "accepted_scientific_name": detail.get("simple_name"),
            "display_name_zh_tw": detail.get("common_name_c"),
            "alternative_names_zh_tw": alternatives,
            "taiwan_occurrence_status": "recorded" if detail.get("is_in_taiwan") is True else "unclear",
            "taxon_id": taxon_id,
            "display_name_source_scope": "taiwan_taxonomic_public",
            "display_name_source_evidence_ids": ["taicol_book_alias"],
            "display_name_is_taiwan_public": True,
            "display_name_answer_policy": "use_as_taiwan_primary",
            "rationale": "A scientific alias explicitly printed in the Köhler entry exactly matches a TaiCOL species with a Taiwan public name.",
            "checked_at": checked_at,
        })
        data["warnings"] = [
            "The Taiwan accepted name is linked through a scientific alias printed in Köhler; Köhler facts remain attached to the book taxon."
        ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    processed = 0
    promoted = 0
    results = []
    for path in sorted((ROOT / "naming/staging").glob("*.naming.json")):
        before = json.loads(path.read_text(encoding="utf-8"))["name_resolution"]["terminal_status"]
        if process(path):
            processed += 1
            after_data = json.loads(path.read_text(encoding="utf-8"))
            after = after_data["name_resolution"]["terminal_status"]
            promoted += after != before
            results.append({"entry_id": after_data["entry_id"], "status": after})
            if processed >= args.limit:
                break
    print(json.dumps({"processed": processed, "promoted": promoted, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
