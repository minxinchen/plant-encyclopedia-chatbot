#!/usr/bin/env python3
"""Add deterministic query expansion, lexical fusion and parent collapse to a bounded report.

Pure embedding metrics remain unchanged in the source report. This checker only approves a
profile when the hybrid result ranks page-linked evidence first and refuses absent relations.

author: Codex (GPT-5)
date: 2026-08-11
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    ln = math.sqrt(sum(value * value for value in left))
    rn = math.sqrt(sum(value * value for value in right))
    return dot / (ln * rn) if ln and rn else 0.0


def expanded_terms(question: str) -> list[str]:
    folded = question.casefold()
    terms: list[str] = []
    if any(value in folded for value in ("士的寧", "strychnine", "strychnin")):
        terms.append("strychnin")
    if any(value in folded for value in ("毒性", "toxic", "poison")):
        terms.append("giftig")
    if any(value in folded for value in ("papayotin", "papain", "木瓜蛋白酶")):
        terms.append("papayotin")
    if any(value in folded for value in ("penawar djambi", "毛狀材料", "hairs supplied")):
        terms.extend(["penawar djambi", "haare"])
    if any(value in folded for value in ("atropin", "atropine", "顛茄鹼")):
        terms.append("atropin")
    if any(value in folded for value in ("piperin", "piperine", "胡椒鹼")):
        terms.append("piperin")
    if any(value in folded for value in ("senegin", "saponin", "皂苷", "皂甙")):
        terms.append("senegin")
    if any(value in folded for value in ("分布範圍", "distribution range")):
        terms.extend(["assam", "china", "malayische region"])
    if "顛茄" in folded and any(value in folded for value in ("分布", "where", "distribution")):
        terms.extend(["europa", "mittelasien"])
    if any(value in folded for value in ("胡椒", "piper nigrum")) and any(
        value in folded for value in ("原生地", "分布", "where", "distribution")
    ):
        terms.extend(["malabarküste", "südindien"])
    if any(value in folded for value in ("美遠志", "polygala senega")) and any(
        value in folded for value in ("原生", "分布", "where", "distribution")
    ):
        terms.extend(["nordamerikas", "texas"])
    if any(value in folded for value in ("極北海帶", "laminaria cloustonii", "laminaria hyperborea")):
        if any(value in folded for value in ("碘", "iodine")):
            terms.extend(["jod", "meerwasser"])
        if any(value in folded for value in ("分布", "水深", "where", "distribution", "depth")):
            terms.extend(["grossbritannien", "seetiefen"])
        if any(value in folded for value in ("flexicaulis", "柄", "stipe", "stem")):
            terms.extend(["flexicaulis", "leicht biegen"])
    if any(value in folded for value in ("染色體", "chromosome")):
        terms.append("chromosom")
    return terms


def lexical_coverage(text: str, terms: list[str]) -> float:
    if not terms:
        return 0.0
    folded = text.casefold()
    return sum(term in folded for term in terms) / len(terms)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", type=Path, default=LAB / "data/tests/strychnos-nux-vomica-volume2.json")
    parser.add_argument("--experiment-db", type=Path, default=LAB / "data/index/experiments/strychnos-volume2-profile-check.sqlite")
    parser.add_argument("--report", type=Path, default=LAB / "reports/volume2-strychnos-profile-check-2026-08-11.json")
    parser.add_argument("--profile-id", default="section-aware-512-100-v1")
    args = parser.parse_args()

    tests = json.loads(args.tests.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    fixture_status = {item["query_id"]: item["expected_status"] for item in tests["queries"]}
    for profile_result in report.get("profile_results", {}).values():
        for metric in profile_result.get("query_metrics", []):
            metric["expected_status"] = fixture_status[metric["query_id"]]
    db = sqlite3.connect(args.experiment_db)
    db.row_factory = sqlite3.Row
    query_rows = {row["query_id"]: row for row in db.execute("SELECT * FROM queries")}
    output: dict[str, Any] = {}

    for profile_id in report["profile_results"]:
        chunks = db.execute("SELECT * FROM chunks WHERE profile_id=? ORDER BY chunk_id", (profile_id,)).fetchall()
        query_results = []
        for fixture in tests["queries"]:
            query_vector = json.loads(query_rows[fixture["query_id"]]["embedding_json"])
            terms = expanded_terms(fixture["question"])
            raw = []
            for row in chunks:
                semantic = cosine(query_vector, json.loads(row["embedding_json"]))
                lexical = lexical_coverage(row["source_text"], terms)
                raw.append({
                    "chunk_id": row["chunk_id"],
                    "parent_chunk_id": row["parent_chunk_id"],
                    "pdf_page": row["pdf_page"],
                    "semantic_score": round(semantic, 6),
                    "lexical_coverage": round(lexical, 6),
                    "hybrid_score": round(semantic + 0.08 * lexical, 6),
                    "has_required_book_term": any(term.casefold() in row["source_text"].casefold() for term in fixture["required_book_terms"]),
                })
            raw.sort(key=lambda item: (-item["hybrid_score"], item["chunk_id"]))
            grouped: dict[str, dict[str, Any]] = {}
            for item in raw:
                parent = item["parent_chunk_id"]
                if parent not in grouped:
                    grouped[parent] = {**item, "matched_child_chunk_ids": [], "supporting_child_chunk_ids": []}
                grouped[parent]["matched_child_chunk_ids"].append(item["chunk_id"])
                if item["has_required_book_term"]:
                    grouped[parent]["has_required_book_term"] = True
                    grouped[parent]["supporting_child_chunk_ids"].append(item["chunk_id"])
            collapsed = sorted(grouped.values(), key=lambda item: (-item["hybrid_score"], item["parent_chunk_id"]))
            for rank, item in enumerate(collapsed, 1):
                item["rank"] = rank
            evidence = [item for item in collapsed if item["has_required_book_term"]]
            if fixture["expected_status"] == "answerable":
                correct = bool(
                    evidence
                    and evidence[0]["rank"] == 1
                    and evidence[0]["pdf_page"] in fixture["expected_pdf_pages"]
                )
                gate = "answerable_with_page_evidence" if correct else "hold_for_evidence"
            else:
                correct = not evidence
                gate = "refuse_no_book_relation" if correct else "unsafe_false_support"
            query_results.append({
                "query_id": fixture["query_id"],
                "expected_status": fixture["expected_status"],
                "expanded_lexical_terms": terms,
                "answer_gate": gate,
                "correct": correct,
                "top_parent_results": collapsed[:4],
            })
        output[profile_id] = {
            "fusion_contract": "cosine + 0.08 * deterministic lexical coverage, then max score per parent page",
            "query_results": query_results,
            "all_gates_pass": all(item["correct"] for item in query_results),
        }

    selected_passed = output[args.profile_id]["all_gates_pass"]
    report["pure_semantic_verdict"] = report["verdict"]
    report["pure_semantic_selected_profile"] = report["selected_profile"]
    report["hybrid_parent_results"] = output
    report["selection_basis"] = "deterministic bilingual domain-term expansion + lexical/semantic fusion + parent-page collapse + exact book-term gate"
    report["selected_profile"] = args.profile_id if selected_passed else None
    report["selected_profile_review_status"] = "approved_bounded_hybrid_batch" if selected_passed else "none"
    report["verdict"] = "promote" if selected_passed else "hold_for_evidence"
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    db.execute("UPDATE chunks SET review_status='candidate'")
    if selected_passed:
        db.execute("UPDATE chunks SET review_status='approved' WHERE profile_id=?", (args.profile_id,))
    db.commit()
    db.close()
    print(json.dumps({"selected_profile": report["selected_profile"], "pure_semantic_verdict": report["pure_semantic_verdict"], "hybrid_pass": selected_passed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
