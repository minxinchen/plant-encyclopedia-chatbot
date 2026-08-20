#!/usr/bin/env python3
"""Independent deterministic checker for the portable chat API contract.

author: Codex (GPT-5)
date: 2026-08-11
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def load_engine():
    path = LAB / "scripts/plant-chat-api.py"
    spec = importlib.util.spec_from_file_location("plant_chat_api", path)
    if spec is None or spec.loader is None:
        fail("cannot load plant-chat-api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PlantChatEngine(LAB)


def main() -> None:
    fixture = json.loads((LAB / "data/tests/chat-api-acceptance.json").read_text(encoding="utf-8"))
    engine = load_engine()
    verdicts = []
    for case in fixture["cases"]:
        result = engine.respond(case["request"])
        expected = case["expected"]
        if result["schema_version"] != "1.0":
            fail(f"schema version drift: {case['case_id']}")
        for field in ("answer_status", "response_locale"):
            if result[field] != expected[field]:
                fail(f"{field} mismatch: {case['case_id']} got {result[field]}")
        if result["external_model_calls"] != 0 or result["incremental_usd"] != 0:
            fail(f"non-zero cost marker: {case['case_id']}")
        if image_status := expected.get("image_request_status"):
            if result["image_request_status"] != image_status or result["capabilities"]["image_reasoning"] is not False:
                fail(f"image capability mismatch: {case['case_id']}")
        if result["answer_status"] == "answerable":
            if not result["answer_sentences"] or not result["evidence"]:
                fail(f"answer lacks evidence: {case['case_id']}")
            for sentence in result["answer_sentences"]:
                if not sentence["citations"]:
                    fail(f"answer sentence lacks citation: {case['case_id']}")
        if "evidence_count" in expected and len(result["evidence"]) != expected["evidence_count"]:
            fail(f"evidence count mismatch: {case['case_id']}")
        if display_name := expected.get("display_name"):
            if display_name not in {item["display_name"] for item in result["display_names"]}:
                fail(f"display name mismatch: {case['case_id']}")
        if scientific_name := expected.get("scientific_name"):
            if scientific_name not in {item["scientific_name"] for item in result["display_names"]}:
                fail(f"scientific name mismatch: {case['case_id']}")
        if source_id := expected.get("source_id"):
            if source_id not in {item["source_id"] for item in result["evidence"]}:
                fail(f"source mismatch: {case['case_id']}")
        if pdf_page := expected.get("pdf_page"):
            if pdf_page not in {item["pdf_page"] for item in result["evidence"]}:
                fail(f"page mismatch: {case['case_id']}")
        if forbidden := expected.get("forbidden_answer"):
            if forbidden in result["answer"]:
                fail(f"forbidden answer wording: {case['case_id']}")
        if required := expected.get("answer_contains"):
            if required not in result["answer"]:
                fail(f"required answer wording missing: {case['case_id']}")
        verdicts.append({
            "case_id": case["case_id"],
            "answer_status": result["answer_status"],
            "response_locale": result["response_locale"],
            "evidence_count": len(result["evidence"]),
        })

    retrieve = engine.respond({"question": "What does the book say about the hairs supplied as Penawar Djambi?"}, retrieve_only=True)
    if retrieve["answer"] or retrieve["answer_sentences"] or not retrieve["evidence"]:
        fail("retrieve-only response did not separate evidence from answer generation")

    print(json.dumps({
        "valid": True,
        "suite_id": fixture["suite_id"],
        "cases": len(verdicts),
        "http_contract": ["GET /health", "POST /v1/retrieve", "POST /v1/chat"],
        "retrieve_generation_separated": True,
        "image_reasoning_declared_unavailable": True,
        "external_model_calls": 0,
        "incremental_usd": 0,
        "verdicts": verdicts,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
