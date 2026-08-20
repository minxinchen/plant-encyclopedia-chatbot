#!/usr/bin/env python3
"""Promote validated v2 staging to the naming eligibility authority.

This does not promote canonical records, chunks, embeddings, or indexes. The
previous staging manifest is archived by content hash before replacement.

author: Nio (Master)
date: 2026-08-20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
ROOT = LAB / "data/candidates/preembedding-v1"
SOURCE = ROOT / "integration-v2/consolidated-embedding-ready-candidate-manifest.json"
TARGET = ROOT / "integration/embedding-ready-candidate-manifest.json"
RECEIPT = ROOT / "integration-v2/staging-promotion-receipt.json"


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(value)
        handle.flush()
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    validation = subprocess.run(
        ["python3", "scripts/validate-consolidated-v2-staging-manifest.py", "--require-complete"],
        cwd=LAB,
        text=True,
        capture_output=True,
    )
    if validation.returncode:
        raise SystemExit(validation.stderr or validation.stdout)
    source_bytes = SOURCE.read_bytes()
    source = json.loads(source_bytes)
    old_bytes = TARGET.read_bytes() if TARGET.exists() else b""
    old_sha = digest_bytes(old_bytes) if old_bytes else None
    new_sha = digest_bytes(source_bytes)
    receipt = {
        "schema_version": "1.0",
        "promotion_scope": "naming-eligibility-staging-only",
        "promoted_at": now(),
        "source_path": str(SOURCE.relative_to(LAB)),
        "target_path": str(TARGET.relative_to(LAB)),
        "source_manifest_sha256": source["manifest_sha256"],
        "source_content_sha256": new_sha,
        "previous_content_sha256": old_sha,
        "candidate_count": source["candidate_count"],
        "validation": json.loads(validation.stdout.splitlines()[-1]),
        "canonical_writes": False,
        "chunk_writes": False,
        "embedding_calls": False,
        "index_writes": False,
        "write_requested": args.write,
    }
    receipt["receipt_sha256"] = digest_bytes(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    if args.write:
        if old_bytes and old_sha != new_sha:
            archive = ROOT / "integration/archive-pre-v2" / f"embedding-ready-candidate-manifest-{old_sha}.json"
            if not archive.exists():
                atomic_write(archive, old_bytes)
        atomic_write(TARGET, source_bytes)
        atomic_write(RECEIPT, (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
