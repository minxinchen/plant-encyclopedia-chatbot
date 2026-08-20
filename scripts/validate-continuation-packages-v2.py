#!/usr/bin/env python3
"""Validate taxon-safe continuation v2 packages before any model call."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
ROOT = LAB / "data/candidates/preembedding-v1"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    plan = json.loads((ROOT / "boundary-evidence-v1/boundary-overlay-plan.json").read_text())
    manifest = json.loads((ROOT / "structure/continuation-work-packages-v2-manifest.json").read_text())
    packages = read_jsonl(ROOT / "structure/continuation-work-packages-v2.jsonl")
    errors = []
    segments = {
        segment["child_entry_id"]: segment
        for parent in plan["parents"]
        for segment in parent["segments"]
    }
    by_child: dict[str, list[dict]] = {}
    package_ids = []
    for package in packages:
        package_ids.append(package["package_id"])
        stored = package.get("package_sha256")
        unhashed = dict(package)
        unhashed.pop("package_sha256", None)
        if stored != digest(unhashed):
            errors.append(f"package hash mismatch: {package['package_id']}")
        segment = segments.get(package.get("child_entry_id"))
        if not segment:
            errors.append(f"unknown child segment: {package['package_id']}")
            continue
        if package["boundary_overlay_plan_sha256"] != plan["plan_sha256"]:
            errors.append(f"plan hash mismatch: {package['package_id']}")
        if package["boundary_segment_sha256"] != segment["segment_sha256"]:
            errors.append(f"segment hash mismatch: {package['package_id']}")
        if package["book_taxon_candidate"] != segment["taxon_candidate"]:
            errors.append(f"cross-taxon package: {package['package_id']}")
        if package["page_count"] != len(package["pdf_pages"]) or not 1 <= package["page_count"] <= 6:
            errors.append(f"invalid package page count: {package['package_id']}")
        if [item["pdf_page"] for item in package["source_locators"]] != package["pdf_pages"]:
            errors.append(f"source locator coverage drift: {package['package_id']}")
        if package.get("layout_or_plate_claims_approved") is not False:
            errors.append(f"layout approval escaped: {package['package_id']}")
        by_child.setdefault(package["child_entry_id"], []).append(package)
    if len(package_ids) != len(set(package_ids)):
        errors.append("duplicate package id")
    for child_id, segment in segments.items():
        child_packages = sorted(by_child.get(child_id, []), key=lambda item: item["sequence"])
        pages = [page for package in child_packages for page in package["pdf_pages"]]
        if pages != segment["pdf_pages"] or len(pages) != len(set(pages)):
            errors.append(f"child page coverage drift: {child_id}")
        if [package["sequence"] for package in child_packages] != list(range(1, len(child_packages) + 1)):
            errors.append(f"child sequence drift: {child_id}")
        if any(package["sequence_count"] != len(child_packages) for package in child_packages):
            errors.append(f"child sequence count drift: {child_id}")
    manifest_hash = manifest.get("manifest_sha256")
    unhashed_manifest = dict(manifest)
    unhashed_manifest.pop("manifest_sha256", None)
    if manifest_hash != digest(unhashed_manifest):
        errors.append("manifest hash mismatch")
    if manifest.get("package_count") != len(packages) or manifest.get("child_segment_count") != len(segments):
        errors.append("manifest count mismatch")
    result = {
        "valid": not errors,
        "parent_count": len(plan["parents"]),
        "child_segment_count": len(segments),
        "package_count": len(packages),
        "page_count": sum(package["page_count"] for package in packages),
        "maximum_pages_per_package": max(package["page_count"] for package in packages),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
