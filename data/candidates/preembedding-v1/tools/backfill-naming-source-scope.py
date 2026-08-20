#!/usr/bin/env python3
"""Deterministically annotate Taiwan-name provenance for naming staging.

This helper never reads or writes canonical records/chunks/indexes.  It only
adds projection-safe provenance fields to naming staging artifacts.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SCOPES = {
    "taiwan_taxonomic_public",
    "taiwan_government_public",
    "taiwan_academic_public",
    "taiwan_public_fallback",
    "non_taiwan_traditional_fallback",
    "unresolved",
}
EVIDENCE_SCOPES = SCOPES | {
    "non_taiwan_scientific_authority",
    "kohler_source_visual",
    "other_external_support",
}

# Manual resolutions are deliberately keyed to the evidence record that
# actually supplies the display name, not merely the source that resolves the
# scientific synonym.  This prevents a Kew synonym record from being mistaken
# for evidence of a Taiwan Chinese name.
MANUAL_SOURCE = {
    "ntu-seed-hyoscyamus-niger": ("taiwan_academic_public", ["ntu_seed"]),
    "aphia-quarantine-prunus-dulcis": ("taiwan_government_public", ["aphia"]),
    "kew-powo-60466103-2": ("taiwan_government_public", ["tfri"]),
    "tfda-cosmetic-standard-juniperus-communis": ("taiwan_government_public", ["taiwan_fda"]),
    "forest-gov-tw-18928-abies-alba": ("taiwan_government_public", ["forest_bureau"]),
    "executive-yuan-gazette-triticum-agropyron-elymus-repens": ("taiwan_government_public", ["executive_yuan_gazette"]),
    "nmns-menyanthes-trifoliata-sleeping-bogbean": ("taiwan_academic_public", ["nmns"]),
    "tfda-tcm-material-inula-helenium": ("taiwan_government_public", ["taiwan_fda"]),
    "aphia-quarterly-2023-04-pimpinella-anisum": ("taiwan_government_public", ["aphia"]),
    "tfb-research-report-98-00-5-08-melaleuca-leucadendron": ("taiwan_government_public", ["forestry_bureau"]),
    "aphia-host-list-prunus-laurocerasus": ("taiwan_government_public", ["aphia"]),
    "executive-yuan-gazette-015-039-anthemis-arvensis": ("taiwan_government_public", ["executive_yuan_gazette"]),
    "acri-imported-unrecorded-weed-anthemis-cotula": ("taiwan_government_public", ["taiwan_acri"]),
    "zhwiki-piscidia-piscipula": ("non_taiwan_traditional_fallback", ["zh_wikipedia_fallback"]),
    "bioeconomy-agbio-60-table-1-gaultheria-procumbens": ("taiwan_academic_public", ["taiwan_academic_publication"]),
    "zhwiki-curare-strychnos-toxifera": ("non_taiwan_traditional_fallback", ["zh_wikipedia_fallback"]),
    "tfri-forestry-research-quarterly-28-1-strychnos-ignatii": ("taiwan_government_public", ["tfri"]),
    "nchu-horticulture-37-1-gelsemium-sempervirens": ("taiwan_academic_public", ["nchu_horticulture"]),
    "moa-import-quarantine-betula-pendula": ("taiwan_government_public", ["taiwan_moa"]),
    "taiwan-public-horticulture-helleborus-niger": ("taiwan_public_fallback", ["taiwan_public_horticulture"]),
    "taiwan-public-horticulture-laburnum-anagyroides": ("taiwan_public_fallback", ["taiwan_public_horticulture"]),
}


def policy(scope: str) -> tuple[bool, str]:
    if scope in {
        "taiwan_taxonomic_public",
        "taiwan_government_public",
        "taiwan_academic_public",
        "taiwan_public_fallback",
    }:
        return True, "use_as_taiwan_primary"
    if scope == "non_taiwan_traditional_fallback":
        return False, "use_only_with_non_taiwan_fallback_label"
    return False, "scientific_name_only"


def evidence_scope(item: dict, display_scope: str, display_evidence_ids: list[str]) -> str:
    source_id = item.get("source_id") or ""
    authority = item.get("authority") or ""
    if source_id in display_evidence_ids:
        return display_scope
    if source_id in {"taicol", "taicol_book_alias", "tai2"}:
        return "taiwan_taxonomic_public"
    if source_id == "kew_powo":
        return "non_taiwan_scientific_authority"
    if source_id == "zh_wikipedia_fallback":
        return "non_taiwan_traditional_fallback"
    if source_id == "kohler_page_visual":
        return "kohler_source_visual"
    if source_id.startswith("taiwan_public") or "臺灣公開" in authority:
        return "taiwan_public_fallback"
    if any(token in authority for token in ("農業部", "衛生福利部", "經濟部", "考選部", "行政院", "文化部", "海關", "國家中醫藥研究所")):
        return "taiwan_government_public"
    if any(token in authority for token in ("國立臺灣大學", "國立中興大學", "國立自然科學博物館", "台灣經濟研究院", "科博")):
        return "taiwan_academic_public"
    return "other_external_support"


def classify(data: dict) -> tuple[str, list[str]]:
    resolution = data["name_resolution"]
    if resolution.get("terminal_status") == "unresolved":
        return "unresolved", []
    authority_record_id = resolution.get("authority_record_id")
    if authority_record_id in MANUAL_SOURCE:
        return MANUAL_SOURCE[authority_record_id]
    if resolution.get("taxon_id"):
        source_ids = {item.get("source_id") for item in data.get("evidence", [])}
        evidence_id = "taicol_book_alias" if "taicol_book_alias" in source_ids and resolution.get("terminal_status") == "alias" else "taicol"
        return "taiwan_taxonomic_public", [evidence_id]
    if resolution.get("tai2_code"):
        return "taiwan_taxonomic_public", ["tai2"]
    raise ValueError(f"{data.get('entry_id')}: resolved display name has no deterministic source-scope rule")


def main() -> None:
    changed = 0
    counts = {scope: 0 for scope in sorted(SCOPES)}
    for path in sorted((ROOT / "naming/staging").glob("*.naming.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        resolution = data["name_resolution"]
        scope, evidence_ids = classify(data)
        is_taiwan, answer_policy = policy(scope)
        before = json.dumps(
            {"resolution": resolution, "evidence": data.get("evidence", [])},
            ensure_ascii=False,
            sort_keys=True,
        )
        resolution.update({
            "display_name_source_scope": scope,
            "display_name_source_evidence_ids": evidence_ids,
            "display_name_is_taiwan_public": is_taiwan,
            "display_name_answer_policy": answer_policy,
        })
        for item in data.get("evidence", []):
            item["evidence_source_scope"] = evidence_scope(item, scope, evidence_ids)
        after = json.dumps(
            {"resolution": resolution, "evidence": data.get("evidence", [])},
            ensure_ascii=False,
            sort_keys=True,
        )
        counts[scope] += 1
        if before != after:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed += 1
    print(json.dumps({"artifacts": sum(counts.values()), "changed": changed, "source_scope_counts": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
