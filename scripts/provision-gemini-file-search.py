#!/usr/bin/env python3
"""Idempotently upload the eight public-safe Köhler knowledge shards.

Dry-run is the default. ``--execute`` creates or reuses one Gemini File Search
store, uploads only missing shard display names, and prints the Script Property
value needed by the Apps Script web app. Secrets are read from the existing
local env file and are never printed.

author: Codex (GPT-5)
date: 2026-08-22
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = LAB / "data/candidates/preembedding-v1/exports/google-gem/fullbook-beta"
DEFAULT_ENV = LAB.parents[1] / "secrets/plant-encyclopedia.env.local"
DISPLAY_NAME = "kohler-fullbook-public-beta"


def env_value(path: Path, key: str) -> str:
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith(f"{key}="):
            return raw.split("=", 1)[1].strip().strip("\"'")
    return ""


def request_json(url: str, key: str, method: str = "GET",
                 payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read() or b"{}")


def list_all(url: str, key: str, field: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    token = ""
    while True:
        separator = "&" if "?" in url else "?"
        page_url = url + (f"{separator}pageToken={token}" if token else "")
        payload = request_json(page_url, key)
        values.extend(payload.get(field, []))
        token = payload.get("nextPageToken", "")
        if not token:
            return values


def create_store(key: str) -> dict[str, Any]:
    return request_json(
        "https://generativelanguage.googleapis.com/v1beta/fileSearchStores",
        key, "POST", {"displayName": DISPLAY_NAME, "embeddingModel": "models/gemini-embedding-2"},
    )


def upload(store_name: str, path: Path, key: str) -> dict[str, Any]:
    start = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/upload/v1beta/{store_name}:uploadToFileSearchStore",
        data=json.dumps({
            "displayName": path.name,
            "chunkingConfig": {"whiteSpaceConfig": {
                "maxTokensPerChunk": 512, "maxOverlapTokens": 100,
            }},
        }).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json", "x-goog-api-key": key,
            "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(path.stat().st_size),
            "X-Goog-Upload-Header-Content-Type": "text/markdown",
        },
    )
    with urllib.request.urlopen(start, timeout=120) as response:
        upload_url = response.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError(f"missing resumable upload URL for {path.name}")
    finalize = urllib.request.Request(
        upload_url, data=path.read_bytes(), method="POST",
        headers={
            "Content-Length": str(path.stat().st_size), "Content-Type": "text/markdown",
            "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize",
        },
    )
    with urllib.request.urlopen(finalize, timeout=180) as response:
        return json.loads(response.read())


def wait_operation(operation: dict[str, Any], key: str) -> dict[str, Any]:
    current = operation
    for _ in range(120):
        if current.get("done"):
            if current.get("error"):
                raise RuntimeError(f"File Search operation failed: {current['error']}")
            return current
        time.sleep(2)
        current = request_json(
            f"https://generativelanguage.googleapis.com/v1beta/{current['name']}", key,
        )
    raise TimeoutError(f"File Search operation did not finish: {operation.get('name')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    shards = sorted(args.input.glob("knowledge-s[0-9][0-9].md"))
    if len(shards) != 8:
        raise SystemExit(f"expected 8 knowledge shards, found {len(shards)}")
    plan = {
        "store_display_name": DISPLAY_NAME,
        "embedding_model": "models/gemini-embedding-2",
        "chunk_profile": "512/100",
        "files": [path.name for path in shards],
        "total_bytes": sum(path.stat().st_size for path in shards),
        "execute": args.execute,
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False))
        return
    key = env_value(args.env_file, "GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is unavailable")
    stores = list_all(
        "https://generativelanguage.googleapis.com/v1beta/fileSearchStores", key,
        "fileSearchStores",
    )
    matches = [item for item in stores if item.get("displayName") == DISPLAY_NAME]
    store = matches[0] if matches else create_store(key)
    documents = list_all(
        f"https://generativelanguage.googleapis.com/v1beta/{store['name']}/documents",
        key, "documents",
    )
    existing = {item.get("displayName") for item in documents}
    uploaded: list[str] = []
    for path in shards:
        if path.name in existing:
            continue
        wait_operation(upload(store["name"], path, key), key)
        uploaded.append(path.name)
    print(json.dumps({
        **plan, "store_name": store["name"], "created_store": not matches,
        "already_present": sorted(existing), "uploaded": uploaded,
        "script_properties": {
            "FILE_SEARCH_STORE_NAME": store["name"],
            "GEMINI_MODEL": "gemini-3.5-flash-lite",
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
