#!/usr/bin/env python3
"""Validate non-canonical 512/100 pre-embedding chunk artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
UNIT_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def candidate_source_chain(candidate: dict) -> dict:
    fields = (
        "maker_receipt_sha256",
        "validation_check_sha256",
        "source_disposition_sha256",
        "boundary_overlay_plan_sha256",
        "boundary_segment_sha256",
        "package_sha256",
        "receipt_sha256",
        "recovery_kind",
        "source_parent_entry_id",
        "continuation_v2_receipts",
    )
    return {field: candidate.get(field) for field in fields if candidate.get(field) is not None}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--require-caught-up", action="store_true")
    args = parser.parse_args()
    chunks_path = args.root / "chunks-candidate/section-aware-512-100-v1.jsonl"
    manifest = json.loads((args.root / "chunks-candidate/manifest.json").read_text(encoding="utf-8"))
    source = json.loads(
        (args.root / "integration/embedding-ready-candidate-manifest.json").read_text(encoding="utf-8")
    )
    chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line]
    candidate_by_entry = {item["entry_id"]: item for item in source["candidates"]}
    naming_scopes = {}
    naming_hashes = {}
    for path in (args.root / "naming/staging").glob("*.naming.json"):
        naming_text = path.read_text(encoding="utf-8")
        naming = json.loads(naming_text)
        resolution = naming["name_resolution"]
        naming_hashes[naming["entry_id"]] = sha256_text(naming_text)
        naming_scopes[naming["entry_id"]] = resolution.get("display_name_source_scope") or (
            "unresolved" if resolution.get("display_name_zh_tw") is None else "unclassified_staging"
        )
    errors = []
    ids = Counter(item["chunk_id"] for item in chunks)
    errors.extend(f"duplicate_chunk_id:{key}" for key, count in ids.items() if count > 1)

    groups: dict[tuple[str, int, int, int], list[dict]] = {}
    for chunk in chunks:
        candidate = candidate_by_entry.get(chunk["entry_id"])
        if candidate is None:
            errors.append(f"unknown_entry:{chunk['chunk_id']}")
            continue
        section = candidate["sections"][chunk["section_index"]]
        quote = section["exact_source_quotes"][chunk["source_span_index"]]
        locator = section["source_locators"][chunk["source_span_index"]]
        start, end = chunk["source_quote_char_start"], chunk["source_quote_char_end"]
        if quote[start:end] != chunk["source_text"]:
            errors.append(f"not_exact_source_substring:{chunk['chunk_id']}")
        if chunk["pdf_page"] != locator["pdf_page"]:
            errors.append(f"page_mismatch:{chunk['chunk_id']}")
        if chunk["page_char_start"] != locator["char_start"] + start:
            errors.append(f"page_char_start_mismatch:{chunk['chunk_id']}")
        if chunk["page_char_end"] != locator["char_start"] + end:
            errors.append(f"page_char_end_mismatch:{chunk['chunk_id']}")
        if chunk["text_sha256"] != sha256_text(chunk["source_text"]):
            errors.append(f"text_hash_mismatch:{chunk['chunk_id']}")
        expected = dict(chunk)
        stored_chunk_hash = expected.pop("chunk_sha256", None)
        if stored_chunk_hash != sha256_json(expected):
            errors.append(f"chunk_hash_mismatch:{chunk['chunk_id']}")
        if chunk["token_unit_count"] != len(UNIT_RE.findall(chunk["source_text"])):
            errors.append(f"token_count_mismatch:{chunk['chunk_id']}")
        if chunk["token_unit_count"] > 512:
            errors.append(f"token_limit_exceeded:{chunk['chunk_id']}")
        if chunk["canonical_write_allowed"] or chunk["embedding_call_performed"]:
            errors.append(f"unsafe_promotion_state:{chunk['chunk_id']}")
        if chunk.get("display_name_source_scope") != naming_scopes.get(chunk["entry_id"]):
            errors.append(f"display_name_source_scope_drift:{chunk['chunk_id']}")
        expected_provenance = {
            "integration_manifest_sha256": source["manifest_sha256"],
            "candidate_sha256": candidate["candidate_sha256"],
            "maker_receipt_sha256": candidate.get("maker_receipt_sha256"),
            "validation_check_sha256": candidate.get("validation_check_sha256"),
            "source_disposition_sha256": candidate.get("source_disposition_sha256"),
            "candidate_source_chain": candidate_source_chain(candidate),
            "naming_artifact_sha256": naming_hashes.get(chunk["entry_id"]),
        }
        for field, expected_value in expected_provenance.items():
            if chunk.get(field) != expected_value:
                errors.append(f"provenance_chain_drift:{field}:{chunk['chunk_id']}")
        groups.setdefault(
            (chunk["entry_id"], chunk["section_index"], chunk["pdf_page"], chunk["source_span_index"]), []
        ).append(chunk)

    for key, group in groups.items():
        group.sort(key=lambda row: row["token_unit_start"])
        if group[0]["token_unit_start"] != 0:
            errors.append(f"span_does_not_start_at_zero:{key}")
        for previous, current in zip(group, group[1:]):
            overlap = previous["token_unit_end"] - current["token_unit_start"]
            if overlap != 100:
                errors.append(f"overlap_not_100:{key}:{overlap}")

    entry_count = len({item["entry_id"] for item in chunks})
    if manifest["chunk_count"] != len(chunks) or manifest["entry_count"] != entry_count:
        errors.append("manifest_counts_mismatch")
    if manifest["source_manifest_sha256"] != source["manifest_sha256"]:
        errors.append("source_manifest_hash_mismatch")
    if args.require_caught_up and manifest["missing_naming_entry_ids"]:
        errors.append("naming_lane_not_caught_up")
    result = {
        "status": "PASS" if not errors else "FAIL",
        "source_candidates": source["candidate_count"],
        "chunk_entries": entry_count,
        "chunks": len(chunks),
        "missing_naming": len(manifest["missing_naming_entry_ids"]),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
