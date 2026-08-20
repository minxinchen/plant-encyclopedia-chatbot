#!/usr/bin/env python3
"""Validate the full-book Google Gem export and its source projection."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
DEFAULT_INPUT = DEFAULT_ROOT / "exports/google-gem/fullbook-beta"
EXPECTED_FILES = {"gem-instructions.md", *(f"knowledge-s{i:02d}.md" for i in range(1, 9))}
SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"AQ\.[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)(?:api[_-]?key|token|secret)\s*[:=]\s*[0-9A-Za-z_-]{20,}"),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_sha(value: dict[str, Any]) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    failures: list[str] = []

    manifest_path = args.input / "manifest.json"
    if not manifest_path.exists():
        print(json.dumps({"valid": False, "failures": ["manifest missing"]}))
        raise SystemExit(2)
    manifest = json.loads(manifest_path.read_text())
    supplied_hash = manifest.pop("manifest_sha256", None)
    if supplied_hash != canonical_sha(manifest):
        fail("manifest_sha256 mismatch", failures)
    manifest["manifest_sha256"] = supplied_hash
    if manifest.get("uploadable_file_count") != 9:
        fail("uploadable file count is not 9", failures)
    file_names = {row.get("path") for row in manifest.get("files", [])}
    if file_names != EXPECTED_FILES:
        fail(f"file set mismatch: {sorted(file_names ^ EXPECTED_FILES)}", failures)

    file_text: dict[str, str] = {}
    for row in manifest.get("files", []):
        path = args.input / str(row.get("path"))
        if not path.exists():
            fail(f"missing file: {path.name}", failures)
            continue
        raw = path.read_bytes()
        if len(raw) > 100 * 1024 * 1024:
            fail(f"file exceeds 100 MB: {path.name}", failures)
        if row.get("bytes") != len(raw) or row.get("sha256") != sha256_bytes(raw):
            fail(f"file bytes/hash mismatch: {path.name}", failures)
        text = raw.decode()
        file_text[path.name] = text
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                fail(f"secret-like value detected: {path.name}", failures)
        if "embedding_json" in text or '"vector"' in text:
            fail(f"vector payload marker detected: {path.name}", failures)
        if "繁中檢索提示" in text or "zh_tw_rendering" in text:
            fail(f"unvalidated machine rendering detected: {path.name}", failures)

    if manifest.get("contains_vectors") is not False or manifest.get("contains_api_keys") is not False:
        fail("manifest safety flags are not false", failures)
    if args.require_complete and manifest.get("unclassified_display_name_source_scope_count") != 0:
        fail("display-name source scopes are not fully classified", failures)
    records = manifest.get("records", [])
    if manifest.get("record_count") != len(records):
        fail("record count mismatch", failures)
    record_ids: set[str] = set()
    for row in records:
        record_id = str(row.get("record_id"))
        if record_id in record_ids:
            fail(f"duplicate record id: {record_id}", failures)
        record_ids.add(record_id)
        shard = str(row.get("shard", "")).lower()
        target = file_text.get(f"knowledge-{shard}.md", "")
        if f"record-file-sha256: {row.get('record_file_sha256')}" not in target:
            fail(f"record absent from owned shard: {record_id}", failures)
        source_path = LAB / str(row.get("source_path"))
        if not source_path.exists() or sha256_bytes(source_path.read_bytes()) != row.get("record_file_sha256"):
            fail(f"source record hash drift: {record_id}", failures)
        for section in row.get("sections", []):
            marker = f"exact-text-sha256: {section.get('exact_text_sha256')}"
            if marker not in target:
                fail(f"section absent from owned shard: {record_id} {section.get('exact_text_sha256')}", failures)

    candidate_manifest = json.loads((args.root / "records-candidate/manifest.json").read_text())
    expected_candidates = {str(row.get("record_sha256")) for row in candidate_manifest.get("records", [])}
    exported_candidate_paths = {
        str(row.get("source_path")) for row in records if row.get("source_kind") == "machine_extracted_candidate"
    }
    expected_candidate_paths = {
        str(Path("data/candidates/preembedding-v1/records-candidate") / row["path"])
        for row in candidate_manifest.get("records", [])
    }
    if exported_candidate_paths != expected_candidate_paths:
        fail("candidate record coverage mismatch", failures)
    if len(expected_candidates) != manifest.get("candidate_record_count"):
        fail("candidate record manifest cardinality mismatch", failures)
    approved_paths = {str(path.relative_to(LAB)) for path in (LAB / "data/records").glob("*.json")}
    exported_approved_paths = {
        str(row.get("source_path")) for row in records if row.get("source_kind") == "approved_baseline"
    }
    if exported_approved_paths != approved_paths:
        fail("approved baseline record coverage mismatch", failures)

    if args.require_complete:
        if manifest.get("status") != "complete" or manifest.get("completion_blockers"):
            fail(f"export is not complete: {manifest.get('completion_blockers')}", failures)
        promotion = LAB / "reports/fullbook-main-index-promotion.json"
        if not promotion.exists():
            fail("main promotion report missing", failures)
        else:
            report = json.loads(promotion.read_text())
            if report.get("status") not in {"pass", "complete"}:
                fail("main promotion report is not PASS", failures)

    result = {
        "valid": not failures,
        "status": manifest.get("status"),
        "uploadable_file_count": manifest.get("uploadable_file_count"),
        "record_count": len(records),
        "section_count": sum(len(row.get("sections", [])) for row in records),
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not failures else 2)


if __name__ == "__main__":
    main()
