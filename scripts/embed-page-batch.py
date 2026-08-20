#!/usr/bin/env python3
"""Embed one bounded page batch with Gemini and keep a local checkpoint store."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise argparse.ArgumentTypeError(f"invalid page range: {part}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    if not pages:
        raise argparse.ArgumentTypeError("at least one PDF page is required")
    if len(pages) > 6:
        raise argparse.ArgumentTypeError("bounded loop permits at most six PDF pages")
    return sorted(pages)


def ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS embedding_chunks (
          chunk_id TEXT PRIMARY KEY,
          source_id TEXT NOT NULL,
          volume INTEGER NOT NULL,
          pdf_page INTEGER NOT NULL,
          record_id TEXT NOT NULL,
          scientific_name TEXT NOT NULL,
          display_name TEXT,
          chunk_kind TEXT NOT NULL,
          text_sha256 TEXT NOT NULL,
          source_text TEXT NOT NULL,
          embedding_json TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          model TEXT NOT NULL,
          task_type TEXT NOT NULL,
          embedded_at TEXT NOT NULL,
          review_status TEXT NOT NULL,
          UNIQUE(source_id, pdf_page, record_id, model, dimensions)
        );

        CREATE TABLE IF NOT EXISTS embedding_queries (
          query_id TEXT PRIMARY KEY,
          question TEXT NOT NULL,
          embedding_json TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          model TEXT NOT NULL,
          task_type TEXT NOT NULL,
          embedded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS embedding_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )
    for table in ("embedding_chunks", "embedding_queries"):
        ensure_column(connection, table, "provider", "TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, table, "vector_space_id", "TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, table, "prompt_contract_version", "TEXT NOT NULL DEFAULT ''")


def embed_content(
    *,
    api_key: str,
    model: str,
    text: str,
    dimensions: int,
) -> list[float]:
    payload: dict = {
        "model": f"models/{model}",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": dimensions,
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"Gemini embedding request failed HTTP {error.code}: {detail}") from error
    values = result.get("embedding", {}).get("values")
    if not isinstance(values, list) or len(values) != dimensions:
        raise RuntimeError(f"unexpected embedding dimensions: {len(values) if isinstance(values, list) else 'missing'}")
    vector = [float(value) for value in values]
    if not all(math.isfinite(value) for value in vector):
        raise RuntimeError("embedding contains non-finite values")
    return vector


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--pages", type=parse_pages, required=True)
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--scientific-name", required=True)
    parser.add_argument("--display-name", default="")
    parser.add_argument("--tests", type=Path, required=True)
    parser.add_argument("--fulltext-database", type=Path, default=LAB / "data/fulltext/kohler-pages.sqlite")
    parser.add_argument("--vector-database", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=LAB / "config/embedding-profile.json")
    parser.add_argument("--chunks-jsonl", type=Path)
    parser.add_argument("--model", default="gemini-embedding-2")
    parser.add_argument("--dimensions", type=int, default=768)
    parser.add_argument("--prior-external-calls", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.prior_external_calls <= 4:
        raise SystemExit("prior external calls must be between zero and four")

    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    if args.model != profile["model"] or args.dimensions != int(profile["dimensions"]):
        raise SystemExit("model and dimensions must match the selected embedding profile")
    provider = profile["provider"]
    vector_space_id = profile["vector_space_id"]
    prompt_contract = profile["prompt_contract"]
    prompt_version = prompt_contract["version"]
    if prompt_contract.get("api_task_type_field") is not False:
        raise SystemExit("gemini-embedding-2 profile must disable the API taskType field")
    chunks_jsonl = args.chunks_jsonl or LAB / "data/chunks" / f"{args.record_id}.jsonl"

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not loaded")
    tests = json.loads(args.tests.read_text(encoding="utf-8"))
    queries = tests.get("queries", [])
    if len(queries) + len(args.pages) > 4:
        raise SystemExit("bounded run exceeds four external embedding calls")

    source = sqlite3.connect(f"file:{args.fulltext_database}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in args.pages)
    rows = source.execute(
        f"SELECT source_id, volume, pdf_page, source_path, quality, best_text "
        f"FROM pages WHERE source_id=? AND pdf_page IN ({placeholders}) ORDER BY pdf_page",
        [args.source_id, *args.pages],
    ).fetchall()
    if len(rows) != len(args.pages):
        raise SystemExit(f"source page mismatch expected={args.pages} actual={[row['pdf_page'] for row in rows]}")
    if any(row["quality"] != "usable" for row in rows):
        raise SystemExit("embedding batch contains a non-usable page; OCR or review it first")

    portable_chunks: list[dict] = []
    for row in rows:
        source_text = row["best_text"].strip()
        text_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        portable_chunks.append({
            "schema_version": "1.0",
            "chunk_id": f"{args.source_id}:p{row['pdf_page']}:{args.record_id}:page",
            "source_id": args.source_id,
            "source_filename": Path(row["source_path"]).name,
            "volume": row["volume"],
            "pdf_page": row["pdf_page"],
            "record_id": args.record_id,
            "scientific_name": args.scientific_name,
            "display_name": args.display_name,
            "chunk_kind": "page",
            "text_sha256": text_hash,
            "source_text": source_text,
        })
    chunks_jsonl.parent.mkdir(parents=True, exist_ok=True)
    chunks_jsonl.write_text(
        "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in portable_chunks),
        encoding="utf-8",
    )

    args.vector_database.parent.mkdir(parents=True, exist_ok=True)
    vector_db = sqlite3.connect(args.vector_database)
    create_schema(vector_db)
    embedded_at = datetime.now(TZ).isoformat(timespec="seconds")
    external_calls = 0
    indexed: list[dict] = []
    document_vectors: dict[int, list[float]] = {}

    for row in rows:
        source_text = row["best_text"].strip()
        text_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        chunk_id = f"{args.source_id}:p{row['pdf_page']}:{args.record_id}:page"
        existing = vector_db.execute(
            "SELECT text_sha256, embedding_json, dimensions, model, vector_space_id "
            "FROM embedding_chunks WHERE chunk_id=?",
            (chunk_id,),
        ).fetchone()
        if (
            existing
            and existing[0] == text_hash
            and existing[2] == args.dimensions
            and existing[3] == args.model
            and existing[4] == vector_space_id
        ):
            vector = json.loads(existing[1])
            checkpoint = "reused"
        else:
            if args.prior_external_calls + external_calls >= 4:
                raise SystemExit("bounded run would exceed four external embedding calls")
            document_content = (
                f"Scientific name: {args.scientific_name}\n"
                f"Taiwan display name: {args.display_name or 'unresolved'}\n"
                f"Book source: {args.source_id}, PDF page {row['pdf_page']}\n\n"
                f"{source_text}"
            )
            title = f"{args.scientific_name} - {args.source_id} PDF page {row['pdf_page']}"
            document_text = prompt_contract["document"].format(title=title, content=document_content)
            vector = embed_content(
                api_key=api_key,
                model=args.model,
                text=document_text,
                dimensions=args.dimensions,
            )
            external_calls += 1
            vector_db.execute(
                """
                INSERT INTO embedding_chunks (
                  chunk_id, source_id, volume, pdf_page, record_id, scientific_name,
                  display_name, chunk_kind, text_sha256, source_text, embedding_json,
                  dimensions, model, task_type, embedded_at, review_status,
                  provider, vector_space_id, prompt_contract_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'page', ?, ?, ?, ?, ?, 'prompt_prefix_document', ?, 'candidate', ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                  text_sha256=excluded.text_sha256,
                  source_text=excluded.source_text,
                  embedding_json=excluded.embedding_json,
                  dimensions=excluded.dimensions,
                  model=excluded.model,
                  task_type=excluded.task_type,
                  embedded_at=excluded.embedded_at,
                  review_status='candidate',
                  provider=excluded.provider,
                  vector_space_id=excluded.vector_space_id,
                  prompt_contract_version=excluded.prompt_contract_version
                """,
                (
                    chunk_id, args.source_id, row["volume"], row["pdf_page"], args.record_id,
                    args.scientific_name, args.display_name, text_hash, source_text,
                    json.dumps(vector, separators=(",", ":")), args.dimensions, args.model, embedded_at,
                    provider, vector_space_id, prompt_version,
                ),
            )
            checkpoint = "embedded"
        document_vectors[int(row["pdf_page"])] = vector
        indexed.append({
            "chunk_id": chunk_id,
            "pdf_page": row["pdf_page"],
            "characters": len(source_text),
            "text_sha256": text_hash,
            "checkpoint": checkpoint,
        })

    query_results: list[dict] = []
    for item in queries:
        query_id = item["query_id"]
        question = item["question_zh_tw"]
        existing_query = vector_db.execute(
            "SELECT question, embedding_json, dimensions, model, provider, vector_space_id, "
            "prompt_contract_version FROM embedding_queries WHERE query_id=?",
            (query_id,),
        ).fetchone()
        if existing_query == (
            question,
            existing_query[1] if existing_query else None,
            args.dimensions,
            args.model,
            provider,
            vector_space_id,
            prompt_version,
        ):
            vector = json.loads(existing_query[1])
            query_checkpoint = "reused"
        else:
            if args.prior_external_calls + external_calls >= 4:
                raise SystemExit("bounded run would exceed four external embedding calls")
            query_text = prompt_contract["query"].format(content=question)
            vector = embed_content(
                api_key=api_key,
                model=args.model,
                text=query_text,
                dimensions=args.dimensions,
            )
            external_calls += 1
            vector_db.execute(
                """
                INSERT INTO embedding_queries (
                  query_id, question, embedding_json, dimensions, model, task_type, embedded_at,
                  provider, vector_space_id, prompt_contract_version
                ) VALUES (?, ?, ?, ?, ?, 'prompt_prefix_question_answering', ?, ?, ?, ?)
                ON CONFLICT(query_id) DO UPDATE SET
                  question=excluded.question,
                  embedding_json=excluded.embedding_json,
                  dimensions=excluded.dimensions,
                  model=excluded.model,
                  task_type=excluded.task_type,
                  embedded_at=excluded.embedded_at,
                  provider=excluded.provider,
                  vector_space_id=excluded.vector_space_id,
                  prompt_contract_version=excluded.prompt_contract_version
                """,
                (
                    query_id, question, json.dumps(vector, separators=(",", ":")),
                    args.dimensions, args.model, embedded_at, provider, vector_space_id, prompt_version,
                ),
            )
            query_checkpoint = "embedded"
        rankings = sorted(
            (
                {"pdf_page": page, "cosine_similarity": round(cosine(vector, doc_vector), 6)}
                for page, doc_vector in document_vectors.items()
            ),
            key=lambda result: result["cosine_similarity"],
            reverse=True,
        )
        raw_exact_hits = source.execute(
            "SELECT count(*) FROM pages WHERE source_id=? AND instr(best_text, ?) > 0",
            (args.source_id, question),
        ).fetchone()[0]
        relation_terms = item.get("required_book_terms", [])
        supporting_pages = [
            int(row["pdf_page"])
            for row in rows
            if any(term.casefold() in row["best_text"].casefold() for term in relation_terms)
        ]
        answer_gate = "supporting_book_terms_found" if supporting_pages else "no_supporting_book_relation"
        query_results.append({
            "query_id": query_id,
            "question_zh_tw": question,
            "checkpoint": query_checkpoint,
            "expected_status": item["expected_status"],
            "raw_exact_hits": raw_exact_hits,
            "required_book_terms": relation_terms,
            "supporting_pages": supporting_pages,
            "answer_gate": answer_gate,
            "rankings": rankings,
        })

    vector_db.execute("INSERT OR REPLACE INTO embedding_meta(key, value) VALUES ('model', ?)", (args.model,))
    vector_db.execute("INSERT OR REPLACE INTO embedding_meta(key, value) VALUES ('dimensions', ?)", (str(args.dimensions),))
    vector_db.execute("INSERT OR REPLACE INTO embedding_meta(key, value) VALUES ('provider', ?)", (provider,))
    vector_db.execute("INSERT OR REPLACE INTO embedding_meta(key, value) VALUES ('vector_space_id', ?)", (vector_space_id,))
    vector_db.execute("INSERT OR REPLACE INTO embedding_meta(key, value) VALUES ('prompt_contract_version', ?)", (prompt_version,))
    vector_db.execute("INSERT OR REPLACE INTO embedding_meta(key, value) VALUES ('updated_at', ?)", (embedded_at,))
    vector_db.commit()
    vector_db.close()
    source.close()

    report = {
        "schema_version": "1.1",
        "run_id": tests["run_id"],
        "batch_id": tests["batch_id"],
        "embedded_at": embedded_at,
        "source_id": args.source_id,
        "record_id": args.record_id,
        "scientific_name": args.scientific_name,
        "display_name": args.display_name,
        "pdf_pages": args.pages,
        "model": args.model,
        "dimensions": args.dimensions,
        "provider": provider,
        "embedding_profile": str(args.profile.resolve()),
        "profile_id": profile["profile_id"],
        "vector_space_id": vector_space_id,
        "prompt_contract_version": prompt_version,
        "api_task_type_field_used": False,
        "external_model_calls": args.prior_external_calls + external_calls,
        "external_model_calls_this_process": external_calls,
        "recovered_external_model_calls": args.prior_external_calls,
        "incremental_usd": 0,
        "paid_fallback_used": False,
        "indexed_chunks": indexed,
        "query_results": query_results,
        "vector_database": str(args.vector_database.resolve()),
        "portable_chunks_jsonl": str(chunks_jsonl.resolve()),
        "review_status": "candidate",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "complete",
        "chunks": len(indexed),
        "queries": len(query_results),
        "external_model_calls": external_calls,
        "report": str(args.report),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
