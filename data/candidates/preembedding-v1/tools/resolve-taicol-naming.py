#!/usr/bin/env python3
"""Create naming-only TaiCOL staging records for integration-ready candidates.

This helper never writes canonical records, chunks, registries, indexes, or source PDFs.
It promotes a match only when the book binomial is an exact TaiCOL name or is explicitly
listed in the matched TaiCOL taxon's synonyms. Genus-only fuzzy matches stay unresolved,
and cross-genus synonym links require independent taxonomy review before promotion.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
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
TAICOL_API = "https://api.taicol.tw/v2"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def genus(value: str | None) -> str | None:
    words = re.findall(r"[A-Za-z]+", value or "")
    return words[0].casefold() if words else None


def book_binomial(name: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z.-]*", name)
    if len(words) >= 2:
        return f"{words[0][:1].upper()}{words[0][1:]} {words[1].lower()}"
    return name.strip()


def api_get(endpoint: str, **params: str) -> tuple[str, dict]:
    url = f"{TAICOL_API}/{endpoint}?{urllib.parse.urlencode(params)}"
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
    raise RuntimeError(f"TaiCOL request failed after three attempts: {url}: {last_error}") from last_error


def flat_results(payload: dict) -> list[dict]:
    data = payload.get("data", [])
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def source_summary(item: dict) -> dict:
    return {
        "matched_name": item.get("matched_name"),
        "matched_name_id": item.get("matched_name_id"),
        "taxon_id": item.get("taxon_id"),
        "taicol_name_status": item.get("taicol_name_status"),
        "accepted_name": item.get("accepted_name"),
        "accepted_name_id": item.get("accepted_name_id"),
        "accepted_usage": item.get("matched_name_accepted_usage", []),
    }


def detail_summary(item: dict) -> dict:
    keys = (
        "taxon_id", "taxon_status", "name_id", "simple_name", "name_author", "rank",
        "common_name_c", "alternative_name_c", "synonyms", "is_in_taiwan",
        "alien_type", "alien_status_note", "not_official", "updated_at",
    )
    return {key: item.get(key) for key in keys}


def candidate_stem(entry_id: str) -> str:
    return entry_id.replace(":", "__").replace("/", "_")


def draft_for(candidate: dict) -> Path | None:
    owner = candidate.get("owner_shard")
    source_entry_id = candidate.get("source_parent_entry_id") or candidate["entry_id"]
    if owner:
        matches = list(ROOT.glob(f"shards/{owner}/maker/structure-drafts/{candidate_stem(source_entry_id)}.json"))
    else:
        matches = list(ROOT.glob(f"shards/*/maker/structure-drafts/{candidate_stem(source_entry_id)}.json"))
    return matches[0] if matches else None


def resolve(candidate: dict) -> dict:
    draft_path = draft_for(candidate)
    source = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path else {}
    taxon = source.get("draft", {}).get("book_taxon", {})
    book_name = candidate["book_taxon_candidate"]
    binomial = book_binomial(book_name)
    queries = []
    for value in (book_name, binomial):
        # Preserve a canonical lowercase epithet query because TaiCOL matching can
        # treat historical title-case epithets as a genus-only fuzzy request.
        if value and value not in queries:
            queries.append(value)

    retrieved_at = datetime.now(TZ).isoformat(timespec="seconds")
    evidence = []
    promoted = None
    blocked_cross_genus = []
    for query in queries:
        name_url, name_payload = api_get("nameMatch", name=query)
        query_evidence = {
            "source_id": "taicol",
            "authority": "臺灣物種名錄 TaiCOL",
            "query_name": query,
            "name_match_url": name_url,
            "retrieved_at": retrieved_at,
            "result_total": name_payload.get("info", {}).get("total", 0),
            "name_match_results": [],
            "assertion_scope": "external_naming_and_occurrence_metadata_only",
        }
        for match in flat_results(name_payload):
            summary = source_summary(match)
            taxon_id = match.get("taxon_id")
            detail = {}
            detail_url = None
            if taxon_id:
                detail_url, detail_payload = api_get("taxon", taxon_id=taxon_id)
                details = flat_results(detail_payload)
                detail = detail_summary(details[0]) if details else {}
            summary["taxon_url"] = f"https://taicol.tw/zh-hant/taxon/{taxon_id}" if taxon_id else None
            summary["taxon_api_url"] = detail_url
            summary["taxon"] = detail
            query_evidence["name_match_results"].append(summary)

            matched = compact(match.get("matched_name"))
            synonyms = {compact(item) for item in (detail.get("synonyms") or "").split(",") if item.strip()}
            exact_name = matched == compact(binomial)
            explicit_synonym = compact(binomial) in synonyms
            if detail.get("rank") == "Species" and (exact_name or explicit_synonym):
                accepted_name = detail.get("simple_name") or match.get("accepted_name")
                is_alias = not (exact_name and match.get("taicol_name_status") == "accepted")
                if is_alias and genus(binomial) != genus(accepted_name):
                    block = {
                        "book_binomial": binomial,
                        "accepted_name": accepted_name,
                        "reason": "cross-genus synonym links require independent taxonomy review",
                    }
                    summary["auto_promotion_blocked"] = block
                    blocked_cross_genus.append(block)
                    continue
                promotion = {
                    "kind": "alias" if is_alias else "accepted",
                    "match": match,
                    "detail": detail,
                    "query_name": query,
                    "exact_name": exact_name,
                    "explicit_synonym": explicit_synonym,
                }
                if promoted is None or (promoted["kind"] == "alias" and promotion["kind"] == "accepted"):
                    promoted = promotion
        evidence.append(query_evidence)

    if promoted:
        detail = promoted["detail"]
        alternatives = [item.strip() for item in (detail.get("alternative_name_c") or "").split(",") if item.strip()]
        terminal_status = promoted["kind"]
        display_name = detail.get("common_name_c") or None
        accepted_name = detail.get("simple_name") or promoted["match"].get("accepted_name")
        occurrence = "recorded" if detail.get("is_in_taiwan") is True else "not_recorded" if detail.get("is_in_taiwan") is False else "unclear"
        rationale = (
            "TaiCOL accepts the queried book binomial at species rank."
            if terminal_status == "accepted"
            else "TaiCOL explicitly links the book binomial as a species-level synonym to the accepted taxon."
        )
    else:
        terminal_status = "unresolved"
        display_name = None
        alternatives = []
        accepted_name = None
        occurrence = "not_checked"
        rationale = (
            "TaiCOL returned a cross-genus synonym link that requires independent taxonomy review; it was not auto-promoted."
            if blocked_cross_genus
            else "TaiCOL returned no exact species-level name or explicit synonym; genus-only/fuzzy results were not promoted."
        )

    return {
        "schema_version": "1.0",
        "lane": "taiwan_naming",
        "entry_id": candidate["entry_id"],
        "owner_shard": candidate.get("owner_shard"),
        "source_draft": str(draft_path.resolve()) if draft_path else None,
        "source_draft_sha256": sha256(draft_path) if draft_path else None,
        "eligibility": {
            "authority": "integration/embedding-ready-candidate-manifest.json",
            "integration_status": "embedding_ready_candidate",
            "candidate_sha256": candidate["candidate_sha256"],
            "validation_check_sha256": candidate.get("validation_check_sha256"),
            "source_disposition_sha256": candidate.get("source_disposition_sha256"),
            "review_status": candidate.get("review_status"),
            "maker_deterministic_status": source.get("deterministic_status"),
            "maker_hold_resolved": source.get("hold_resolved") is True,
        },
        "book_name": {
            "scientific_name_candidate": book_name,
            "authorship_candidate": taxon.get("authorship_candidate"),
            "aliases_candidates": taxon.get("aliases_candidates", []),
        },
        "name_resolution": {
            "terminal_status": terminal_status,
            "query_names": queries,
            "accepted_scientific_name": accepted_name,
            "display_name_zh_tw": display_name,
            "alternative_names_zh_tw": alternatives,
            "taiwan_occurrence_status": occurrence,
            "taxon_id": promoted["detail"].get("taxon_id") if promoted else None,
            "display_name_source_scope": "taiwan_taxonomic_public" if promoted else "unresolved",
            "display_name_source_evidence_ids": ["taicol"] if promoted else [],
            "display_name_is_taiwan_public": bool(promoted),
            "display_name_answer_policy": "use_as_taiwan_primary" if promoted else "scientific_name_only",
            "rationale": rationale,
            "checked_at": retrieved_at,
        },
        "evidence": evidence,
        "separation": {
            "book_facts_included": False,
            "external_metadata_may_supply_plant_facts": False,
            "scope": "scientific-name mapping, Taiwan public name, aliases, and occurrence metadata only",
        },
        "warnings": (
            [
                "TaiCOL supplied a cross-genus synonym link; automatic promotion was blocked pending independent taxonomy evidence.",
                "No Taiwan public display name was assigned; unresolved is terminal until new evidence appears.",
            ]
            if terminal_status == "unresolved" and blocked_cross_genus
            else ["No Taiwan public display name was assigned; unresolved is terminal until new evidence appears."]
            if terminal_status == "unresolved"
            else []
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--pending-only", action="store_true", help="Select only pass drafts without a staging artifact")
    args = parser.parse_args()
    staging = ROOT / "naming/staging"
    staging.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "integration/embedding-ready-candidate-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates = sorted(manifest.get("candidates", []), key=lambda item: item["entry_id"])
    if args.pending_only:
        candidates = [item for item in candidates if not (staging / f"{candidate_stem(item['entry_id'])}.naming.json").exists()]
    selected = candidates[args.offset:args.offset + args.limit]
    results = []
    for candidate in selected:
        output = staging / f"{candidate_stem(candidate['entry_id'])}.naming.json"
        if output.exists() and not args.overwrite:
            result = json.loads(output.read_text(encoding="utf-8"))
            action = "reused"
        else:
            result = resolve(candidate)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            action = "written"
        results.append({"entry_id": result["entry_id"], "status": result["name_resolution"]["terminal_status"], "output": str(output), "action": action})
    print(json.dumps({"selected": len(selected), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
