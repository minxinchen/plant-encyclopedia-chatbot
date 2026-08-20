#!/usr/bin/env python3
"""Turn validated boundary evidence into a non-canonical child-span plan.

author: Codex (GPT-5)
date: 2026-08-20
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
ROOT = LAB / "data/candidates/preembedding-v1"
EVIDENCE = ROOT / "boundary-evidence-v1/boundary-evidence.jsonl"
OUTPUT = ROOT / "boundary-evidence-v1/boundary-overlay-plan.json"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = Path(handle.name)
    temp.replace(path)


def main() -> None:
    rows = [json.loads(line) for line in EVIDENCE.read_text(encoding="utf-8").splitlines() if line]
    by_parent: dict[str, list[dict]] = {}
    for row in rows:
        by_parent.setdefault(row["parent_entry_id"], []).append(row)

    parents = []
    for parent_id, parent_rows in sorted(by_parent.items()):
        parent_rows.sort(key=lambda item: item["pdf_page"])
        start = parent_rows[0]["pdf_page"]
        end = parent_rows[-1]["pdf_page"]
        boundaries = [{
            "pdf_page": start,
            "taxon_candidate": parent_rows[0]["frozen_parent_taxon"],
            "authority": "frozen_parent_start",
            "evidence_sha256": parent_rows[0]["evidence_sha256"],
        }]
        boundaries.extend({
            "pdf_page": row["pdf_page"],
            "taxon_candidate": row["boundary_taxon_candidate"],
            "authority": "confirmed_hidden_heading",
            "evidence_sha256": row["evidence_sha256"],
        } for row in parent_rows if row["decision"] == "confirmed_hidden_heading")
        boundaries.sort(key=lambda item: item["pdf_page"])

        review_candidates = []
        non_boundary_candidates = []
        for row in parent_rows:
            if row["is_frozen_parent_start"] or row["decision"] != "candidate_needs_secondary_evidence":
                continue
            heading = row.get("ia_heading") or row.get("local_heading") or {}
            scientific = row.get("scientific_name_evidence") or {}
            boundary_gate = row.get("boundary_gate") or {}
            bhl = row.get("bhl_page_name_evidence") or {}
            plausible = (
                heading.get("line_order", 99) <= 1
                and scientific.get("verifier_cardinality", 0) >= 2
                and scientific.get("verifier_match_type") in {"Exact", "Fuzzy"}
                and (boundary_gate.get("structural_signal") or bhl.get("candidate_or_verifier_match"))
            )
            if plausible:
                candidate = {
                    "pdf_page": row["pdf_page"],
                    "taxon_candidate": row.get("boundary_taxon_candidate"),
                    "verifier_match_type": scientific.get("verifier_match_type"),
                    "verifier_matched_name": scientific.get("verifier_matched_name"),
                    "structural_signal": boundary_gate.get("structural_signal"),
                    "bhl_page_name_match": bhl.get("candidate_or_verifier_match"),
                    "evidence_sha256": row["evidence_sha256"],
                }
                prior_boundaries = [item for item in boundaries if item["pdf_page"] < row["pdf_page"]]
                prior_taxon = prior_boundaries[-1]["taxon_candidate"] if prior_boundaries else ""
                heading_line = heading.get("heading_line", "")
                same_genus_continuation = bool(
                    prior_taxon
                    and candidate["taxon_candidate"]
                    and prior_taxon.split()[0].casefold() == candidate["taxon_candidate"].split()[0].casefold()
                    and not boundary_gate.get("structural_signal")
                    and len(heading_line) > 80
                    and re.search(r"\b[A-Z][a-z]+,\s+(mit|ein(?:e[rmns]?)?)\b", heading_line)
                )
                if same_genus_continuation:
                    candidate["disposition"] = "same_genus_subtaxon_continuation_not_entry_boundary"
                    candidate["rule"] = {
                        "prior_boundary_taxon": prior_taxon,
                        "same_genus": True,
                        "structural_signal": False,
                        "heading_continues_as_prose": True,
                    }
                    non_boundary_candidates.append(candidate)
                else:
                    candidate["disposition"] = "requires_review"
                    review_candidates.append(candidate)

        segments = []
        for index, boundary in enumerate(boundaries):
            segment_end = boundaries[index + 1]["pdf_page"] - 1 if index + 1 < len(boundaries) else end
            pages = list(range(boundary["pdf_page"], segment_end + 1))
            segment = {
                "child_entry_id": f"{parent_id}:child-{index + 1:02d}",
                "taxon_candidate": boundary["taxon_candidate"],
                "start_pdf_page": pages[0],
                "end_pdf_page": pages[-1],
                "pdf_pages": pages,
                "page_count": len(pages),
                "requires_continuation_split": len(pages) > 6,
                "boundary_authority": boundary["authority"],
                "boundary_evidence_sha256": boundary["evidence_sha256"],
            }
            segment["segment_sha256"] = digest(segment)
            segments.append(segment)

        covered = [page for segment in segments for page in segment["pdf_pages"]]
        exact_coverage = covered == list(range(start, end + 1)) and len(covered) == len(set(covered))
        parent = {
            "parent_entry_id": parent_id,
            "source_id": parent_rows[0]["source_id"],
            "start_pdf_page": start,
            "end_pdf_page": end,
            "segments": segments,
            "review_candidates": review_candidates,
            "non_boundary_candidates": non_boundary_candidates,
            "exact_page_coverage": exact_coverage,
            "safe_to_replace_old_continuation": exact_coverage and not review_candidates,
            "canonical_write_allowed": False,
        }
        parent["parent_plan_sha256"] = digest(parent)
        parents.append(parent)

    plan = {
        "schema_version": "1.0",
        "pipeline_id": "kohler-boundary-overlay-plan-v1",
        "parents": parents,
        "summary": {
            "parents": len(parents),
            "child_segments": sum(len(parent["segments"]) for parent in parents),
            "safe_parents": sum(parent["safe_to_replace_old_continuation"] for parent in parents),
            "parents_requiring_review": sum(bool(parent["review_candidates"]) for parent in parents),
            "review_candidates": sum(len(parent["review_candidates"]) for parent in parents),
            "resolved_non_boundary_candidates": sum(len(parent["non_boundary_candidates"]) for parent in parents),
        },
        "safety": {
            "canonical_writes": False,
            "old_continuation_packages_modified": False,
            "embedding_calls": False,
            "taiwan_names_resolved": False,
            "layout_or_image_claims_approved": False,
        },
    }
    plan["plan_sha256"] = digest(plan)
    write_json(OUTPUT, plan)
    print(json.dumps(plan["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
