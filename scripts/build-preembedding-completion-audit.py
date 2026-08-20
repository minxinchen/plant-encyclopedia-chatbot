#!/usr/bin/env python3
"""Build a requirement-by-requirement completion audit for preembedding-v1.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def requirement(
    requirement_id: str,
    description: str,
    expected: Any,
    observed: Any,
    achieved: bool,
    evidence_paths: list[str],
) -> dict:
    return {
        "requirement_id": requirement_id,
        "description": description,
        "expected": expected,
        "observed": observed,
        "status": "achieved" if achieved else "in_progress",
        "evidence_paths": evidence_paths,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    root = args.root
    manifest = read_json(root / "manifest.json")
    batch = read_json(root / "batch-status.json")
    summary = read_json(root / "checks/integration-summary.json")
    checks = read_jsonl(root / "checks/structure-validation.jsonl")
    continuation_checks = read_jsonl(root / "checks/continuation-validation.jsonl")
    recovery_checks = read_jsonl(root / "checks/content-recovery-validation.jsonl")
    dispositions = read_jsonl(root / "integration/entry-dispositions.jsonl")
    continuations = read_jsonl(root / "structure/continuation-work-packages.jsonl")
    embedding = read_json(root / "integration/embedding-ready-candidate-manifest.json")
    source_validation = read_json(root / "checks/source-receipt-validation.json")

    input_counts = Counter(item["input_disposition"] for item in dispositions)
    terminal_count = sum(item.get("terminal") is True for item in dispositions)
    exact_locator_entries = sum(
        bool(item.get("source_locators"))
        and all(
            isinstance(locator.get("char_start"), int)
            and isinstance(locator.get("char_end"), int)
            and isinstance(locator.get("exact_text_sha256"), str)
            and len(locator["exact_text_sha256"]) == 64
            and isinstance(locator.get("page_text_sha256"), str)
            and len(locator["page_text_sha256"]) == 64
            and isinstance(locator.get("source_pdf_sha256"), str)
            and len(locator["source_pdf_sha256"]) == 64
            for locator in item["source_locators"]
        )
        for item in dispositions
    )
    needs_review = [item["entry_id"] for item in checks if not item["status"].startswith("pass")]
    continuation_parents = {item["parent_entry_id"] for item in continuations}
    all_safe = (
        all(item.get("name_resolution_status") == "unresolved" for item in dispositions)
        and all(item.get("layout_or_plate_claims_approved") is False for item in dispositions)
        and embedding.get("canonical_write_allowed") is False
        and embedding.get("embedding_calls_performed") is False
        and embedding.get("vector_space_id") is None
        and all(
            candidate.get("display_name") is None
            and candidate.get("name_resolution") == {"status": "unresolved", "sources": []}
            and candidate.get("review_status") == "machine_extracted"
            and candidate.get("layout_or_plate_claims_approved") is False
            and all(section["section_type"] != "plate_description" for section in candidate["sections"])
            for candidate in embedding.get("candidates", [])
        )
    )

    requirements = [
        requirement(
            "detected-entry-inventory",
            "Frozen inventory contains exactly 265 mutually tracked detected entries.",
            265,
            len(dispositions),
            len(dispositions) == 265 == manifest["totals"]["detected_entries"],
            ["manifest.json", "integration/entry-dispositions.jsonl"],
        ),
        requirement(
            "local-maker-batch",
            "All 231 eligible local structure entries have maker receipts and the primary batch is complete.",
            {"receipts": 231, "batch_status": "complete"},
            {"receipts": len(checks), "batch_status": batch.get("status")},
            len(checks) == 231 and batch.get("status") == "complete",
            ["batch-status.json", "checks/structure-validation.jsonl"],
        ),
        requirement(
            "deterministic-maker-validation",
            "Every maker receipt has a deterministic source/hash/schema terminal validation.",
            {"checked": 231, "needs_review": 0},
            {"checked": len(checks), "needs_review": len(needs_review), "entry_ids": needs_review},
            len(checks) == 231 and not needs_review,
            ["checks/structure-validation.jsonl", "structure/deterministic-repairs.jsonl"],
        ),
        requirement(
            "span-over-limit-continuations",
            "All 18 over-limit parents are split into traceable work packages of at most six pages.",
            {"parents": 18, "maximum_pages": 6},
            {
                "parents": len(continuation_parents),
                "packages": len(continuations),
                "maximum_pages": max((item["page_count"] for item in continuations), default=0),
            },
            len(continuation_parents) == 18 and len(continuations) > 0
            and max(item["page_count"] for item in continuations) <= 6,
            ["structure/continuation-work-packages.jsonl", "integration/entry-dispositions.jsonl"],
        ),
        requirement(
            "continuation-receipts-complete",
            "All 41 continuation packages have deterministic-pass receipts and all 18 parents are integrated.",
            {"receipts": 41, "needs_review": 0, "validated_parents": 18},
            {
                "receipts": len(continuation_checks),
                "passed": sum(item.get("status") == "pass" for item in continuation_checks),
                "needs_review": sum(item.get("status") != "pass" for item in continuation_checks),
                "validated_parents": sum(
                    item.get("terminal_disposition") == "continuation_structure_validated"
                    for item in dispositions
                ),
            },
            len(continuation_checks) == 41
            and all(item.get("status") == "pass" for item in continuation_checks)
            and sum(
                item.get("terminal_disposition") == "continuation_structure_validated"
                for item in dispositions
            ) == 18,
            [
                "checks/continuation-validation.jsonl",
                "structure/continuation-maker-receipts/",
                "integration/entry-dispositions.jsonl",
            ],
        ),
        requirement(
            "terminal-no-next-heading",
            "The frozen inventory retains all four terminal-no-next-heading parents for deterministic boundary recovery tracking.",
            4,
            input_counts["hold_terminal_no_next_heading"],
            input_counts["hold_terminal_no_next_heading"] == 4,
            ["integration/entry-dispositions.jsonl"],
        ),
        requirement(
            "page-quality-holds",
            "The frozen inventory retains all four page-quality parent entries for deterministic recovery tracking.",
            4,
            input_counts["hold_page_quality"],
            input_counts["hold_page_quality"] == 4,
            ["integration/entry-dispositions.jsonl"],
        ),
        requirement(
            "content-hold-recovery-complete",
            "All nine recovery packages pass and all eight prior content-hold parents have resolved dispositions.",
            {"receipts": 9, "needs_review": 0, "recovered_parents": 8, "unresolved_content_holds": 0},
            {
                "receipts": len(recovery_checks),
                "passed": sum(item.get("status") == "pass" for item in recovery_checks),
                "needs_review": sum(item.get("status") != "pass" for item in recovery_checks),
                "recovered_parents": sum(
                    item.get("terminal_disposition") in {
                        "page_quality_structure_recovered", "terminal_body_boundaries_recovered"
                    }
                    for item in dispositions
                ),
                "unresolved_content_holds": sum(
                    item["input_disposition"] in {"hold_page_quality", "hold_terminal_no_next_heading"}
                    and not item.get("terminal")
                    for item in dispositions
                ),
            },
            len(recovery_checks) == 9
            and all(item.get("status") == "pass" for item in recovery_checks)
            and sum(
                item.get("terminal_disposition") in {
                    "page_quality_structure_recovered", "terminal_body_boundaries_recovered"
                }
                for item in dispositions
            ) == 8
            and not any(
                item["input_disposition"] in {"hold_page_quality", "hold_terminal_no_next_heading"}
                and not item.get("terminal")
                for item in dispositions
            ),
            [
                "structure/content-recovery-work-packages.jsonl",
                "structure/recovery-maker-receipts/",
                "checks/content-recovery-validation.jsonl",
                "integration/entry-dispositions.jsonl",
            ],
        ),
        requirement(
            "approved-overlap-dispositions",
            "Eight approved overlaps retain record references and are not duplicated by the maker.",
            8,
            input_counts["already_approved_overlap"],
            input_counts["already_approved_overlap"] == 8
            and all(
                item.get("approved_record_refs")
                for item in dispositions if item["input_disposition"] == "already_approved_overlap"
            ),
            ["integration/entry-dispositions.jsonl", "../../records/"],
        ),
        requirement(
            "all-entry-terminal-disposition",
            "All 265 detected entries have terminal dispositions.",
            265,
            terminal_count,
            terminal_count == 265,
            ["integration/entry-dispositions.jsonl", "checks/integration-summary.json"],
        ),
        requirement(
            "exact-source-locators",
            "All 265 entries retain page, char, exact-text, page-text and source-PDF hashes.",
            265,
            exact_locator_entries,
            exact_locator_entries == 265,
            ["integration/entry-dispositions.jsonl", "source-receipt.json"],
        ),
        requirement(
            "source-pdf-byte-hashes",
            "All four currently mounted source PDFs match the frozen source receipt byte-for-byte by SHA-256.",
            {"files": 4, "full_hash_verified": True},
            {
                "files": source_validation.get("file_count"),
                "mode": source_validation.get("mode"),
                "full_hash_verified": source_validation.get("full_hash_verified"),
                "errors": source_validation.get("errors"),
            },
            source_validation.get("status") == "PASS"
            and source_validation.get("file_count") == 4
            and source_validation.get("full_hash_verified") is True,
            ["source-receipt.json", "checks/source-receipt-validation.json"],
        ),
        requirement(
            "embedding-ready-candidate-manifest",
            "A staging-only embedding-ready candidate manifest is generated from validated text sections.",
            {"generated": True, "candidate_count_matches": True},
            {
                "generated": True,
                "status": embedding.get("status"),
                "candidate_count": embedding.get("candidate_count"),
                "candidate_count_matches": embedding.get("candidate_count") == len(embedding.get("candidates", [])),
            },
            embedding.get("candidate_count") == len(embedding.get("candidates", [])),
            ["integration/embedding-ready-candidate-manifest.json"],
        ),
        requirement(
            "safety-boundaries",
            "No Taiwan name is guessed; no layout/plate claim, canonical write, embedding call or external API is approved.",
            True,
            all_safe,
            all_safe,
            ["integration/entry-dispositions.jsonl", "integration/embedding-ready-candidate-manifest.json"],
        ),
    ]
    overall_complete = all(item["status"] == "achieved" for item in requirements)
    if overall_complete != bool(summary.get("complete")):
        raise SystemExit(
            f"completion mismatch audit={overall_complete} integration_summary={summary.get('complete')}"
        )
    audit = {
        "schema_version": "1.0",
        "pipeline_id": manifest["pipeline_id"],
        "audited_at": now(),
        "status": "complete" if overall_complete else "in_progress",
        "requirements": requirements,
        "achieved_requirements": sum(item["status"] == "achieved" for item in requirements),
        "total_requirements": len(requirements),
        "overall_complete": overall_complete,
    }
    audit["audit_sha256"] = hashlib.sha256(canonical_json(audit).encode("utf-8")).hexdigest()
    output = root / "checks/completion-audit.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    if args.require_complete and not overall_complete:
        raise SystemExit("pre-embedding completion audit is not complete")
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
