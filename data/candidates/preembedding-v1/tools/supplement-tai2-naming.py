#!/usr/bin/env python3
"""Supplement unresolved TaiCOL naming records with exact Tai2 plant-name evidence.

Tai2 type M is its main/accepted name and type S is a synonym. Only an exact
book-binomial result is eligible. A synonym is promoted only after its species
page identifies the main scientific name and Traditional Chinese display name.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import html
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


def compact(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip().casefold()


def binomial(value: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z.-]*", value)
    return f"{words[0]} {words[1].lower()}" if len(words) >= 2 else value.strip()


def livewire_decode(value):
    if isinstance(value, list):
        if len(value) == 2 and isinstance(value[1], dict) and value[1].get("s") == "arr":
            return livewire_decode(value[0])
        return [livewire_decode(item) for item in value]
    if isinstance(value, dict):
        return {key: livewire_decode(item) for key, item in value.items()}
    return value


def flatten_dicts(value):
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from flatten_dicts(item)


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Kohler-Taiwan-Naming-Lane/1.0"})
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Tai2 request failed after three attempts: {url}: {last_error}") from last_error


def search(query: str) -> tuple[str, list[dict]]:
    url = "https://tai2.ntu.edu.tw/search/1/" + urllib.parse.quote(query, safe="")
    text = fetch_text(url)
    match = re.search(
        r'wire:snapshot="([^"]+)"[^>]+wire:name="search-component\.plant-search"',
        text,
    )
    if not match:
        return url, []
    snapshot = json.loads(html.unescape(match.group(1)))
    decoded = livewire_decode(snapshot.get("data", {}).get("searchResults", []))
    return url, list(flatten_dicts(decoded))


def detail(code: str) -> tuple[str, dict]:
    url = "https://tai2.ntu.edu.tw/species/" + urllib.parse.quote(code, safe="")
    text = fetch_text(url)
    match = re.search(r"var spname = (\{.*?\});", text)
    return url, json.loads(match.group(1)) if match else {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--overwrite-evidence", action="store_true")
    args = parser.parse_args()
    paths = []
    for path in sorted((ROOT / "naming/staging").glob("*.naming.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("name_resolution", {}).get("terminal_status") != "unresolved":
            continue
        has_tai2 = any(item.get("source_id") == "tai2" for item in data.get("evidence", []))
        if not has_tai2 or args.overwrite_evidence:
            paths.append(path)
    results = []
    for path in paths[:args.limit]:
        data = json.loads(path.read_text(encoding="utf-8"))
        query = binomial(data["book_name"]["scientific_name_candidate"])
        search_url, found = search(query)
        exact = [item for item in found if compact(item.get("ebooksearch") or item.get("simnametitle") or item.get("name")) == compact(query)]
        evidence = {
            "source_id": "tai2",
            "authority": "台灣植物資訊整合查詢系統 Plants of Taiwan（國立臺灣大學）",
            "query_name": query,
            "search_url": search_url,
            "retrieved_at": datetime.now(TZ).isoformat(timespec="seconds"),
            "exact_result_count": len(exact),
            "exact_results": exact,
            "assertion_scope": "external_naming_and_occurrence_metadata_only",
        }
        promoted = None
        for item in exact:
            if item.get("result_type") != "species" or item.get("type") not in {"M", "S"} or not item.get("code"):
                continue
            detail_url, accepted = detail(item["code"])
            evidence["species_url"] = detail_url
            evidence["accepted_species_record"] = accepted
            accepted_name = accepted.get("ebooksearch")
            display_name = accepted.get("chname")
            if accepted_name and display_name:
                promoted = {
                    "kind": "accepted" if item.get("type") == "M" else "alias",
                    "accepted_name": accepted_name,
                    "display_name": display_name,
                    "code": item["code"],
                    "alternative_names": [name for name in (accepted.get("comname") or "").split("、") if name],
                    "occurrence": "recorded" if accepted.get("endetype") else "unclear",
                }
                break
        data["evidence"] = [item for item in data.get("evidence", []) if item.get("source_id") != "tai2"]
        data["evidence"].append(evidence)
        resolution = data["name_resolution"]
        if promoted:
            resolution.update({
                "terminal_status": promoted["kind"],
                "accepted_scientific_name": promoted["accepted_name"],
                "display_name_zh_tw": promoted["display_name"],
                "alternative_names_zh_tw": promoted["alternative_names"],
                "taiwan_occurrence_status": promoted["occurrence"],
                "taxon_id": None,
                "tai2_code": promoted["code"],
                "display_name_source_scope": "taiwan_taxonomic_public",
                "display_name_source_evidence_ids": ["tai2"],
                "display_name_is_taiwan_public": True,
                "display_name_answer_policy": "use_as_taiwan_primary",
                "rationale": "Tai2 exactly lists the book binomial as a main name or synonym and links it to this accepted species page.",
                "checked_at": evidence["retrieved_at"],
            })
            data["warnings"] = [warning for warning in data.get("warnings", []) if not warning.startswith("No Taiwan public display name")]
            if promoted["kind"] == "alias":
                warning = (
                    "Tai2 treats the exact historical book binomial as a synonym of a different accepted name; "
                    "Köhler facts remain attached to the book taxon and must not be reinterpreted as facts about the Taiwan accepted taxon."
                )
                if warning not in data["warnings"]:
                    data["warnings"].append(warning)
        else:
            resolution["rationale"] += " Tai2 exact plant-name search also did not provide a promotable main-name/synonym mapping."
            resolution["checked_at"] = evidence["retrieved_at"]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results.append({"entry_id": data["entry_id"], "status": resolution["terminal_status"], "tai2_exact_results": len(exact)})
    print(json.dumps({"processed": len(results), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
