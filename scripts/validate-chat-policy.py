#!/usr/bin/env python3
"""Validate language, Köhler scope and Taiwan-name acceptance policy without a model call."""

from __future__ import annotations

import json
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def response_locale(policy: dict, input_language: str) -> str:
    route = policy["input_language_policy"].get(input_language, policy["input_language_policy"]["other"])
    return "en" if route == "respond_en" else "zh-TW"


def choose_name(policy: dict, candidates: list[dict], context: str | None) -> str | None:
    if not candidates or not context:
        return None
    priority = policy["name_priority"][context]
    order = {status: index for index, status in enumerate(priority)}
    eligible = [item for item in candidates if item["status"] in order]
    if not eligible:
        fail(f"no eligible name candidate for context {context}")
    return min(eligible, key=lambda item: order[item["status"]])["value"]


def disposition(case: dict) -> str:
    if case["domain"] == "non_kohler_drug":
        return "out_of_scope"
    if case["medical_advice"]:
        return "refuse_medical_advice"
    if case["domain"] == "name_metadata_only":
        return "name_metadata"
    if case["retrieval_status"] == "approved_evidence":
        return "answer"
    return "book_not_recorded" if case["relevant_scope_processed"] else "not_yet_processed"


def main() -> None:
    policy = json.loads((LAB / "config/chat-policy.json").read_text(encoding="utf-8"))
    registry = json.loads((LAB / policy["source_registry"]).read_text(encoding="utf-8"))
    fixture = json.loads((LAB / policy["acceptance_policy"]["fixture"]).read_text(encoding="utf-8"))
    sample_names = json.loads((LAB / "data/sample-name-resolution.json").read_text(encoding="utf-8"))
    record = json.loads((LAB / "data/records/cibotium-barometz.json").read_text(encoding="utf-8"))

    if fixture.get("acceptance_gate") is not True:
        fail("fixture is not marked as an acceptance gate")
    if "libra-plant-zh-tw" not in fixture.get("excluded_exploratory_cases", []):
        fail("historical Libra probe was not excluded from acceptance")
    if "libra-plant-zh-tw" not in policy["acceptance_policy"]["historical_exploratory_only"]:
        fail("policy did not classify Libra as exploratory only")

    source_ids = {item["source_id"] for item in registry["sources"]}
    required_sources = {
        "taicol", "tai2", "taiwan-herbal-pharmacopeia-4",
        "mohw-prescription-materia-medica-names", "tbd", "tbn",
        "taif", "hast", "moe-revised-mandarin", "moe-taigi",
        "moe-hakka", "indigenous-language-dictionary",
    }
    if missing := sorted(required_sources - source_ids):
        fail("name-source registry missing: " + ", ".join(missing))
    if len(source_ids) != len(registry["sources"]):
        fail("duplicate source_id in name-source registry")
    if any(not item["url"].startswith("https://") for item in registry["sources"]):
        fail("registry contains a non-HTTPS source")

    plant_priority = policy["name_priority"]["plant_display_name"]
    medicinal_priority = policy["name_priority"]["medicinal_material_name"]
    if plant_priority[-1] != "simplified_chinese_fallback" or medicinal_priority[-1] != "simplified_chinese_fallback":
        fail("simplified Chinese is not the final fallback")
    if plant_priority[:2] != ["taiwan_catalogue_preferred", "taiwan_public_name"]:
        fail("Taiwan plant names are not first")
    if medicinal_priority[:2] != ["taiwan_herbal_pharmacopeia_current", "taiwan_mohw_prescription_name"]:
        fail("Taiwan materia-medica names are not first")

    known_names = {item["query_scientific_name"]: item for item in sample_names["records"]}
    if known_names["Cibotium barometz"]["display_name_zh_tw"] != record["display_name"]:
        fail("Cibotium Taiwan display name drift")
    if known_names["Saponaria officinalis"]["display_name_zh_tw"] != "皂質草":
        fail("Saponaria TaiCOL display name drift")

    categories: set[str] = set()
    verdicts: list[dict] = []
    approved_evidence = {(item["source_id"], item["pdf_page"]) for item in record["book_evidence"]}
    for case in fixture["cases"]:
        categories.add(case["category"])
        expected = case["expected"]
        actual_disposition = disposition(case)
        actual_locale = response_locale(policy, case["input_language"])
        actual_name = choose_name(policy, case["name_candidates"], case["name_context"])
        if actual_disposition != expected["disposition"]:
            fail(f"disposition mismatch: {case['case_id']}")
        if actual_locale != expected["response_locale"]:
            fail(f"locale mismatch: {case['case_id']}")
        if "display_name" in expected and actual_name != expected["display_name"]:
            fail(f"display-name mismatch: {case['case_id']}")
        if actual_name in expected.get("forbidden_display_names", []):
            fail(f"forbidden display name selected: {case['case_id']}")
        if expected.get("scientific_name_required") and not case.get("scientific_name"):
            fail(f"English answer lacks scientific-name contract: {case['case_id']}")
        if expected["citation_required"]:
            if not case["citations"]:
                fail(f"answer lacks citation: {case['case_id']}")
            for citation in case["citations"]:
                if (citation["source_id"], citation["pdf_page"]) not in approved_evidence:
                    fail(f"citation is not approved evidence: {case['case_id']}")
        elif case["domain"] == "non_kohler_drug" and case["citations"]:
            fail(f"out-of-scope drug answer attempted citation: {case['case_id']}")
        if template := expected.get("template"):
            if template not in policy["response_templates"]:
                fail(f"missing response template: {case['case_id']}")
        verdicts.append({
            "case_id": case["case_id"],
            "disposition": actual_disposition,
            "response_locale": actual_locale,
            "display_name": actual_name,
        })

    required_categories = set(policy["acceptance_policy"]["required_categories"])
    if missing := sorted(required_categories - categories):
        fail("acceptance suite missing categories: " + ", ".join(missing))

    print(json.dumps({
        "valid": True,
        "policy_id": policy["policy_id"],
        "registry_sources": len(registry["sources"]),
        "acceptance_cases": len(fixture["cases"]),
        "categories": sorted(categories),
        "external_model_calls": 0,
        "incremental_usd": 0,
        "verdicts": verdicts,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
