#!/usr/bin/env python3
"""Create portable, vector-free embedding jobs from validated staging chunks."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    profile = json.loads((LAB / "config/embedding-profile.json").read_text(encoding="utf-8"))
    chunks_path = args.root / "chunks-candidate/section-aware-512-100-v1.jsonl"
    chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line]
    chunk_manifest = json.loads((args.root / "chunks-candidate/manifest.json").read_text(encoding="utf-8"))
    jobs = []
    for chunk in chunks:
        text = embedding_input(chunk)
        if sha256_text(text) != chunk["embedding_input_sha256"]:
            raise SystemExit(f"embedding input drift: {chunk['chunk_id']}")
        job = {
            "schema_version": "1.0",
            "job_id": f"embed:{chunk['chunk_id']}",
            "chunk_id": chunk["chunk_id"],
            "chunk_sha256": chunk["chunk_sha256"],
            "embedding_input": text,
            "embedding_input_sha256": chunk["embedding_input_sha256"],
            "profile_id": chunk["profile_id"],
            "provider": "google-gemini",
            "model": profile["model"],
            "dimensions": profile["dimensions"],
            "vector_space_id": profile["vector_space_id"],
            "status": "planned",
            "embedding": None,
            "external_call_performed": False,
            "incremental_usd": 0,
        }
        job["job_sha256"] = sha256_json(job)
        jobs.append(job)
    output_dir = args.root / "embedding-jobs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "gemini-embedding-jobs.jsonl"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(
        "".join(json.dumps(job, ensure_ascii=False, separators=(",", ":")) + "\n" for job in jobs),
        encoding="utf-8",
    )
    temporary.replace(output)
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "chunk_manifest_sha256": chunk_manifest["summary_sha256"],
        "job_count": len(jobs),
        "profile_id": "section-aware-512-100-v1",
        "provider": "google-gemini",
        "model": profile["model"],
        "dimensions": profile["dimensions"],
        "vector_space_id": profile["vector_space_id"],
        "status": "planned_no_external_calls",
        "external_calls": 0,
        "incremental_usd": 0,
        "portable_paths": True,
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    manifest_path = output_dir / "manifest.json"
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    print(json.dumps({"jobs": str(output), "manifest": str(manifest_path), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
