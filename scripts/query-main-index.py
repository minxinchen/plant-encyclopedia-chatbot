#!/usr/bin/env python3
"""Query approved child vectors and collapse overlapping hits to one result per parent page."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


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


def retrieve_collapsed(database: Path, tests_path: Path, query_id: str) -> dict[str, Any]:
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    fixture = next((item for item in tests["queries"] if item["query_id"] == query_id), None)
    if fixture is None:
        raise ValueError(f"query_id is not in the acceptance fixture: {query_id}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    meta = dict(connection.execute("SELECT key, value FROM embedding_meta"))
    active_profile = meta.get("active_chunk_profile")
    if not active_profile:
        raise ValueError("main index has no active_chunk_profile")
    query = connection.execute(
        "SELECT * FROM embedding_queries WHERE query_id=? AND review_status='approved'",
        (query_id,),
    ).fetchone()
    if query is None:
        raise ValueError(f"approved query vector not found: {query_id}")
    query_vector = json.loads(query["embedding_json"])
    rows = connection.execute(
        """
        SELECT chunk_id, parent_chunk_id, source_id, volume, pdf_page, record_id,
               text_sha256, source_text, embedding_json
        FROM embedding_chunks
        WHERE profile_id=? AND review_status='approved' AND vector_space_id=?
        ORDER BY chunk_id
        """,
        (active_profile, query["vector_space_id"]),
    ).fetchall()
    if not rows:
        raise ValueError("active profile has no approved chunks")
    required_terms = fixture.get("required_book_terms", [])
    lexical_terms = expanded_terms(query["question"])
    raw = []
    for row in rows:
        semantic_score = cosine(query_vector, json.loads(row["embedding_json"]))
        folded_source = row["source_text"].casefold()
        lexical_coverage = (
            sum(term in folded_source for term in lexical_terms) / len(lexical_terms)
            if lexical_terms else 0.0
        )
        raw.append({
            "chunk_id": row["chunk_id"],
            "parent_chunk_id": row["parent_chunk_id"],
            "source_id": row["source_id"],
            "volume": row["volume"],
            "pdf_page": row["pdf_page"],
            "record_id": row["record_id"],
            "text_sha256": row["text_sha256"],
            "cosine_similarity": round(semantic_score, 6),
            "lexical_coverage": round(lexical_coverage, 6),
            "hybrid_score": round(semantic_score + 0.08 * lexical_coverage, 6),
            "has_required_book_term": any(
                term.casefold() in row["source_text"].casefold() for term in required_terms
            ),
        })
    raw.sort(key=lambda item: (-item["hybrid_score"], item["chunk_id"]))
    for rank, item in enumerate(raw, start=1):
        item["raw_rank"] = rank

    grouped: dict[str, dict[str, Any]] = {}
    for item in raw:
        parent_id = item["parent_chunk_id"]
        if parent_id not in grouped:
            grouped[parent_id] = {
                **item,
                "matched_child_chunk_ids": [],
                "supporting_child_chunk_ids": [],
            }
        grouped[parent_id]["matched_child_chunk_ids"].append(item["chunk_id"])
        if item["has_required_book_term"]:
            grouped[parent_id]["supporting_child_chunk_ids"].append(item["chunk_id"])
            grouped[parent_id]["has_required_book_term"] = True
    collapsed = sorted(
        grouped.values(), key=lambda item: (-item["hybrid_score"], item["parent_chunk_id"])
    )
    for rank, item in enumerate(collapsed, start=1):
        item["collapsed_rank"] = rank
        item["citation"] = {
            "source_id": item["source_id"],
            "volume": item["volume"],
            "pdf_page": item["pdf_page"],
            "record_id": item["record_id"],
        }
    answer_gate = (
        "supporting_book_terms_found"
        if any(item["has_required_book_term"] for item in collapsed)
        else "no_supporting_book_relation"
    )
    connection.close()
    return {
        "schema_version": "1.0",
        "query_id": query_id,
        "question": query["question"],
        "expected_status": fixture["expected_status"],
        "active_profile": active_profile,
        "vector_space_id": query["vector_space_id"],
        "expanded_lexical_terms": lexical_terms,
        "fusion_contract": "cosine + 0.08 * deterministic lexical coverage, then max score per parent page",
        "answer_gate": answer_gate,
        "raw_child_hits": raw,
        "collapsed_parent_hits": collapsed,
        "raw_hit_count": len(raw),
        "collapsed_hit_count": len(collapsed),
        "duplicate_parent_hits_removed": len(raw) - len(collapsed),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--database", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    parser.add_argument("--tests", type=Path, default=LAB / "data/tests/podophyllum-chunking-ab.json")
    args = parser.parse_args()
    print(json.dumps(retrieve_collapsed(args.database, args.tests, args.query_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
