#!/usr/bin/env python3
"""Validate one bounded embedding batch against source text and answer gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--fulltext-database", type=Path, default=LAB / "data/fulltext/kohler-pages.sqlite")
    parser.add_argument("--vector-database", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    parser.add_argument("--profile", type=Path, default=LAB / "config/embedding-profile.json")
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    for field in ("provider", "profile_id", "vector_space_id"):
        if report.get(field) != profile[field]:
            fail(f"embedding profile mismatch: {field}")
    if report.get("prompt_contract_version") != profile["prompt_contract"]["version"]:
        fail("prompt contract version mismatch")
    if report.get("api_task_type_field_used") is not False:
        fail("gemini-embedding-2 request incorrectly used API taskType")
    if report.get("incremental_usd") != 0 or report.get("paid_fallback_used") is not False:
        fail("zero-cost policy was not satisfied")
    if report.get("external_model_calls", 99) > 4:
        fail("external model call budget exceeded")

    source = sqlite3.connect(f"file:{args.fulltext_database}?mode=ro", uri=True)
    vector_db = (
        sqlite3.connect(args.vector_database)
        if args.promote
        else sqlite3.connect(f"file:{args.vector_database}?mode=ro", uri=True)
    )
    expected_dimensions = int(report["dimensions"])
    chunks_path = Path(report["portable_chunks_jsonl"])
    if not chunks_path.is_file():
        fail("portable chunk JSONL is missing")
    portable_chunks = {
        item["chunk_id"]: item
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for item in [json.loads(line)]
    }
    if len(portable_chunks) != len(report["indexed_chunks"]):
        fail("portable chunk count mismatch")
    for chunk in report["indexed_chunks"]:
        source_row = source.execute(
            "SELECT best_text, quality FROM pages WHERE source_id=? AND pdf_page=?",
            (report["source_id"], chunk["pdf_page"]),
        ).fetchone()
        if not source_row or source_row[1] != "usable":
            fail(f"source page unavailable or unusable: {chunk['pdf_page']}")
        actual_hash = hashlib.sha256(source_row[0].strip().encode("utf-8")).hexdigest()
        if actual_hash != chunk["text_sha256"]:
            fail(f"source hash drift: PDF page {chunk['pdf_page']}")
        stored = vector_db.execute(
            "SELECT source_id, pdf_page, text_sha256, embedding_json, dimensions, model, review_status, "
            "provider, vector_space_id, prompt_contract_version "
            "FROM embedding_chunks WHERE chunk_id=?",
            (chunk["chunk_id"],),
        ).fetchone()
        if not stored or stored[0] != report["source_id"] or stored[1] != chunk["pdf_page"]:
            fail(f"missing vector metadata: {chunk['chunk_id']}")
        if stored[2] != actual_hash or stored[4] != expected_dimensions or stored[5] != report["model"]:
            fail(f"vector metadata mismatch: {chunk['chunk_id']}")
        if stored[7] != report["provider"] or stored[8] != report["vector_space_id"]:
            fail(f"vector space mismatch: {chunk['chunk_id']}")
        if stored[9] != report["prompt_contract_version"]:
            fail(f"prompt contract mismatch: {chunk['chunk_id']}")
        portable = portable_chunks.get(chunk["chunk_id"])
        if not portable or portable.get("text_sha256") != actual_hash:
            fail(f"portable chunk mismatch: {chunk['chunk_id']}")
        vector = json.loads(stored[3])
        if len(vector) != expected_dimensions or not all(math.isfinite(value) for value in vector):
            fail(f"invalid vector: {chunk['chunk_id']}")
        norm = math.sqrt(sum(value * value for value in vector))
        if not 0.98 <= norm <= 1.02:
            fail(f"unexpected vector norm {norm:.6f}: {chunk['chunk_id']}")
        if stored[6] not in {"candidate", "approved"}:
            fail(f"invalid review status {stored[6]}: {chunk['chunk_id']}")
        if report.get("review_status") == "approved_bounded_batch" and stored[6] != "approved":
            fail(f"approved report does not match vector status: {chunk['chunk_id']}")

    query_verdicts = {}
    for result in report["query_results"]:
        stored_query = vector_db.execute(
            "SELECT dimensions, model, provider, vector_space_id, prompt_contract_version "
            "FROM embedding_queries WHERE query_id=?",
            (result["query_id"],),
        ).fetchone()
        if not stored_query or stored_query != (
            expected_dimensions,
            report["model"],
            report["provider"],
            report["vector_space_id"],
            report["prompt_contract_version"],
        ):
            fail(f"query vector space mismatch: {result['query_id']}")
        rankings = result.get("rankings", [])
        if len(rankings) != len(report["indexed_chunks"]):
            fail(f"incomplete rankings: {result['query_id']}")
        if result["expected_status"] == "answerable":
            if result["answer_gate"] != "supporting_book_terms_found":
                fail(f"answerable query lacked source terms: {result['query_id']}")
            expected_page = 192
            if rankings[0]["pdf_page"] != expected_page or expected_page not in result["supporting_pages"]:
                fail(f"answerable query did not rank evidence page {expected_page} first")
            query_verdicts[result["query_id"]] = "passed_answerable"
        elif result["expected_status"] == "unanswerable":
            if result["answer_gate"] != "no_supporting_book_relation":
                fail(f"unanswerable query was allowed through evidence gate: {result['query_id']}")
            query_verdicts[result["query_id"]] = "passed_refusal_gate"
        else:
            fail(f"unknown expected status: {result['expected_status']}")

    if args.promote:
        vector_db.executemany(
            "UPDATE embedding_chunks SET review_status='approved' WHERE chunk_id=?",
            [(chunk["chunk_id"],) for chunk in report["indexed_chunks"]],
        )
        vector_db.commit()
        report["review_status"] = "approved_bounded_batch"
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    source.close()
    vector_db.close()
    print(json.dumps({
        "valid": True,
        "run_id": report["run_id"],
        "chunks": len(report["indexed_chunks"]),
        "dimensions": expected_dimensions,
        "query_verdicts": query_verdicts,
        "promoted": args.promote,
        "promotion_scope": "this bounded page embedding batch only",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
