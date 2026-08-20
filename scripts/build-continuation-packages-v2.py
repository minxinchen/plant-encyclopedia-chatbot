#!/usr/bin/env python3
"""Build taxon-safe continuation packages from the boundary overlay plan."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
ROOT = LAB / "data/candidates/preembedding-v1"
PLAN = ROOT / "boundary-evidence-v1/boundary-overlay-plan.json"
OLD = ROOT / "structure/continuation-work-packages.jsonl"
OUTPUT = ROOT / "structure/continuation-work-packages-v2.jsonl"
MANIFEST = ROOT / "structure/continuation-work-packages-v2-manifest.json"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.replace(path)


def chunks(values: list[int], size: int = 6) -> list[list[int]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def main() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if not all(parent["safe_to_replace_old_continuation"] for parent in plan["parents"]):
        raise SystemExit("boundary overlay still contains unsafe parents")
    old = read_jsonl(OLD)
    old_by_parent: dict[str, list[dict]] = {}
    locator_by_page: dict[tuple[str, int], dict] = {}
    for package in old:
        old_by_parent.setdefault(package["parent_entry_id"], []).append(package)
        for locator in package["source_locators"]:
            locator_by_page[(package["source_id"], locator["pdf_page"])] = locator

    packages = []
    for parent in plan["parents"]:
        template = old_by_parent[parent["parent_entry_id"]][0]
        for segment in parent["segments"]:
            page_groups = chunks(segment["pdf_pages"])
            for sequence, pages in enumerate(page_groups, 1):
                package_id = f"{segment['child_entry_id']}:continuation-{sequence:02d}"
                package = {
                    "schema_version": "2.0",
                    "package_id": package_id,
                    "work_id": f"structure-continuation-v2:{package_id}:v1",
                    "stage": "local_structure_continuation_v2",
                    "parent_entry_id": parent["parent_entry_id"],
                    "child_entry_id": segment["child_entry_id"],
                    "owner_shard": template["owner_shard"],
                    "source_id": parent["source_id"],
                    "volume": template["volume"],
                    "pdf_pages": pages,
                    "page_count": len(pages),
                    "sequence": sequence,
                    "sequence_count": len(page_groups),
                    "book_taxon_candidate": segment["taxon_candidate"],
                    "source_locators": [locator_by_page[(parent["source_id"], page)] for page in pages],
                    "boundary_overlay_plan_sha256": plan["plan_sha256"],
                    "boundary_segment_sha256": segment["segment_sha256"],
                    "review_status": "candidate",
                    "name_resolution_status": "unresolved",
                    "layout_or_plate_claims_approved": False,
                    "status": "planned_taxon_safe_continuation",
                    "dependencies": [
                        "primary_local_structure_batch_complete",
                        "boundary_overlay_plan_valid",
                    ],
                    "route": {
                        "maker": "local_qwen_structure",
                        "checker": "deterministic_source_hash_schema_taxon_boundary_validator",
                    },
                    "forbidden": [
                        "canonical_record_write",
                        "canonical_chunk_write",
                        "embedding_index_write",
                        "source_pdf_write",
                        "external_api",
                        "taiwan_name_invention",
                        "layout_or_plate_self_approval",
                    ],
                }
                package["package_sha256"] = digest(package)
                packages.append(package)

    manifest = {
        "schema_version": "2.0",
        "pipeline_id": "preembedding-continuation-v2",
        "boundary_overlay_plan_sha256": plan["plan_sha256"],
        "parent_count": len(plan["parents"]),
        "child_segment_count": sum(len(parent["segments"]) for parent in plan["parents"]),
        "package_count": len(packages),
        "page_count": sum(package["page_count"] for package in packages),
        "maximum_pages_per_package": max(package["page_count"] for package in packages),
        "old_package_count": len(old),
        "old_packages_modified": False,
        "canonical_writes": False,
    }
    manifest["manifest_sha256"] = digest(manifest)
    atomic_write(OUTPUT, "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in packages))
    atomic_write(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
