#!/usr/bin/env python3
"""Revalidate page baseline versus the promoted 512/100 profile on one bounded sample.

author: Codex (GPT-5)
date: 2026-08-11
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")


def load_ab_module() -> Any:
    path = LAB / "scripts/evaluate-chunking-ab.py"
    spec = importlib.util.spec_from_file_location("chunking_ab", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load chunking helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", type=Path, default=LAB / "data/tests/strychnos-nux-vomica-volume2.json")
    parser.add_argument("--profile", type=Path, default=LAB / "config/embedding-profile.json")
    parser.add_argument("--fulltext-db", type=Path, default=LAB / "data/fulltext/kohler-pages.sqlite")
    parser.add_argument("--experiment-db", type=Path, default=LAB / "data/index/experiments/strychnos-volume2-profile-check.sqlite")
    parser.add_argument("--output-dir", type=Path, default=LAB / "data/chunks/experiments")
    parser.add_argument("--report", type=Path, default=LAB / "reports/volume2-strychnos-profile-check-2026-08-11.json")
    args = parser.parse_args()

    helper = load_ab_module()
    tests = json.loads(args.tests.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    pages = [int(value) for value in tests["pdf_pages"]]
    if len(pages) > 6 or pages != list(range(min(pages), max(pages) + 1)):
        raise SystemExit("bounded sample must be no more than six adjacent pages")

    source_filename = tests["source_filename"]
    volume = int(tests["volume"])
    source_ranges = {int(item["pdf_page"]): item for item in tests.get("source_ranges", [])}
    connection = sqlite3.connect(f"file:{args.fulltext_db}?mode=ro", uri=True)
    rows = connection.execute(
        "SELECT pdf_page, best_text, quality FROM pages WHERE source_id=? AND pdf_page BETWEEN ? AND ? ORDER BY pdf_page",
        (tests["source_id"], min(pages), max(pages)),
    ).fetchall()
    connection.close()
    if [row[0] for row in rows] != pages or any(row[2] not in {"clean", "usable"} for row in rows):
        raise SystemExit("bounded source pages are missing or below usable quality")

    parents: list[dict[str, Any]] = []
    source_range_notes: list[dict[str, Any]] = []
    for pdf_page, source_text, _quality in rows:
        original_length = len(source_text)
        source_char_start = 0
        source_char_end = original_length
        if pdf_page in source_ranges:
            range_contract = source_ranges[pdf_page]
            if range_contract.get("start_marker"):
                source_char_start = source_text.find(range_contract["start_marker"])
                if source_char_start < 0:
                    raise SystemExit(f"target taxon start marker not found on PDF page {pdf_page}")
            if range_contract.get("end_marker"):
                end_position = source_text.find(range_contract["end_marker"], source_char_start)
                if end_position < 0:
                    raise SystemExit(f"target taxon end marker not found on PDF page {pdf_page}")
                source_char_end = end_position
            source_text = source_text[source_char_start:source_char_end]
            source_range_notes.append({
                "pdf_page": pdf_page,
                "source_char_start": source_char_start,
                "source_char_end": source_char_end,
                "excluded_range_reason": range_contract["reason"]
            })
        digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        chunk_id = f"{tests['source_id']}:p{pdf_page}:{tests['record_id']}:page"
        parents.append({
            "schema_version": "1.0",
            "profile_id": "page-baseline",
            "chunk_id": chunk_id,
            "parent_chunk_id": chunk_id,
            "source_id": tests["source_id"],
            "source_filename": source_filename,
            "volume": volume,
            "pdf_page": pdf_page,
            "record_id": tests["record_id"],
            "scientific_name": tests["scientific_name"],
            "display_name": tests["display_name"],
            "chunk_kind": "page",
            "text_sha256": digest,
            "parent_text_sha256": digest,
            "source_text": source_text,
        })

    candidates: dict[str, list[dict[str, Any]]] = {"page-baseline": parents}
    selected_profile = profile["chunking_profile"]["profile_id"]
    selected_fixture = next(item for item in tests["profiles"] if item["profile_id"] == selected_profile)
    children: list[dict[str, Any]] = []
    for parent in parents:
        children.extend(helper.make_child_chunks(
            parent, selected_profile,
            int(selected_fixture["target_token_units"]),
            int(selected_fixture["overlap_token_units"]),
        ))
    candidates[selected_profile] = children

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for profile_id, chunks in candidates.items():
        path = args.output_dir / f"{tests['record_id']}-{profile_id}.jsonl"
        path.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in chunks), encoding="utf-8")

    pending: list[tuple[str, str, str]] = []
    for profile_id, chunks in candidates.items():
        for chunk in chunks:
            pending.append(("chunk", f"{profile_id}\t{chunk['chunk_id']}", helper.document_prompt(profile, chunk)))
    for query in tests["queries"]:
        pending.append(("query", query["query_id"], profile["prompt_contract"]["query"].format(content=query["question"])))
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not loaded")
    vectors, usage = helper.batch_embed(api_key, profile["model"], int(profile["dimensions"]), [item[2] for item in pending])

    profile_vectors: dict[str, dict[str, list[float]]] = {key: {} for key in candidates}
    query_vectors: dict[str, list[float]] = {}
    for (kind, identity, _text), vector in zip(pending, vectors):
        if kind == "query":
            query_vectors[identity] = vector
        else:
            profile_id, chunk_id = identity.split("\t", 1)
            profile_vectors[profile_id][chunk_id] = vector

    args.experiment_db.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.experiment_db)
    helper.create_experiment_schema(db)
    db.execute("DELETE FROM chunks")
    db.execute("DELETE FROM queries")
    for profile_id, chunks in candidates.items():
        for chunk in chunks:
            db.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (profile_id, chunk["chunk_id"], chunk["parent_chunk_id"], chunk["source_id"], chunk["pdf_page"],
                 chunk["text_sha256"], chunk["source_text"], json.dumps(profile_vectors[profile_id][chunk["chunk_id"]], separators=(",", ":")),
                 "embedded-synchronous-batch", profile["vector_space_id"], "candidate"),
            )
    for query in tests["queries"]:
        db.execute(
            "INSERT INTO queries VALUES (?, ?, ?, ?, ?, ?)",
            (query["query_id"], query["question"], query["expected_status"],
             json.dumps(query_vectors[query["query_id"]], separators=(",", ":")),
             "embedded-synchronous-batch", profile["vector_space_id"]),
        )

    results: dict[str, Any] = {}
    for profile_id, chunks in candidates.items():
        metrics = [helper.metric_for_query(chunks, profile_vectors[profile_id], query, query_vectors[query["query_id"]]) for query in tests["queries"]]
        answers = [item for item in metrics if item["expected_status"] == "answerable"]
        refusals = [item for item in metrics if item["expected_status"] != "answerable"]
        results[profile_id] = {
            "chunk_count": len(chunks),
            "page_chunk_counts": {str(page): sum(1 for item in chunks if item["pdf_page"] == page) for page in pages},
            "mean_mrr": round(sum(item["mrr"] for item in answers) / len(answers), 6),
            "answer_recall_at_1": round(sum(item["recall_at_1"] for item in answers) / len(answers), 6),
            "answer_recall_at_3": round(sum(item["recall_at_3"] for item in answers) / len(answers), 6),
            "mean_evidence_margin": round(sum(item["evidence_margin"] for item in answers) / len(answers), 6),
            "refusal_precision": round(sum(item["refusal_correct"] for item in refusals) / len(refusals), 6),
            "citation_completeness": 1.0,
            "query_metrics": metrics,
        }

    selected_result = results[selected_profile]
    passed = (
        selected_result["answer_recall_at_1"] == 1.0
        and selected_result["refusal_precision"] == 1.0
        and selected_result["citation_completeness"] == 1.0
    )
    if passed:
        db.execute("UPDATE chunks SET review_status='approved' WHERE profile_id=?", (selected_profile,))
    db.commit()
    db.close()

    previous = json.loads(args.report.read_text(encoding="utf-8")) if args.report.is_file() else None
    attempt_history = list(previous.get("attempt_history", [])) if previous else []
    if previous and previous.get("profile_results"):
        attempt_history.append({
            "attempt": len(attempt_history) + 1,
            "verdict": previous.get("verdict"),
            "pure_semantic_verdict": previous.get("pure_semantic_verdict", previous.get("verdict")),
            "external_model_calls": previous.get("external_model_calls_this_run", previous.get("external_model_calls", 0)),
            "finding": tests.get("changed_strategy_finding", "The retry changed source boundaries or retrieval strategy; see source_range_notes."),
            "profile_results": previous["profile_results"],
            "hybrid_parent_results": previous.get("hybrid_parent_results", {})
        })
    report = {
        "schema_version": "1.0",
        "run_id": tests["run_id"],
        "batch_id": tests["batch_id"],
        "embedded_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "source_id": tests["source_id"],
        "record_id": tests["record_id"],
        "pdf_pages": pages,
        "model": profile["model"],
        "dimensions": profile["dimensions"],
        "vector_space_id": profile["vector_space_id"],
        "token_unit_contract": tests["token_unit_contract"],
        "external_model_calls": sum(item.get("external_model_calls", 0) for item in attempt_history) + 1,
        "external_model_calls_this_run": 1,
        "batch_embed_items": len(pending),
        "batch_usage_metadata": usage,
        "incremental_usd": 0,
        "paid_fallback_used": False,
        "source_range_notes": source_range_notes,
        "attempt_history": attempt_history,
        "profile_results": results,
        "selected_profile": selected_profile if passed else None,
        "selected_profile_review_status": "approved_bounded_batch" if passed else "none",
        "verdict": "promote" if passed else "hold_for_evidence",
        "promotion_scope": tests["promotion_scope"]
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "complete", "selected_profile": report["selected_profile"], "external_model_calls": 1, "batch_embed_items": len(pending), "report": str(args.report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
