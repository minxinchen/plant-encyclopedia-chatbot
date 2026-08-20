#!/usr/bin/env python3
"""Validate the long-span boundary-evidence staging overlay.

author: Codex (GPT-5)
date: 2026-08-20
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = LAB / "data/candidates/preembedding-v1/boundary-evidence-v1"
ALLOWED_DECISIONS = {
    "confirmed_parent_heading",
    "confirmed_hidden_heading",
    "candidate_needs_secondary_evidence",
    "no_page_heading_detected",
}
KNOWN_FALSE_POSITIVES = {
    "Der wirksame",
    "Das Kakaoroth",
    "Offieinell ist",
    "Gutta Percha",
    "Ein Produkt",
    "Perigon fällt",
    "Kautschukindustrie heute",
    "Arten sind",
    "Das Anhydromuscarin",
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = read_jsonl(args.output / "boundary-evidence.jsonl")
    manifest = read_json(args.output / "manifest.json")
    errors: list[str] = []

    if len(rows) != manifest.get("scope", {}).get("evaluated_pages"):
        fail(errors, "row count does not match manifest scope")
    keys = [(row.get("parent_entry_id"), row.get("source_id"), row.get("pdf_page")) for row in rows]
    if len(keys) != len(set(keys)):
        fail(errors, "duplicate parent/source/page evidence key")
    if len({row.get("parent_entry_id") for row in rows}) != 18:
        fail(errors, "boundary evidence must cover exactly 18 frozen over-limit parents")

    counts = Counter(row.get("decision") for row in rows)
    if set(counts) - ALLOWED_DECISIONS:
        fail(errors, f"unknown decisions: {sorted(set(counts) - ALLOWED_DECISIONS)}")
    if dict(sorted(counts.items())) != manifest.get("decision_counts"):
        fail(errors, "decision counts do not match manifest")

    for row in rows:
        stored = row.get("evidence_sha256")
        unhashed = dict(row)
        unhashed.pop("evidence_sha256", None)
        if stored != digest(unhashed):
            fail(errors, f"evidence hash mismatch: {row.get('source_id')} p{row.get('pdf_page')}")
        if row.get("canonical_write_allowed") is not False:
            fail(errors, "canonical write escaped boundary staging")
        if row.get("taiwan_name_resolution_allowed") is not False:
            fail(errors, "Taiwan naming escaped boundary staging")
        if row.get("layout_or_image_claims_approved") is not False:
            fail(errors, "layout/image approval escaped boundary staging")

        confirmed = row.get("decision") in {"confirmed_parent_heading", "confirmed_hidden_heading"}
        local = row.get("local_heading") or {}
        external = row.get("ia_heading") or {}
        name_gate = row.get("scientific_name_evidence") or {}
        if confirmed:
            boundary_gate = row.get("boundary_gate") or {}
            bhl_gate = row.get("bhl_page_name_evidence") or {}
            if not (
                row.get("canonical_taxon_agreement")
                or bhl_gate.get("candidate_or_verifier_match")
                or boundary_gate.get("source_disagreement_resolved")
            ):
                fail(errors, "confirmed boundary lacks agreement, BHL evidence, or exact resolution")
            if not (local.get("structural_signals") or external.get("structural_signals")):
                fail(errors, "confirmed boundary lacks structural heading signals")
            if not (boundary_gate.get("exact_confirmed") or boundary_gate.get("fuzzy_bhl_bridge")):
                fail(errors, "confirmed boundary lacks exact or BHL-backed fuzzy scientific-name gate")
            if boundary_gate.get("exact_confirmed") and not name_gate.get("exact_species_gate"):
                fail(errors, "exact boundary lacks complete GNfinder/GNverifier gate")
            if boundary_gate.get("fuzzy_bhl_bridge") and not bhl_gate.get("candidate_or_verifier_match"):
                fail(errors, "fuzzy boundary lacks BHL page-name bridge")
            if row.get("boundary_taxon_candidate") in KNOWN_FALSE_POSITIVES:
                fail(errors, f"known prose false positive was confirmed: {row.get('boundary_taxon_candidate')}")

    hidden = [row for row in rows if row.get("decision") == "confirmed_hidden_heading"]
    manifest_hidden = manifest.get("confirmed_hidden_headings", [])
    if len(hidden) != manifest.get("confirmed_hidden_heading_count") or len(hidden) != len(manifest_hidden):
        fail(errors, "hidden-heading count mismatch")
    if any(not asset.get("djvu_sha1") or asset.get("aligned_page_count", 0) <= 0 for asset in manifest.get("assets", [])):
        fail(errors, "source asset alignment/hash contract incomplete")

    manifest_hash = manifest.get("manifest_sha256")
    unhashed_manifest = dict(manifest)
    unhashed_manifest.pop("manifest_sha256", None)
    if manifest_hash != digest(unhashed_manifest):
        fail(errors, "manifest hash mismatch")
    safety = manifest.get("safety", {})
    for field in (
        "canonical_writes",
        "embedding_calls",
        "taiwan_names_resolved",
        "layout_or_image_claims_approved",
        "external_text_used_as_book_fact",
    ):
        if safety.get(field) is not False:
            fail(errors, f"unsafe manifest flag: {field}")

    result = {
        "valid": not errors,
        "rows": len(rows),
        "parents": len({row.get("parent_entry_id") for row in rows}),
        "confirmed_hidden_headings": len(hidden),
        "decision_counts": dict(sorted(counts.items())),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
