#!/usr/bin/env python3
"""Validate the frozen source receipt against the currently mounted source PDFs.

Normal runs verify identity metadata and byte sizes only. ``--rehash-source-pdfs``
performs the completion-grade streaming SHA-256 check without loading a PDF into
memory and writes a durable check under preembedding-v1/checks.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
DEFAULT_SOURCE_MANIFEST = LAB / "data/source-manifest.json"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--rehash-source-pdfs", action="store_true")
    parser.add_argument("--require-full-hash", action="store_true")
    args = parser.parse_args()
    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    receipt_path = args.root / "source-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_by_id = {item["source_id"]: item for item in receipt["sources"]}
    manifest_by_id = {item["source_id"]: item for item in source_manifest["files"]}
    errors = []
    files = []
    if len(receipt_by_id) != 4 or set(receipt_by_id) != set(manifest_by_id):
        errors.append("source_id_set_mismatch")
    for source_id in sorted(set(receipt_by_id) | set(manifest_by_id)):
        expected = receipt_by_id.get(source_id)
        declared = manifest_by_id.get(source_id)
        if expected is None or declared is None:
            continue
        path = Path(declared["path"])
        exists = path.is_file()
        observed_bytes = path.stat().st_size if exists else None
        metadata_matches = all(
            expected.get(field) == declared.get(field)
            for field in ("source_id", "volume", "pages", "bytes")
        )
        bytes_match = exists and observed_bytes == expected["bytes"]
        observed_sha256 = sha256_file(path) if args.rehash_source_pdfs and bytes_match else None
        sha256_matches = observed_sha256 == expected["sha256"] if observed_sha256 else None
        if not exists:
            errors.append(f"source_pdf_missing:{source_id}")
        if not metadata_matches:
            errors.append(f"source_metadata_mismatch:{source_id}")
        if not bytes_match:
            errors.append(f"source_byte_size_mismatch:{source_id}")
        if args.rehash_source_pdfs and sha256_matches is not True:
            errors.append(f"source_sha256_mismatch:{source_id}")
        files.append({
            "source_id": source_id,
            "volume": expected["volume"],
            "path": str(path),
            "exists": exists,
            "expected_bytes": expected["bytes"],
            "observed_bytes": observed_bytes,
            "bytes_match": bytes_match,
            "expected_sha256": expected["sha256"],
            "observed_sha256": observed_sha256,
            "sha256_matches": sha256_matches,
        })
    full_hash_verified = (
        args.rehash_source_pdfs
        and len(files) == 4
        and all(item["sha256_matches"] is True for item in files)
    )
    if args.require_full_hash and not full_hash_verified:
        errors.append("full_source_pdf_hash_verification_required")
    check = {
        "schema_version": "1.0",
        "pipeline_id": "preembedding-v1",
        "checked_at": now(),
        "mode": "full_sha256" if args.rehash_source_pdfs else "metadata_and_bytes",
        "source_manifest_path": str(args.source_manifest.resolve()),
        "source_receipt_path": str(receipt_path.resolve()),
        "source_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "files": files,
        "file_count": len(files),
        "full_hash_verified": full_hash_verified,
        "errors": sorted(set(errors)),
        "status": "PASS" if not errors else "FAIL",
    }
    check["check_sha256"] = hashlib.sha256(canonical_json(check).encode("utf-8")).hexdigest()
    write_json(args.root / "checks/source-receipt-validation.json", check)
    print(json.dumps(check, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
