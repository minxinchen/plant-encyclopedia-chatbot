#!/usr/bin/env python3
"""Deterministically validate the Podophyllum chunking A/B experiment.

author: Codex (GPT-5)
date: 2026-08-11
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
UNIT_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=LAB / "reports/chunking-ab-podophyllum-2026-08-11.json")
    parser.add_argument("--tests", type=Path, default=LAB / "data/tests/podophyllum-chunking-ab.json")
    parser.add_argument("--page-chunks", type=Path, default=LAB / "data/chunks/podophyllum-peltatum.jsonl")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    tests = json.loads(args.tests.read_text(encoding="utf-8"))
    parents = {item["chunk_id"]: item for line in args.page_chunks.read_text(encoding="utf-8").splitlines() if line for item in [json.loads(line)]}
    if report["external_model_calls"] > 4 or report["incremental_usd"] != 0 or report["paid_fallback_used"]:
        fail("budget or paid-fallback gate failed")
    if any("libra" in query["query_id"].casefold() for query in tests["queries"]):
        fail("historical Libra probe entered formal acceptance")

    db = sqlite3.connect(f"file:{report['experiment_database']}?mode=ro", uri=True)
    for profile in tests["profiles"]:
        profile_id = profile["profile_id"]
        result = report["profile_results"].get(profile_id)
        if not result:
            fail(f"missing profile result: {profile_id}")
        rows = db.execute(
            "SELECT chunk_id,parent_chunk_id,source_id,pdf_page,text_sha256,source_text,embedding_json,vector_checkpoint,vector_space_id,review_status "
            "FROM chunks WHERE profile_id=? ORDER BY pdf_page,chunk_id",
            (profile_id,),
        ).fetchall()
        if len(rows) != result["chunk_count"]:
            fail(f"chunk count mismatch: {profile_id}")
        for row in rows:
            parent = parents.get(row[1])
            if not parent or row[2] != parent["source_id"] or row[3] != parent["pdf_page"]:
                fail(f"parent locator mismatch: {row[0]}")
            if row[5] not in parent["source_text"]:
                fail(f"child is not exact parent substring: {row[0]}")
            if hashlib.sha256(row[5].encode("utf-8")).hexdigest() != row[4]:
                fail(f"child hash mismatch: {row[0]}")
            vector = json.loads(row[6])
            norm = math.sqrt(sum(value * value for value in vector))
            if len(vector) != report["dimensions"] or not 0.98 <= norm <= 1.02:
                fail(f"invalid vector: {row[0]}")
            if row[8] != report["vector_space_id"]:
                fail(f"vector space mismatch: {row[0]}")
            expected_review = "approved" if profile_id == report["selected_profile"] else "candidate"
            if row[9] != expected_review:
                fail(f"review status mismatch: {row[0]}")
            if profile_id == "section-aware-1024-200-v1" and row[7] != "reused-page-baseline":
                fail("1024/200 should collapse to and reuse page baseline on this bounded sample")
        if profile_id != "page-baseline":
            path = Path(report["candidate_chunk_files"][profile_id])
            candidates = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
            if len(candidates) != len(rows):
                fail(f"portable candidate count mismatch: {profile_id}")
            target = int(profile["target_token_units"])
            overlap = int(profile["overlap_token_units"])
            by_page: dict[int, list[dict]] = {}
            for chunk in candidates:
                if len(UNIT_RE.findall(chunk["source_text"])) > target:
                    fail(f"target token-unit limit exceeded: {chunk['chunk_id']}")
                by_page.setdefault(int(chunk["pdf_page"]), []).append(chunk)
            for chunks in by_page.values():
                chunks.sort(key=lambda item: item["token_unit_start"])
                for left, right in zip(chunks, chunks[1:]):
                    actual_overlap = left["token_unit_end"] - right["token_unit_start"]
                    if actual_overlap != overlap:
                        fail(f"overlap mismatch: {right['chunk_id']} got {actual_overlap}")

        for metric in result["query_metrics"]:
            if metric["expected_status"] == "answerable":
                if metric["answer_gate"] != "supporting_book_terms_found" or metric["first_relevant_rank"] != 1:
                    fail(f"answer evidence gate failed: {profile_id}/{metric['query_id']}")
                if metric["top_results"][0]["pdf_page"] != 192:
                    fail(f"wrong evidence page: {profile_id}/{metric['query_id']}")
            else:
                if metric["answer_gate"] != "no_supporting_book_relation" or not metric["refusal_correct"]:
                    fail(f"refusal evidence gate failed: {profile_id}/{metric['query_id']}")

    passing = []
    for profile_id, result in report["profile_results"].items():
        if result["mean_mrr"] == result["answer_recall_at_1"] == result["refusal_precision"] == result["citation_completeness"] == 1.0:
            passing.append((profile_id, result))
    expected = max(
        passing,
        key=lambda pair: (
            pair[1]["mean_mrr"], pair[1]["answer_recall_at_1"], pair[1]["mean_evidence_margin"], -pair[1]["chunk_count"]
        ),
    )[0]
    if report["selected_profile"] != expected or report["verdict"] != "promote":
        fail("selected profile does not follow declared selection rule")
    if report.get("selected_profile_review_status") != "approved_bounded_batch":
        fail("selected profile was not promoted within the bounded experiment index")

    source_manifest = json.loads((LAB / "data/source-manifest.json").read_text(encoding="utf-8"))
    source = next(item for item in source_manifest["files"] if item["source_id"] == report["source_id"])
    source_path = Path(source["path"])
    if not source_path.is_file() or source_path.stat().st_size != source["bytes"]:
        fail("source PDF is missing or changed")
    db.close()
    print(json.dumps({
        "valid": True,
        "selected_profile": report["selected_profile"],
        "profiles": len(report["profile_results"]),
        "queries": len(tests["queries"]),
        "external_model_calls": report["external_model_calls"],
        "incremental_usd": report["incremental_usd"],
        "source_pdf_unchanged": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
