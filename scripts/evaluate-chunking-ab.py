#!/usr/bin/env python3
"""Evaluate page, 512/100 and 1024/200 chunks on one approved Podophyllum sample.

author: Codex (GPT-5)
date: 2026-08-11
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")
UNIT_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
HEADING_RE = re.compile(
    r"(?im)^\s*(?:Beschreibung|Anatomisches|Vorkommen|Bliithezeit|Name|Praparate|"
    r"Bestandtheile|Anwendung|Litteratur|Tafelbesehreibung)\b"
)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    ln = math.sqrt(sum(value * value for value in left))
    rn = math.sqrt(sum(value * value for value in right))
    return dot / (ln * rn) if ln and rn else 0.0


def units(text: str) -> list[re.Match[str]]:
    return list(UNIT_RE.finditer(text))


def heading_boundaries(text: str, token_matches: list[re.Match[str]]) -> list[int]:
    starts = [match.start() for match in token_matches]
    return sorted({bisect.bisect_left(starts, match.start()) for match in HEADING_RE.finditer(text)})


def make_child_chunks(parent: dict[str, Any], profile_id: str, target: int, overlap: int) -> list[dict[str, Any]]:
    text = parent["source_text"]
    token_matches = units(text)
    if not token_matches:
        raise ValueError(f"empty token stream: PDF page {parent['pdf_page']}")
    boundaries = heading_boundaries(text, token_matches)
    output: list[dict[str, Any]] = []
    start = 0
    sequence = 0
    while start < len(token_matches):
        desired = min(start + target, len(token_matches))
        end = desired
        if desired < len(token_matches):
            minimum = start + max(64, target // 2)
            preferred = [boundary for boundary in boundaries if minimum <= boundary <= desired]
            if preferred:
                end = preferred[-1]
        if end <= start:
            end = desired
        char_start = token_matches[start].start()
        char_end = token_matches[end - 1].end()
        child_text = text[char_start:char_end]
        chunk_id = (
            f"{parent['source_id']}:p{parent['pdf_page']}:{parent['record_id']}:"
            f"{profile_id}:c{sequence:02d}"
        )
        output.append({
            "schema_version": "1.0",
            "profile_id": profile_id,
            "chunk_id": chunk_id,
            "parent_chunk_id": parent["chunk_id"],
            "source_id": parent["source_id"],
            "source_filename": parent["source_filename"],
            "volume": parent["volume"],
            "pdf_page": parent["pdf_page"],
            "record_id": parent["record_id"],
            "scientific_name": parent["scientific_name"],
            "display_name": parent["display_name"],
            "chunk_kind": "section-aware-child" if len(token_matches) > target else "page-within-target",
            "target_token_units": target,
            "overlap_token_units": overlap,
            "token_unit_start": start,
            "token_unit_end": end,
            "token_unit_count": end - start,
            "char_start": char_start,
            "char_end": char_end,
            "parent_text_sha256": parent["text_sha256"],
            "text_sha256": sha256(child_text),
            "source_text": child_text,
        })
        if end == len(token_matches):
            break
        next_start = max(start + 1, end - overlap)
        start = next_start
        sequence += 1
    return output


def document_prompt(profile: dict[str, Any], chunk: dict[str, Any]) -> str:
    content = (
        f"Scientific name: {chunk['scientific_name']}\n"
        f"Taiwan display name: {chunk['display_name'] or 'unresolved'}\n"
        f"Book source: {chunk['source_id']}, PDF page {chunk['pdf_page']}\n\n"
        f"{chunk['source_text']}"
    )
    title = f"{chunk['scientific_name']} - {chunk['source_id']} PDF page {chunk['pdf_page']}"
    return profile["prompt_contract"]["document"].format(title=title, content=content)


def batch_embed(api_key: str, model: str, dimensions: int, texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
    requests = [{
        "model": f"models/{model}",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": dimensions,
    } for text in texts]
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents",
        data=json.dumps({"requests": requests}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"Gemini batchEmbedContents failed HTTP {error.code}: {detail}") from error
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise RuntimeError("Gemini batch response count mismatch")
    vectors = []
    for item in embeddings:
        values = item.get("values")
        if not isinstance(values, list) or len(values) != dimensions:
            raise RuntimeError("Gemini batch response dimensions mismatch")
        vector = [float(value) for value in values]
        if not all(math.isfinite(value) for value in vector):
            raise RuntimeError("Gemini batch response contains non-finite values")
        vectors.append(vector)
    return vectors, payload.get("usageMetadata", {})


def create_experiment_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
          profile_id TEXT NOT NULL,
          chunk_id TEXT NOT NULL,
          parent_chunk_id TEXT NOT NULL,
          source_id TEXT NOT NULL,
          pdf_page INTEGER NOT NULL,
          text_sha256 TEXT NOT NULL,
          source_text TEXT NOT NULL,
          embedding_json TEXT NOT NULL,
          vector_checkpoint TEXT NOT NULL,
          vector_space_id TEXT NOT NULL,
          review_status TEXT NOT NULL DEFAULT 'candidate',
          PRIMARY KEY (profile_id, chunk_id)
        );
        CREATE TABLE IF NOT EXISTS queries (
          query_id TEXT PRIMARY KEY,
          question TEXT NOT NULL,
          expected_status TEXT NOT NULL,
          embedding_json TEXT NOT NULL,
          vector_checkpoint TEXT NOT NULL,
          vector_space_id TEXT NOT NULL
        );
        """
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
    if "review_status" not in columns:
        db.execute("ALTER TABLE chunks ADD COLUMN review_status TEXT NOT NULL DEFAULT 'candidate'")


def metric_for_query(chunks: list[dict[str, Any]], vectors: dict[str, list[float]], query: dict[str, Any],
                     query_vector: list[float]) -> dict[str, Any]:
    rankings = sorted(
        [{
            "rank": 0,
            "chunk_id": chunk["chunk_id"],
            "pdf_page": chunk["pdf_page"],
            "cosine_similarity": round(cosine(query_vector, vectors[chunk["chunk_id"]]), 6),
            "has_required_book_term": any(
                term.casefold() in chunk["source_text"].casefold() for term in query["required_book_terms"]
            ),
        } for chunk in chunks],
        key=lambda item: item["cosine_similarity"],
        reverse=True,
    )
    for index, item in enumerate(rankings, start=1):
        item["rank"] = index
    relevant = [item for item in rankings if item["has_required_book_term"]]
    if query["expected_status"] == "answerable":
        first_rank = relevant[0]["rank"] if relevant else None
        top_relevant = max((item["cosine_similarity"] for item in relevant), default=-1.0)
        top_nonrelevant = max((item["cosine_similarity"] for item in rankings if not item["has_required_book_term"]), default=-1.0)
        gate = "supporting_book_terms_found" if relevant else "no_supporting_book_relation"
        return {
            "query_id": query["query_id"],
            "expected_status": "answerable",
            "answer_gate": gate,
            "relevant_chunk_count": len(relevant),
            "first_relevant_rank": first_rank,
            "mrr": round(1 / first_rank, 6) if first_rank else 0.0,
            "recall_at_1": bool(first_rank == 1),
            "recall_at_3": bool(first_rank and first_rank <= 3),
            "evidence_margin": round(top_relevant - top_nonrelevant, 6),
            "top_results": rankings[:5],
        }
    gate = "supporting_book_terms_found" if relevant else "no_supporting_book_relation"
    return {
        "query_id": query["query_id"],
        "expected_status": query["expected_status"],
        "answer_gate": gate,
        "refusal_correct": not relevant,
        "relevant_chunk_count": len(relevant),
        "top_results": rankings[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", type=Path, default=LAB / "data/tests/podophyllum-chunking-ab.json")
    parser.add_argument("--profile", type=Path, default=LAB / "config/embedding-profile.json")
    parser.add_argument("--page-chunks", type=Path, default=LAB / "data/chunks/podophyllum-peltatum.jsonl")
    parser.add_argument("--baseline-db", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    parser.add_argument("--experiment-db", type=Path, default=LAB / "data/index/experiments/podophyllum-chunking-ab.sqlite")
    parser.add_argument("--output-dir", type=Path, default=LAB / "data/chunks/experiments")
    parser.add_argument("--report", type=Path, default=LAB / "reports/chunking-ab-podophyllum-2026-08-11.json")
    args = parser.parse_args()

    tests = json.loads(args.tests.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    parents = [json.loads(line) for line in args.page_chunks.read_text(encoding="utf-8").splitlines() if line.strip()]
    if [item["pdf_page"] for item in parents] != [191, 192]:
        raise SystemExit("bounded experiment requires exactly Podophyllum PDF pages 191 and 192")
    if any(not Path(LAB / "data/fulltext/kohler-pages.sqlite").exists() for _ in [0]):
        raise SystemExit("fulltext source database unavailable")

    candidates: dict[str, list[dict[str, Any]]] = {"page-baseline": parents}
    for item in tests["profiles"]:
        profile_id = item["profile_id"]
        if profile_id == "page-baseline":
            continue
        chunks = []
        for parent in parents:
            chunks.extend(make_child_chunks(
                parent, profile_id, int(item["target_token_units"]), int(item["overlap_token_units"])
            ))
        candidates[profile_id] = chunks
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = args.output_dir / f"podophyllum-peltatum-{profile_id}.jsonl"
        output_path.write_text(
            "".join(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n" for chunk in chunks),
            encoding="utf-8",
        )

    baseline = sqlite3.connect(f"file:{args.baseline_db}?mode=ro", uri=True)
    baseline.row_factory = sqlite3.Row
    baseline_vectors: dict[int, list[float]] = {}
    for row in baseline.execute(
        "SELECT pdf_page, source_text, embedding_json, review_status, vector_space_id "
        "FROM embedding_chunks WHERE record_id='podophyllum-peltatum' ORDER BY pdf_page"
    ):
        if row["review_status"] != "approved" or row["vector_space_id"] != profile["vector_space_id"]:
            raise SystemExit("page baseline is not approved in the selected vector space")
        baseline_vectors[int(row["pdf_page"])] = json.loads(row["embedding_json"])
    if sorted(baseline_vectors) != [191, 192]:
        raise SystemExit("approved baseline vectors are incomplete")

    query_vectors: dict[str, list[float]] = {}
    query_checkpoints: dict[str, str] = {}
    pending_items: list[tuple[str, str, str]] = []
    for query in tests["queries"]:
        existing = baseline.execute(
            "SELECT question, embedding_json, vector_space_id FROM embedding_queries WHERE query_id=?",
            (query["query_id"],),
        ).fetchone()
        if existing and existing["question"] == query["question"] and existing["vector_space_id"] == profile["vector_space_id"]:
            query_vectors[query["query_id"]] = json.loads(existing["embedding_json"])
            query_checkpoints[query["query_id"]] = "reused-approved-query"
        else:
            query_text = profile["prompt_contract"]["query"].format(content=query["question"])
            pending_items.append(("query", query["query_id"], query_text))

    profile_vectors: dict[str, dict[str, list[float]]] = {}
    vector_checkpoints: dict[str, dict[str, str]] = {}
    for profile_id, chunks in candidates.items():
        profile_vectors[profile_id] = {}
        vector_checkpoints[profile_id] = {}
        for chunk in chunks:
            page = int(chunk["pdf_page"])
            if profile_id == "page-baseline" or chunk["source_text"] == next(
                parent["source_text"] for parent in parents if int(parent["pdf_page"]) == page
            ):
                profile_vectors[profile_id][chunk["chunk_id"]] = baseline_vectors[page]
                vector_checkpoints[profile_id][chunk["chunk_id"]] = "reused-page-baseline"
            else:
                pending_items.append(("chunk", f"{profile_id}\t{chunk['chunk_id']}", document_prompt(profile, chunk)))

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not loaded")
    if not pending_items:
        raise SystemExit("experiment unexpectedly has no new vectors")
    vectors, usage = batch_embed(api_key, profile["model"], int(profile["dimensions"]), [item[2] for item in pending_items])
    for (kind, identity, _), vector in zip(pending_items, vectors):
        if kind == "query":
            query_vectors[identity] = vector
            query_checkpoints[identity] = "embedded-synchronous-batch"
        else:
            profile_id, chunk_id = identity.split("\t", 1)
            profile_vectors[profile_id][chunk_id] = vector
            vector_checkpoints[profile_id][chunk_id] = "embedded-synchronous-batch"

    args.experiment_db.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.experiment_db)
    create_experiment_schema(db)
    db.execute("DELETE FROM chunks")
    db.execute("DELETE FROM queries")
    for profile_id, chunks in candidates.items():
        for chunk in chunks:
            db.execute(
                """INSERT INTO chunks (
                  profile_id, chunk_id, parent_chunk_id, source_id, pdf_page, text_sha256,
                  source_text, embedding_json, vector_checkpoint, vector_space_id, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate')""",
                (
                    profile_id, chunk["chunk_id"], chunk.get("parent_chunk_id", chunk["chunk_id"]),
                    chunk["source_id"], chunk["pdf_page"], chunk["text_sha256"], chunk["source_text"],
                    json.dumps(profile_vectors[profile_id][chunk["chunk_id"]], separators=(",", ":")),
                    vector_checkpoints[profile_id][chunk["chunk_id"]], profile["vector_space_id"],
                ),
            )
    for query in tests["queries"]:
        db.execute(
            "INSERT INTO queries VALUES (?, ?, ?, ?, ?, ?)",
            (
                query["query_id"], query["question"], query["expected_status"],
                json.dumps(query_vectors[query["query_id"]], separators=(",", ":")),
                query_checkpoints[query["query_id"]], profile["vector_space_id"],
            ),
        )
    db.commit()
    db.close()
    baseline.close()

    results: dict[str, Any] = {}
    for profile_id, chunks in candidates.items():
        query_metrics = [
            metric_for_query(chunks, profile_vectors[profile_id], query, query_vectors[query["query_id"]])
            for query in tests["queries"]
        ]
        answers = [item for item in query_metrics if item["expected_status"] == "answerable"]
        refusals = [item for item in query_metrics if item["expected_status"] == "unanswerable"]
        results[profile_id] = {
            "chunk_count": len(chunks),
            "page_chunk_counts": {
                str(page): sum(1 for chunk in chunks if int(chunk["pdf_page"]) == page) for page in (191, 192)
            },
            "mean_mrr": round(sum(item["mrr"] for item in answers) / len(answers), 6),
            "answer_recall_at_1": round(sum(item["recall_at_1"] for item in answers) / len(answers), 6),
            "answer_recall_at_3": round(sum(item["recall_at_3"] for item in answers) / len(answers), 6),
            "mean_evidence_margin": round(sum(item["evidence_margin"] for item in answers) / len(answers), 6),
            "refusal_precision": round(sum(item["refusal_correct"] for item in refusals) / len(refusals), 6),
            "citation_completeness": 1.0 if all(chunk.get("source_id") and chunk.get("pdf_page") for chunk in chunks) else 0.0,
            "query_metrics": query_metrics,
        }

    passing = [
        (profile_id, item) for profile_id, item in results.items()
        if item["mean_mrr"] == 1.0 and item["answer_recall_at_1"] == 1.0
        and item["refusal_precision"] == 1.0 and item["citation_completeness"] == 1.0
    ]
    if not passing:
        selected = None
        verdict = "hold_for_evidence"
    else:
        selected = max(
            passing,
            key=lambda pair: (
                pair[1]["mean_mrr"], pair[1]["answer_recall_at_1"], pair[1]["mean_evidence_margin"],
                -pair[1]["chunk_count"],
            ),
        )[0]
        verdict = "promote"

    if selected:
        promotion_db = sqlite3.connect(args.experiment_db)
        promotion_db.execute("UPDATE chunks SET review_status='candidate'")
        promotion_db.execute(
            "UPDATE chunks SET review_status='approved' WHERE profile_id=?",
            (selected,),
        )
        promotion_db.commit()
        promotion_db.close()

    embedded_at = datetime.now(TZ).isoformat(timespec="seconds")
    report = {
        "schema_version": "1.0",
        "run_id": tests["run_id"],
        "batch_id": tests["batch_id"],
        "embedded_at": embedded_at,
        "source_id": "kohler-volume-1",
        "record_id": "podophyllum-peltatum",
        "pdf_pages": [191, 192],
        "model": profile["model"],
        "dimensions": profile["dimensions"],
        "vector_space_id": profile["vector_space_id"],
        "token_unit_contract": tests["token_unit_contract"],
        "external_model_calls": 1,
        "batch_embed_items": len(pending_items),
        "batch_usage_metadata": usage,
        "incremental_usd": 0,
        "paid_fallback_used": False,
        "official_contract": "https://ai.google.dev/api/embeddings#method_models.batchEmbedContents",
        "candidate_chunk_files": {
            profile_id: str((args.output_dir / f"podophyllum-peltatum-{profile_id}.jsonl").resolve())
            for profile_id in candidates if profile_id != "page-baseline"
        },
        "experiment_database": str(args.experiment_db.resolve()),
        "query_checkpoints": query_checkpoints,
        "profile_results": results,
        "selected_profile": selected,
        "selected_profile_review_status": "approved_bounded_batch" if selected else "none",
        "verdict": verdict,
        "promotion_scope": "Podophyllum PDF pages 191-192 chunking profile only; no full-volume expansion",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "complete",
        "selected_profile": selected,
        "external_model_calls": 1,
        "batch_embed_items": len(pending_items),
        "report": str(args.report),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
