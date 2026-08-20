#!/usr/bin/env python3
"""Loopback HTTP API for the full-book source-grounded chat engine.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = LAB / "data/index/plant-embeddings.sqlite"
DEFAULT_ENV = LAB.parents[1] / "secrets/plant-encyclopedia.env.local"


def load_engine():
    path = LAB / "scripts/fullbook-beta-chat.py"
    spec = importlib.util.spec_from_file_location("fullbook_beta_chat", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load full-book chat engine")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def database_health(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ready": False, "error": "main_index_missing"}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    meta = dict(db.execute("SELECT key,value FROM embedding_meta"))
    total = db.execute("SELECT count(*) FROM embedding_chunks").fetchone()[0]
    approved = db.execute(
        "SELECT count(*) FROM embedding_chunks WHERE review_status='approved'"
    ).fetchone()[0]
    beta = db.execute(
        "SELECT count(*) FROM embedding_chunks WHERE review_status='machine_extracted_beta'"
    ).fetchone()[0]
    records = db.execute("SELECT count(DISTINCT record_id) FROM embedding_chunks").fetchone()[0]
    db.close()
    return {
        "ready": integrity == "ok" and total > 0, "sqlite_integrity": integrity,
        "total_chunks": total, "approved_chunks": approved,
        "machine_extracted_beta_chunks": beta, "record_count": records,
        "schema_version": meta.get("schema_version"),
        "active_chunk_profile": meta.get("active_chunk_profile"),
        "active_review_statuses": meta.get("active_review_statuses", "approved"),
        "vector_space_id": meta.get("vector_space_id"),
    }


class Handler(BaseHTTPRequestHandler):
    engine = None
    database = DEFAULT_DATABASE
    env_file = DEFAULT_ENV
    server_version = "KohlerFullbookChat/1.0"

    def send_json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_json(404, {"error": "not_found"})
            return
        health = database_health(self.database)
        self.send_json(200 if health["ready"] else 503, {
            "service": "kohler-fullbook-chat-api", "status": "ready" if health["ready"] else "not_ready",
            "database": health, "capabilities": {
                "traditional_chinese": True, "english": True,
                "simplified_input_to_zh_tw": True, "book_only_answer_gate": True,
                "image_reasoning": False, "n8n_adapter": True, "google_search_grounding": False,
            },
        })

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/v1/chat", "/v1/retrieve"}:
            self.send_json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("invalid_content_length")
            payload = json.loads(self.rfile.read(length))
            question = payload.get("question", "")
            if not isinstance(question, str) or not question.strip():
                raise ValueError("question_required")
            top_k = int(payload.get("top_k", 6))
            if not 1 <= top_k <= 12:
                raise ValueError("top_k_out_of_range")
            requested_language = payload.get("language")
            if requested_language not in {None, "zh-TW", "zh-Hans", "en"}:
                raise ValueError("language_not_supported")
            response_locale = (
                "en" if requested_language == "en" else "zh-TW"
                if requested_language in {"zh-TW", "zh-Hans"} else self.engine.locale(question)
            )
            if payload.get("include_images") is True:
                self.send_json(200, {
                    "schema_version": "1.0", "request_id": str(uuid.uuid4()), "question": question,
                    "response_locale": response_locale,
                    "answer_status": "image_reasoning_not_yet_available",
                    "answer": (
                        "Full-book beta currently exposes validated text evidence only; image reasoning is not yet available."
                        if response_locale == "en" else "目前全書 beta 僅開放文字書證，尚未開放圖像推理。"
                    ),
                    "evidence": [], "external_embedding_calls": 0,
                    "external_generation_calls": 0, "incremental_usd": 0,
                })
                return
            response = self.engine.answer(
                question.strip(), self.database, self.env_file, top_k,
                self.path == "/v1/retrieve", response_locale,
            )
            self.send_json(200, response)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": "invalid_request", "detail": str(error)})
        except Exception as error:
            print(f"request error: {type(error).__name__}: {error}", file=sys.stderr)
            self.send_json(503, {"error": "service_unavailable", "detail": type(error).__name__})

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {format_string % args}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("full-book beta API is loopback-only")
    Handler.engine = load_engine()
    Handler.database = args.database
    Handler.env_file = args.env_file
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Köhler full-book chat API listening at http://{args.host}:{args.port}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
