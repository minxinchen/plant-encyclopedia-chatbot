#!/usr/bin/env python3
"""Validate naming-only staging artifacts and their deterministic-pass linkage.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"accepted", "alias", "unresolved"}
DISPLAY_NAME_SOURCE_SCOPES = {
    "taiwan_taxonomic_public",
    "taiwan_government_public",
    "taiwan_academic_public",
    "taiwan_public_fallback",
    "non_taiwan_traditional_fallback",
    "unresolved",
}
TAIWAN_PUBLIC_SCOPES = {
    "taiwan_taxonomic_public",
    "taiwan_government_public",
    "taiwan_academic_public",
    "taiwan_public_fallback",
}
EVIDENCE_SOURCE_SCOPES = DISPLAY_NAME_SOURCE_SCOPES | {
    "non_taiwan_scientific_authority",
    "kohler_source_visual",
    "other_external_support",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portable_source_path(source_value: str) -> Path:
    """Resolve historical absolute provenance paths inside a public checkout."""
    source = Path(source_value)
    if source.is_file():
        return source
    marker = "/data/candidates/preembedding-v1/"
    normalized = source_value.replace("\\", "/")
    if marker in normalized:
        relative = normalized.split(marker, 1)[1]
        candidate = (ROOT / relative).resolve()
        if candidate.is_relative_to(ROOT.resolve()):
            return candidate
    return source


def main() -> None:
    failures = []
    counts = {key: 0 for key in sorted(ALLOWED)}
    scope_counts = {key: 0 for key in sorted(DISPLAY_NAME_SOURCE_SCOPES)}
    projection_entries = []
    paths = sorted((ROOT / "naming/staging").glob("*.naming.json"))
    manifest_path = ROOT / "integration/embedding-ready-candidate-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    eligible = {item["entry_id"]: item for item in manifest.get("candidates", [])}
    staged_entries = set()
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        staged_entries.add(data.get("entry_id"))
        status = data.get("name_resolution", {}).get("terminal_status")
        if status not in ALLOWED:
            failures.append(f"{path.name}: invalid terminal status")
            continue
        counts[status] += 1
        entry_id = data.get("entry_id")
        candidate = eligible.get(entry_id)
        if candidate is None:
            failures.append(f"{path.name}: absent from integration embedding-ready manifest")
        else:
            link = data.get("eligibility", {})
            if link.get("authority") != "integration/embedding-ready-candidate-manifest.json":
                failures.append(f"{path.name}: wrong eligibility authority")
            if link.get("candidate_sha256") != candidate.get("candidate_sha256"):
                failures.append(f"{path.name}: candidate_sha256 mismatch")
            if link.get("validation_check_sha256") != candidate.get("validation_check_sha256"):
                failures.append(f"{path.name}: validation_check_sha256 mismatch")
            if link.get("source_disposition_sha256") != candidate.get("source_disposition_sha256"):
                failures.append(f"{path.name}: source_disposition_sha256 mismatch")
            if link.get("review_status") != candidate.get("review_status"):
                failures.append(f"{path.name}: review_status mismatch")
        source_value = data.get("source_draft")
        if source_value:
            source = portable_source_path(source_value)
            if not source.is_file() or digest(source) != data.get("source_draft_sha256"):
                failures.append(f"{path.name}: source draft/hash mismatch")
        if data.get("separation", {}).get("book_facts_included") is not False:
            failures.append(f"{path.name}: naming artifact includes book facts")
        if not data.get("evidence") or any(
            not item.get("query_name")
            or not (
                item.get("name_match_url")
                or item.get("search_url")
                or item.get("source_url")
                or item.get("record_url")
            )
            or not item.get("retrieved_at")
            for item in data.get("evidence", [])
        ):
            failures.append(f"{path.name}: incomplete evidence")
        resolution = data.get("name_resolution", {})
        scope = resolution.get("display_name_source_scope")
        source_evidence_ids = resolution.get("display_name_source_evidence_ids")
        is_taiwan_public = resolution.get("display_name_is_taiwan_public")
        answer_policy = resolution.get("display_name_answer_policy")
        if scope not in DISPLAY_NAME_SOURCE_SCOPES:
            failures.append(f"{path.name}: invalid or missing display_name_source_scope")
        else:
            scope_counts[scope] += 1
        if not isinstance(source_evidence_ids, list) or any(not isinstance(value, str) or not value for value in source_evidence_ids):
            failures.append(f"{path.name}: display_name_source_evidence_ids must be a string list")
            source_evidence_ids = []
        known_evidence_ids = {item.get("source_id") for item in data.get("evidence", [])}
        if not set(source_evidence_ids).issubset(known_evidence_ids):
            failures.append(f"{path.name}: display-name evidence id is absent from evidence")
        for item in data.get("evidence", []):
            evidence_scope = item.get("evidence_source_scope")
            if evidence_scope not in EVIDENCE_SOURCE_SCOPES:
                failures.append(f"{path.name}: evidence has invalid or missing evidence_source_scope")
            if item.get("source_id") == "kew_powo" and evidence_scope != "non_taiwan_scientific_authority":
                failures.append(f"{path.name}: Kew evidence is not isolated as a non-Taiwan scientific authority")
            if item.get("source_id") == "zh_wikipedia_fallback" and evidence_scope != "non_taiwan_traditional_fallback":
                failures.append(f"{path.name}: Wikipedia fallback is not isolated as non-Taiwan")
            if item.get("source_id") in source_evidence_ids and evidence_scope != scope:
                failures.append(f"{path.name}: selected display-name evidence scope conflicts with resolution")
        if scope in TAIWAN_PUBLIC_SCOPES and any(
            item.get("source_id") in {"kew_powo", "zh_wikipedia_fallback"}
            for item in data.get("evidence", [])
            if item.get("source_id") in source_evidence_ids
        ):
            failures.append(f"{path.name}: non-Taiwan evidence cannot supply a Taiwan display name")
        expected_is_taiwan = scope in TAIWAN_PUBLIC_SCOPES
        expected_policy = (
            "use_as_taiwan_primary"
            if expected_is_taiwan
            else "use_only_with_non_taiwan_fallback_label"
            if scope == "non_taiwan_traditional_fallback"
            else "scientific_name_only"
        )
        if is_taiwan_public is not expected_is_taiwan:
            failures.append(f"{path.name}: display_name_is_taiwan_public conflicts with source scope")
        if answer_policy != expected_policy:
            failures.append(f"{path.name}: display_name_answer_policy conflicts with source scope")
        if status == "unresolved" and (scope != "unresolved" or source_evidence_ids):
            failures.append(f"{path.name}: unresolved artifact has a resolved display-name scope")
        if status != "unresolved" and (scope == "unresolved" or not source_evidence_ids):
            failures.append(f"{path.name}: resolved artifact lacks display-name source evidence")
        if scope == "non_taiwan_traditional_fallback" and not any(
            item.get("source_id") in source_evidence_ids
            and "非臺灣" in (item.get("authority") or "")
            for item in data.get("evidence", [])
        ):
            failures.append(f"{path.name}: non-Taiwan fallback is not explicitly labelled in evidence")
        if status == "unresolved" and any(resolution.get(key) for key in ("accepted_scientific_name", "display_name_zh_tw", "taxon_id", "tai2_code")):
            failures.append(f"{path.name}: unresolved artifact assigns a name or taxon")
        if status == "unresolved" and not any(item.get("source_id") != "taicol" for item in data.get("evidence", [])):
            failures.append(f"{path.name}: unresolved artifact lacks a secondary Taiwan-source attempt")
        if status != "unresolved" and (
            not resolution.get("accepted_scientific_name")
            or not resolution.get("display_name_zh_tw")
            or not (resolution.get("taxon_id") or resolution.get("tai2_code") or resolution.get("authority_record_id"))
        ):
            failures.append(f"{path.name}: resolved artifact lacks accepted/display/taxon")
        projection_entries.append({
            "entry_id": entry_id,
            "candidate_sha256": data.get("eligibility", {}).get("candidate_sha256"),
            "validation_check_sha256": data.get("eligibility", {}).get("validation_check_sha256"),
            "naming_terminal_status": status,
            "accepted_scientific_name": resolution.get("accepted_scientific_name"),
            "display_name_zh_tw": resolution.get("display_name_zh_tw"),
            "display_name_source_scope": scope,
            "display_name_source_evidence_ids": source_evidence_ids,
            "display_name_is_taiwan_public": is_taiwan_public,
            "display_name_answer_policy": answer_policy,
            "checked_at": resolution.get("checked_at"),
        })
    missing_entries = sorted(set(eligible) - staged_entries)
    ineligible_entries = sorted(staged_entries - set(eligible))
    if missing_entries:
        failures.append(f"missing naming artifacts: {len(missing_entries)}")
    if ineligible_entries:
        failures.append(f"staging artifacts without current deterministic-pass source: {len(ineligible_entries)}")
    result = {
        "valid": not failures,
        "eligibility_authority": "integration/embedding-ready-candidate-manifest.json",
        "eligible_embedding_ready_candidates": len(eligible),
        "artifacts": len(paths),
        "coverage": round(len(staged_entries & set(eligible)) / len(eligible), 6) if eligible else 1.0,
        "missing_entries": missing_entries,
        "ineligible_entries": ineligible_entries,
        "terminal_status_counts": counts,
        "display_name_source_scope_counts": scope_counts,
        "record_chunk_projection": "naming/checks/record-chunk-projection.json",
        "failures": failures,
    }
    checks = ROOT / "naming/checks"
    checks.mkdir(parents=True, exist_ok=True)
    output = checks / "validation-latest.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    projection = {
        "schema_version": "1.0",
        "projection": "record_and_chunk_naming_metadata",
        "eligibility_authority": "integration/embedding-ready-candidate-manifest.json",
        "validation_valid": not failures,
        "contract": {
            "display_name_source_scope_enum": sorted(DISPLAY_NAME_SOURCE_SCOPES),
            "evidence_source_scope_enum": sorted(EVIDENCE_SOURCE_SCOPES),
            "taiwan_primary_scopes": sorted(TAIWAN_PUBLIC_SCOPES),
            "non_taiwan_fallback_requires_label": True,
            "unresolved_answer_uses_scientific_name_only": True,
            "book_facts_included": False,
        },
        "entries": sorted(projection_entries, key=lambda item: item["entry_id"] or ""),
    }
    (checks / "record-chunk-projection.json").write_text(
        json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
