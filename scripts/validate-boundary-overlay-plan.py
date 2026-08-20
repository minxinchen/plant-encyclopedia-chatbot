#!/usr/bin/env python3
"""Validate boundary overlay coverage, hash chain, and promotion safety."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
PATH = LAB / "data/candidates/preembedding-v1/boundary-evidence-v1/boundary-overlay-plan.json"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> None:
    plan = json.loads(PATH.read_text(encoding="utf-8"))
    errors = []
    child_ids = []
    for parent in plan.get("parents", []):
        parent_hash = parent.get("parent_plan_sha256")
        unhashed_parent = dict(parent)
        unhashed_parent.pop("parent_plan_sha256", None)
        if parent_hash != digest(unhashed_parent):
            errors.append(f"parent hash mismatch: {parent.get('parent_entry_id')}")
        expected = list(range(parent["start_pdf_page"], parent["end_pdf_page"] + 1))
        covered = []
        for segment in parent["segments"]:
            segment_hash = segment.get("segment_sha256")
            unhashed_segment = dict(segment)
            unhashed_segment.pop("segment_sha256", None)
            if segment_hash != digest(unhashed_segment):
                errors.append(f"segment hash mismatch: {segment.get('child_entry_id')}")
            if segment["page_count"] != len(segment["pdf_pages"]):
                errors.append(f"page count mismatch: {segment.get('child_entry_id')}")
            if segment["requires_continuation_split"] != (segment["page_count"] > 6):
                errors.append(f"split flag mismatch: {segment.get('child_entry_id')}")
            child_ids.append(segment["child_entry_id"])
            covered.extend(segment["pdf_pages"])
        if covered != expected or len(covered) != len(set(covered)) or not parent["exact_page_coverage"]:
            errors.append(f"non-exact page coverage: {parent.get('parent_entry_id')}")
        expected_safe = not parent["review_candidates"] and parent["exact_page_coverage"]
        if parent["safe_to_replace_old_continuation"] != expected_safe:
            errors.append(f"unsafe replace status: {parent.get('parent_entry_id')}")
        if parent.get("canonical_write_allowed") is not False:
            errors.append(f"canonical write escaped: {parent.get('parent_entry_id')}")
        for candidate in parent.get("non_boundary_candidates", []):
            rule = candidate.get("rule") or {}
            if candidate.get("disposition") != "same_genus_subtaxon_continuation_not_entry_boundary":
                errors.append(f"unknown non-boundary disposition: {parent.get('parent_entry_id')}")
            if not (
                rule.get("same_genus") is True
                and rule.get("structural_signal") is False
                and rule.get("heading_continues_as_prose") is True
                and rule.get("prior_boundary_taxon")
            ):
                errors.append(f"non-boundary rule incomplete: {parent.get('parent_entry_id')}")
    if len(child_ids) != len(set(child_ids)):
        errors.append("duplicate child entry id")
    stored = plan.get("plan_sha256")
    unhashed = dict(plan)
    unhashed.pop("plan_sha256", None)
    if stored != digest(unhashed):
        errors.append("plan hash mismatch")
    if any(value is not False for key, value in plan.get("safety", {}).items() if key != "unused"):
        errors.append("unsafe plan-level flag")
    result = {"valid": not errors, "summary": plan.get("summary"), "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
