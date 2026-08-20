#!/usr/bin/env python3
"""Validate coverage-aware answer and refusal fixtures against an approved record."""

from __future__ import annotations

import json
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def main() -> None:
    record = json.loads((LAB / "data/records/cibotium-barometz.json").read_text(encoding="utf-8"))
    fixture = json.loads((LAB / "data/tests/cibotium-barometz-grounding.json").read_text(encoding="utf-8"))

    if fixture["record_id"] != record["record_id"]:
        fail("fixture record_id does not match record")

    evidence = {
        (item["source_id"], item["pdf_page"])
        for item in record["book_evidence"]
    }
    section_types = {item["section_type"] for item in record["sections"]}
    categories = set()

    for case in fixture["cases"]:
        category = case["category"]
        categories.add(category)
        disposition = case["expected_disposition"]

        if category == "answerable":
            if disposition != "answer" or case["required_section_type"] not in section_types:
                fail(f"answerable case lacks approved section: {case['case_id']}")
            for sentence in case["expected_answer_sentences"]:
                if not sentence["citations"]:
                    fail(f"answer sentence lacks citation: {case['case_id']}")
                for citation in sentence["citations"]:
                    key = (citation["source_id"], citation["pdf_page"])
                    if key not in evidence:
                        fail(f"answer citation is not approved evidence: {case['case_id']}")
        else:
            if not case.get("expected_response"):
                fail(f"non-answer case lacks exact expected response: {case['case_id']}")
            forbidden = case.get("forbidden_response")
            if forbidden and forbidden in case["expected_response"]:
                fail(f"expected response contains forbidden wording: {case['case_id']}")

    required = {"answerable", "incomplete_section", "wrong_name", "unanswerable", "medical_advice"}
    if missing := sorted(required - categories):
        fail("missing regression categories: " + ", ".join(missing))

    print(json.dumps({
        "valid": True,
        "record_id": record["record_id"],
        "cases": len(fixture["cases"]),
        "categories": sorted(categories),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
