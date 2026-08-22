#!/usr/bin/env python3
"""Bounded live acceptance for the completed full-book beta chat index.

The default mode validates prerequisites only. ``--execute`` is required for
the ten free-tier Gemini requests used by the bilingual retrieval/generation
checks. Policy refusals are verified to make zero external calls.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_DB = LAB / "data/index/staging/plant-embeddings-fullbook-beta.sqlite"
DEFAULT_REPORT = LAB / "reports/fullbook-beta-chat-acceptance.json"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_chat_module():
    path = LAB / "scripts/fullbook-beta-chat.py"
    spec = importlib.util.spec_from_file_location("fullbook_beta_chat", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load fullbook beta chat")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compact(response: dict[str, Any]) -> dict[str, Any]:
    compacted = {
        "answer_status": response["answer_status"],
        "response_locale": response["response_locale"],
        "answer": response["answer"],
        "evidence": response["evidence"],
        "external_embedding_calls": response["external_embedding_calls"],
        "external_generation_calls": response["external_generation_calls"],
        "incremental_usd": response["incremental_usd"],
    }
    for key in ("answer_mode", "verified_items"):
        if key in response:
            compacted[key] = response[key]
    return compacted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    prerequisites: list[dict[str, Any]] = []

    def prerequisite(name: str, command: list[str]) -> None:
        result = subprocess.run(command, cwd=LAB, text=True, capture_output=True)
        prerequisites.append({
            "name": name, "passed": result.returncode == 0,
            "output": (result.stdout or result.stderr).strip()[-3000:],
        })

    prerequisite("offline_chat_policy", ["python3", "scripts/test-fullbook-beta-chat-policy.py"])
    if args.database.exists():
        prerequisite("fullbook_beta_index", [
            "python3", "scripts/validate-fullbook-beta-index.py", "--beta", str(args.database),
        ])
    else:
        prerequisites.append({"name": "fullbook_beta_index", "passed": False, "output": "missing"})
    gate = {
        "schema_version": "1.0", "checked_at": now(),
        "ready": all(item["passed"] for item in prerequisites), "prerequisites": prerequisites,
    }
    if not args.execute:
        print(json.dumps(gate, ensure_ascii=False))
        raise SystemExit(0 if gate["ready"] else 2)
    if not gate["ready"]:
        raise SystemExit("full-book beta chat acceptance prerequisites are not ready")

    chat = load_chat_module()
    results: list[dict[str, Any]] = []
    failures: list[str] = []

    def record(case_id: str, response: dict[str, Any], assertions: dict[str, bool]) -> None:
        failed = [name for name, passed in assertions.items() if not passed]
        if failed:
            failures.extend(f"{case_id}:{name}" for name in failed)
        results.append({"case_id": case_id, "passed": not failed,
                        "failed_assertions": failed, "response": compact(response)})

    zh = chat.answer(
        "這本書如何記載盾葉鬼臼與習慣性便秘的關係？", args.database,
        chat.DEFAULT_ENV, 4, False,
    )
    record("zh_tw_book_answer", zh, {
        "answerable": zh["answer_status"] == "answerable_from_book",
        "locale": zh["response_locale"] == "zh-TW",
        "top_source": bool(zh["evidence"]) and zh["evidence"][0]["source_id"] == "kohler-volume-1",
        "top_page": bool(zh["evidence"]) and zh["evidence"][0]["pdf_page"] == 192,
        "taiwan_name": "盾葉鬼臼" in zh["answer"],
        "historical_frame": "歷史文獻" in zh["answer"],
        "medical_disclaimer": "不是現代醫療" in zh["answer"],
        "bounded_calls": zh["external_embedding_calls"] == 1 and zh["external_generation_calls"] == 1,
    })

    en = chat.answer(
        "What does the book say about Podophyllum peltatum and habitual constipation?",
        args.database, chat.DEFAULT_ENV, 4, False,
    )
    record("english_book_answer", en, {
        "answerable": en["answer_status"] == "answerable_from_book",
        "locale": en["response_locale"] == "en",
        "top_page": bool(en["evidence"]) and en["evidence"][0]["pdf_page"] == 192,
        "scientific_name": "Podophyllum peltatum" in en["answer"],
        "taiwan_name": "盾葉鬼臼" in en["answer"],
        "historical_frame": "historical book" in en["answer"],
        "medical_disclaimer": "not modern medical" in en["answer"],
        "bounded_calls": en["external_embedding_calls"] == 1 and en["external_generation_calls"] == 1,
    })

    simplified = chat.answer(
        "这本书如何记载盾叶鬼臼与习惯性便秘的关系？", args.database,
        chat.DEFAULT_ENV, 4, True,
    )
    record("simplified_input_traditional_output", simplified, {
        "retrieval_only": simplified["answer_status"] == "retrieval_only",
        "locale": simplified["response_locale"] == "zh-TW",
        "top_page": bool(simplified["evidence"]) and simplified["evidence"][0]["pdf_page"] == 192,
        "taiwan_name": bool(simplified["evidence"]) and simplified["evidence"][0]["display_name"] == "盾葉鬼臼",
        "bounded_calls": simplified["external_embedding_calls"] == 1 and simplified["external_generation_calls"] == 0,
    })

    taiwan_name = chat.answer(
        "What Taiwan public name is used for Laurus nobilis?", args.database,
        chat.DEFAULT_ENV, 6, True,
    )
    record("taiwan_public_name_retrieval", taiwan_name, {
        "retrieval_only": taiwan_name["answer_status"] == "retrieval_only",
        "laurus_present": any(
            item["scientific_name"].casefold().startswith("laurus nobilis")
            and item["display_name"] == "月桂" for item in taiwan_name["evidence"]
        ),
        "bounded_calls": taiwan_name["external_embedding_calls"] == 1
        and taiwan_name["external_generation_calls"] == 0,
    })

    absent_fact = chat.answer(
        "What chromosome count does the book give for Podophyllum peltatum?",
        args.database, chat.DEFAULT_ENV, 4, False,
    )
    record("known_plant_absent_fact_refusal", absent_fact, {
        "insufficient": absent_fact["answer_status"] == "not_in_book_or_insufficient",
        "locale": absent_fact["response_locale"] == "en",
        "bounded_calls": absent_fact["external_embedding_calls"] == 1
        and absent_fact["external_generation_calls"] == 1,
    })

    fallback_name = chat.answer(
        "What does the book say about Piscidia erythrina?",
        args.database, chat.DEFAULT_ENV, 5, False,
    )
    record("non_taiwan_name_fallback_label", fallback_name, {
        "answerable": fallback_name["answer_status"] == "answerable_from_book",
        "scientific_name": "Piscidia" in fallback_name["answer"],
        "fallback_name": "毒魚豆" in fallback_name["answer"],
        "explicit_non_taiwan_label": "non-Taiwan" in fallback_name["answer"],
        "scope_metadata": any(
            item.get("scientific_name", "").casefold().startswith("piscidia")
            and item.get("display_name_source_scope") == "non_taiwan_traditional_fallback"
            for item in fallback_name["evidence"]
        ),
        "bounded_calls": fallback_name["external_embedding_calls"] == 1
        and fallback_name["external_generation_calls"] == 1,
    })

    broad = chat.answer(
        "便秘的時候，這本書記載哪些植物或製劑？",
        args.database, chat.DEFAULT_ENV, 6, False,
    )
    record("verified_multi_plant_constipation", broad, {
        "answerable": broad["answer_status"] == "answerable_from_book",
        "verified_mode": broad.get("answer_mode") == "verified_multi_plant",
        "verified_items": len(broad.get("verified_items", [])) >= 2,
        "podophyllum": "Podophyllum peltatum" in broad["answer"],
        "no_irrelevant_strychnos": "Strychnos" not in broad["answer"] and "馬錢" not in broad["answer"],
        "no_irrelevant_carica": "Carica" not in broad["answer"] and "番木瓜" not in broad["answer"],
        "exact_citations": bool(chat.citation_indices(broad["answer"])),
        "bounded_calls": broad["external_embedding_calls"] == 1
        and broad["external_generation_calls"] == 1,
    })

    refusal_cases = [
        ("non_kohler_drug_refusal", "阿斯匹靈是什麼藥？", "refused_non_kohler_drug"),
        ("astrology_refusal", "天秤座適合什麼植物？", "refused_outside_book_scope"),
    ]
    for case_id, question, expected in refusal_cases:
        response = chat.answer(question, args.database, chat.DEFAULT_ENV, 4, False)
        record(case_id, response, {
            "status": response["answer_status"] == expected,
            "zero_external_calls": response["external_embedding_calls"] == 0
            and response["external_generation_calls"] == 0,
            "zero_evidence": response["evidence"] == [],
        })

    total_embedding = sum(item["response"]["external_embedding_calls"] for item in results)
    total_generation = sum(item["response"]["external_generation_calls"] for item in results)
    if total_embedding != 7:
        failures.append(f"external_embedding_call_budget:{total_embedding}")
    if total_generation != 5:
        failures.append(f"external_generation_call_budget:{total_generation}")
    report = {
        "schema_version": "1.0", "tested_at": now(),
        "status": "PASS" if not failures else "FAIL",
        "database": str(args.database), "database_sha256": sha256_file(args.database),
        "cases": results, "case_count": len(results), "failures": failures,
        "external_embedding_calls": total_embedding,
        "external_generation_calls": total_generation,
        "incremental_usd": 0, "paid_fallback_used": False,
        "google_search_grounding": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.report)
    print(json.dumps(report, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
