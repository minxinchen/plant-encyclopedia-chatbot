#!/usr/bin/env python3
"""Zero-model-call smoke test for the loopback full-book chat API."""

from __future__ import annotations

import argparse
import json
import urllib.request


def request_json(url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=body, method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18765")
    parser.add_argument("--require-fullbook", action="store_true")
    args = parser.parse_args()
    health = request_json(args.base_url.rstrip("/") + "/health")
    refusal = request_json(args.base_url.rstrip("/") + "/v1/chat", {
        "question": "阿斯匹靈是什麼藥？", "language": "zh-TW", "top_k": 4,
    })
    database = health.get("database") or {}
    checks = {
        "service_ready": health.get("status") == "ready" and database.get("sqlite_integrity") == "ok",
        "traditional_chinese": (health.get("capabilities") or {}).get("traditional_chinese") is True,
        "book_only_gate": (health.get("capabilities") or {}).get("book_only_answer_gate") is True,
        "non_kohler_refusal": refusal.get("answer_status") == "refused_non_kohler_drug",
        "refusal_zero_model_calls": refusal.get("external_embedding_calls") == 0
        and refusal.get("external_generation_calls") == 0,
    }
    if args.require_fullbook:
        checks["fullbook_beta_rows"] = int(database.get("machine_extracted_beta_chunks") or 0) > 0
        checks["fullbook_status_contract"] = database.get("active_review_statuses") == "approved,machine_extracted_beta"
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "base_url": args.base_url, "checks": checks,
        "database": database,
        "refusal": {
            "answer_status": refusal.get("answer_status"),
            "external_embedding_calls": refusal.get("external_embedding_calls"),
            "external_generation_calls": refusal.get("external_generation_calls"),
        },
    }
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
