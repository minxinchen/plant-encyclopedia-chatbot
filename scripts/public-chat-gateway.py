#!/usr/bin/env python3
"""Bearer-protected loopback gateway for the public Köhler chat demo.

This intentionally exposes only the policy-gated plant chat endpoint. It does
not proxy arbitrary URLs or the raw local Qwen API.

author: Codex (GPT-5)
date: 2026-08-22
"""

from __future__ import annotations

import argparse
import hmac
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MAX_BODY_BYTES = 8 * 1024


class Handler(BaseHTTPRequestHandler):
    token = ""
    upstream_host = "127.0.0.1"
    upstream_port = 18765
    server_version = "KohlerPublicGateway/1.0"

    def send_json(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        return hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        self.proxy("GET", "/health", None)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "invalid_content_length"})
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid_json"})
            return
        if set(payload) - {"question", "language", "top_k", "include_images"}:
            self.send_json(400, {"error": "unsupported_field"})
            return
        question = payload.get("question")
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
            self.send_json(400, {"error": "invalid_question"})
            return
        self.proxy("POST", "/v1/chat", body)

    def proxy(self, method: str, path: str, body: bytes | None) -> None:
        try:
            connection = http.client.HTTPConnection(
                self.upstream_host, self.upstream_port, timeout=180
            )
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            connection.close()
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
        except (OSError, TimeoutError):
            self.send_json(503, {"error": "local_model_offline"})

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {format_string % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=18765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("public chat gateway must remain loopback-only")
    token = os.environ.get("PLANT_PUBLIC_GATEWAY_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("PLANT_PUBLIC_GATEWAY_TOKEN must contain at least 32 characters")
    Handler.token = token
    Handler.upstream_host = args.upstream_host
    Handler.upstream_port = args.upstream_port
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Köhler public gateway listening at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
