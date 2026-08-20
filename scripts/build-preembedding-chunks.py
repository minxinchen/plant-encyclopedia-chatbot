#!/usr/bin/env python3
"""Build deterministic 512/100 staging chunks from validated entry candidates.

The output is deliberately non-canonical and contains no vectors.  A chunk never
crosses a source page, section, or exact source locator.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
UNIT_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def naming_by_entry(root: Path) -> dict[str, tuple[dict, str]]:
    output = {}
    for path in sorted((root / "naming/staging").glob("*.naming.json")):
        payload = read_json(path)
        output[payload["entry_id"]] = (payload, sha256_text(path.read_text(encoding="utf-8")))
    return output


def chunk_quote(text: str, target: int, overlap: int) -> list[tuple[int, int, int, int, str]]:
    matches = list(UNIT_RE.finditer(text))
    if not matches:
        return []
    output = []
    unit_start = 0
    while unit_start < len(matches):
        unit_end = min(unit_start + target, len(matches))
        char_start = matches[unit_start].start()
        char_end = matches[unit_end - 1].end()
        output.append((unit_start, unit_end, char_start, char_end, text[char_start:char_end]))
        if unit_end == len(matches):
            break
        unit_start = max(unit_start + 1, unit_end - overlap)
    return output


def embedding_input(chunk: dict) -> str:
    return (
        f"Scientific name: {chunk['accepted_scientific_name'] or chunk['book_taxon_candidate']}\n"
        f"Book taxon: {chunk['book_taxon_candidate']}\n"
        f"Chinese display name: {chunk['display_name_zh_tw'] or 'unresolved'}\n"
        f"Display name source scope: {chunk['display_name_source_scope']}\n"
        f"Section: {chunk['section_type']}\n"
        f"Book source: {chunk['source_id']}, PDF page {chunk['pdf_page']}\n\n"
        f"{chunk['source_text']}"
    )


def candidate_source_chain(candidate: dict) -> dict:
    """Project the exact upstream provenance for regular, child, and recovery candidates."""
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
    parser.add_argument("--target", type=int, default=512)
    parser.add_argument("--overlap", type=int, default=100)
    args = parser.parse_args()
    if args.target <= 0 or args.overlap < 0 or args.overlap >= args.target:
        raise SystemExit("require target > overlap >= 0")

    source_path = args.root / "integration/embedding-ready-candidate-manifest.json"
    source = read_json(source_path)
    names = naming_by_entry(args.root)
    chunks = []
    missing_names = []

    for candidate in source["candidates"]:
        entry_id = candidate["entry_id"]
        if entry_id not in names:
            missing_names.append(entry_id)
            continue
        naming, naming_artifact_sha256 = names[entry_id]
        resolution = naming["name_resolution"]
        status = resolution["terminal_status"]
        display_name_scope = resolution.get("display_name_source_scope") or (
            "unresolved" if resolution.get("display_name_zh_tw") is None else "unclassified_staging"
        )
        if status not in {"accepted", "alias", "unresolved"}:
            raise SystemExit(f"nonterminal naming status: {entry_id}:{status}")
        for section_index, section in enumerate(candidate["sections"]):
            quotes = section.get("exact_source_quotes")
            locators = section.get("source_locators")
            if not isinstance(quotes, list) or not isinstance(locators, list) or len(quotes) != len(locators):
                raise SystemExit(f"quote/locator mismatch: {entry_id}:section-{section_index}")
            for span_index, (quote, locator) in enumerate(zip(quotes, locators)):
                if sha256_text(quote) != locator["exact_text_sha256"]:
                    raise SystemExit(f"source hash mismatch: {entry_id}:section-{section_index}:span-{span_index}")
                for sequence, (unit_start, unit_end, char_start, char_end, text) in enumerate(
                    chunk_quote(quote, args.target, args.overlap)
                ):
                    chunk_id = (
                        f"{entry_id}:s{section_index:02d}:p{locator['pdf_page']:04d}:"
                        f"x{span_index:02d}:c{sequence:02d}:section-aware-512-100-v1"
                    )
                    chunk = {
                        "schema_version": "1.0",
                        "profile_id": "section-aware-512-100-v1",
                        "chunk_id": chunk_id,
                        "entry_id": entry_id,
                        "source_id": candidate["source_id"],
                        "volume": candidate["volume"],
                        "pdf_page": locator["pdf_page"],
                        "section_index": section_index,
                        "section_type": section["section_type"],
                        "source_span_index": span_index,
                        "book_taxon_candidate": candidate["book_taxon_candidate"],
                        "accepted_scientific_name": resolution.get("accepted_scientific_name"),
                        "display_name_zh_tw": resolution.get("display_name_zh_tw"),
                        "display_name_source_scope": display_name_scope,
                        "name_resolution_status": status,
                        "target_token_units": args.target,
                        "overlap_token_units": args.overlap,
                        "token_unit_start": unit_start,
                        "token_unit_end": unit_end,
                        "token_unit_count": unit_end - unit_start,
                        "source_quote_char_start": char_start,
                        "source_quote_char_end": char_end,
                        "page_char_start": locator["char_start"] + char_start,
                        "page_char_end": locator["char_start"] + char_end,
                        "page_text_sha256": locator["page_text_sha256"],
                        "source_pdf_sha256": locator["source_pdf_sha256"],
                        "source_span_sha256": locator["exact_text_sha256"],
                        "source_text": text,
                        "text_sha256": sha256_text(text),
                        "integration_manifest_sha256": source["manifest_sha256"],
                        "candidate_sha256": candidate["candidate_sha256"],
                        "maker_receipt_sha256": candidate.get("maker_receipt_sha256"),
                        "validation_check_sha256": candidate.get("validation_check_sha256"),
                        "source_disposition_sha256": candidate.get("source_disposition_sha256"),
                        "candidate_source_chain": candidate_source_chain(candidate),
                        "naming_artifact_sha256": naming_artifact_sha256,
                        "review_status": "machine_extracted_candidate",
                        "canonical_write_allowed": False,
                        "embedding_call_performed": False,
                        "vector_space_id": None,
                    }
                    chunk["embedding_input_sha256"] = sha256_text(embedding_input(chunk))
                    chunk["chunk_sha256"] = sha256_json(chunk)
                    chunks.append(chunk)

    chunks.sort(key=lambda row: row["chunk_id"])
    output_dir = args.root / "chunks-candidate"
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = output_dir / "section-aware-512-100-v1.jsonl"
    temporary = chunks_path.with_suffix(".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in chunks),
        encoding="utf-8",
    )
    temporary.replace(chunks_path)

    summary = {
        "schema_version": "1.0",
        "generated_at": now(),
        "profile_id": "section-aware-512-100-v1",
        "source_manifest_sha256": source["manifest_sha256"],
        "source_candidate_count": source["candidate_count"],
        "named_candidate_count": source["candidate_count"] - len(missing_names),
        "missing_naming_entry_ids": missing_names,
        "chunk_count": len(chunks),
        "entry_count": len({row["entry_id"] for row in chunks}),
        "canonical_write_allowed": False,
        "embedding_calls_performed": 0,
        "vector_space_id": None,
    }
    summary["summary_sha256"] = sha256_json(summary)
    summary_path = output_dir / "manifest.json"
    temporary = summary_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(summary_path)
    print(json.dumps({"chunks": str(chunks_path), "manifest": str(summary_path), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
