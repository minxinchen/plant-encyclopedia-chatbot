#!/usr/bin/env python3
"""Offline regression tests for full-book beta chat safety gates."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def load_chat_module():
    path = LAB / "scripts/fullbook-beta-chat.py"
    spec = importlib.util.spec_from_file_location("fullbook_beta_chat", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load chat module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    chat = load_chat_module()
    errors: list[str] = []

    def expect(name: str, value: object, expected: object) -> None:
        if value != expected:
            errors.append(f"{name}: expected={expected!r} actual={value!r}")

    nonexistent = LAB / "data/index/staging/not-created-during-policy-test.sqlite"
    cases = [
        ("non-kohler-zh", "阿斯匹靈是什麼藥？", "refused_non_kohler_drug", "zh-TW"),
        ("non-kohler-en", "What is metformin?", "refused_non_kohler_drug", "en"),
        ("astrology", "天秤座適合什麼植物？", "refused_outside_book_scope", "zh-TW"),
    ]
    for case_id, question, status, response_locale in cases:
        response = chat.answer(question, nonexistent, nonexistent, 4, False)
        expect(f"{case_id}:status", response["answer_status"], status)
        expect(f"{case_id}:locale", response["response_locale"], response_locale)
        expect(f"{case_id}:embedding_calls", response["external_embedding_calls"], 0)
        expect(f"{case_id}:generation_calls", response["external_generation_calls"], 0)
        expect(f"{case_id}:evidence", response["evidence"], [])

    expect("medical-question-is-multi-zh", chat.is_multi_plant_question("我便秘可以吃什麼植物？"), True)
    expect("medical-question-is-multi-simplified", chat.is_multi_plant_question("我便秘可以吃什么植物？"), True)

    forced_english = chat.answer("阿斯匹靈是什麼藥？", nonexistent, nonexistent, 4, False, "en")
    expect("forced-english:locale", forced_english["response_locale"], "en")
    expect("forced-english:status", forced_english["answer_status"], "refused_non_kohler_drug")
    forced_traditional = chat.answer("What is aspirin?", nonexistent, nonexistent, 4, False, "zh-TW")
    expect("forced-traditional:locale", forced_traditional["response_locale"], "zh-TW")
    expect("forced-traditional:status", forced_traditional["answer_status"], "refused_non_kohler_drug")

    citation_cases = [
        ("valid-zh", "書中記載。[E1]", 1, True),
        ("valid-multi-marker", "書中並列記載。[E1, E2, E3]", 3, True),
        ("valid-author-abbreviation", "盾葉鬼臼（Podophyllum peltatum L.）見於書中 [E1]。", 1, True),
        ("uncited-second-sentence", "第一句。[E1] 第二句。", 1, False),
        ("invalid-evidence-index", "錯誤引用。[E9]", 2, False),
        ("policy-disclaimer", "書中記載。[E1] 這不是現代醫療建議。", 1, True),
    ]
    for case_id, text, evidence_count, expected in citation_cases:
        expect(f"citation:{case_id}", chat.citation_gate(text, evidence_count)[0], expected)

    multi_evidence = [
        {
            "record_id": "podophyllum", "scientific_name": "Podophyllum peltatum L.",
            "display_name": "盾葉鬼臼", "display_name_source_scope": "taiwan_taxonomic_public",
            "source_text": "It was formerly used in habitual constipation and related conditions.",
        },
        {
            "record_id": "podophyllum", "scientific_name": "Podophyllum peltatum L.",
            "display_name": "盾葉鬼臼", "display_name_source_scope": "taiwan_taxonomic_public",
            "source_text": "A second paragraph from the same botanical record.",
        },
        {
            "record_id": "carica", "scientific_name": "Carica papaya L.",
            "display_name": "番木瓜", "display_name_source_scope": "taiwan_taxonomic_public",
            "source_text": "This paragraph lists preparations but states no constipation relationship.",
        },
    ]
    valid_selection = json.dumps({
        "items": [{
            "evidence_ids": [1],
            "support_quote": "formerly used in habitual constipation and related conditions.",
        }]
    })
    expect("multi-selection:valid-count", len(chat.validate_multi_selection(
        valid_selection, multi_evidence
    )), 1)
    hyphenated_evidence = [dict(
        multi_evidence[0], source_text="Podophyllin ist bei habitueller Verstopfung brauch-\nbar."
    )]
    hyphenated_selection = json.dumps({
        "items": [{
            "evidence_ids": [1],
            "support_quote": "Podophyllin ist bei habitueller Verstopfung brauchbar.",
        }]
    })
    expect("multi-selection:pdf-linewrap-hyphen", len(chat.validate_multi_selection(
        hyphenated_selection, hyphenated_evidence
    )), 1)
    fabricated_selection = json.dumps({
        "items": [{"evidence_ids": [1], "support_quote": "This fabricated cure is not in the source."}]
    })
    expect("multi-selection:fabricated-quote", chat.validate_multi_selection(
        fabricated_selection, multi_evidence
    ), [])
    cross_record_selection = json.dumps({
        "items": [{
            "evidence_ids": [1, 3],
            "support_quote": "formerly used in habitual constipation and related conditions.",
        }]
    })
    expect("multi-selection:cross-record", chat.validate_multi_selection(
        cross_record_selection, multi_evidence
    ), [])
    invalid_index_selection = json.dumps({
        "items": [{"evidence_ids": [9], "support_quote": "formerly used in habitual constipation."}]
    })
    expect("multi-selection:invalid-index", chat.validate_multi_selection(
        invalid_index_selection, multi_evidence
    ), [])
    expect("multi-question:zh", chat.is_multi_plant_question(
        "便秘時，本書記載哪些植物或製劑？"
    ), True)
    expect("multi-question:single", chat.is_multi_plant_question(
        "盾葉鬼臼與便秘的關係？"
    ), False)

    evidence = [
        {"display_name": "盾葉鬼臼", "scientific_name": "Podophyllum peltatum L."},
        {"display_name": "顛茄", "scientific_name": "Atropa belladonna L."},
    ]
    entity_cases = [
        ("allowed-taiwan-pair", "盾葉鬼臼（Podophyllum peltatum L.）[E1]", True),
        ("allowed-author-omitted", "盾葉鬼臼（Podophyllum peltatum）[E1]", True),
        ("forbidden-wrong-species", "盾葉鬼臼（Podophyllum emodi）[E1]", False),
        ("forbidden-invented-gloss", "顛茄（Extractum Hyoscyami）[E1]", False),
        ("preserved-latin-preparation", "製劑 Extractum Hyoscyami [E1]", True),
    ]
    for case_id, text, expected in entity_cases:
        expect(f"entity:{case_id}", chat.entity_pair_gate(text, evidence)[0], expected)

    display_name_cases = [
        ("allowed-retrieved-name", "盾葉鬼臼（Podophyllum peltatum）[E1]", True),
        ("forbidden-unretrieved-name", "月桂也有相同記載。[E1]", False),
    ]
    for case_id, text, expected in display_name_cases:
        expect(
            f"display-name:{case_id}",
            chat.display_name_gate(text, evidence[:1], ["盾葉鬼臼", "顛茄", "月桂"])[0],
            expected,
        )

    script = (LAB / "scripts/fullbook-beta-chat.py").read_text(encoding="utf-8")
    expect("no-google-search-tool", "google_search" in script, False)
    expect("stable-generation-model", "gemini-2.5-flash-lite" in script, True)
    chinese_fts = chat.lexical_query("請問盾葉鬼臼在書中如何記載")
    expect("chinese-name-fts", '"盾葉鬼臼"' in chinese_fts, True)
    english_fts = chat.lexical_query("What does the book say about Podophyllum peltatum?")
    expect("english-name-fts", '"Podophyllum"' in english_fts and '"peltatum"' in english_fts, True)
    result = {"status": "PASS" if not errors else "FAIL", "cases": 31, "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
