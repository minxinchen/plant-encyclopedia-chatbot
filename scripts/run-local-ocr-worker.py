#!/usr/bin/env python3
"""Create Apple Vision OCR overlays for frozen OCR-queue pages.

The overlay never replaces canonical fulltext. Every result remains a candidate
until a deterministic/layout reviewer accepts it.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def apple_engine_version(helper: Path) -> str:
    product_version = platform.mac_ver()[0] or "unknown"
    completed = subprocess.run(
        ["sw_vers", "-buildVersion"], text=True, capture_output=True, check=False, timeout=10
    )
    build_version = completed.stdout.strip() or "unknown"
    return (
        f"Vision.framework/macOS-{product_version}-{build_version};"
        f"apple-vision-pdf-ocr-sha256={sha256_file(helper)}"
    )


def alpha_ratio(text: str) -> float:
    return round(sum(character.isalpha() for character in text) / max(len(text), 1), 4)


def compile_helper(root: Path) -> Path:
    source = LAB / "scripts/apple-vision-pdf-ocr.swift"
    binary = root / "tools/apple-vision-pdf-ocr"
    binary.parent.mkdir(parents=True, exist_ok=True)
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        subprocess.run(["swiftc", str(source), "-o", str(binary)], check=True, timeout=180)
    return binary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("shard", choices=[f"S{i:02d}" for i in range(1, 9)])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--page", type=int)
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    source_manifest = json.loads((LAB / "data/source-manifest.json").read_text(encoding="utf-8"))
    source_paths = {item["source_id"]: Path(item["path"]) for item in source_manifest["files"]}
    shard_root = args.root / "shards" / args.shard
    queue = read_jsonl(shard_root / "inputs/ocr-pages.jsonl")
    output_dir = shard_root / "maker/ocr-candidates"
    output_dir.mkdir(parents=True, exist_ok=True)
    pending = queue if args.force else [
        row for row in queue
        if not (output_dir / f"{row['source_id']}__p{row['pdf_page']:04d}.json").exists()
    ]
    if args.page is not None:
        pending = [row for row in pending if row["pdf_page"] == args.page]
    selected = pending[: args.limit]
    helper = compile_helper(args.root)
    engine_version = apple_engine_version(helper)
    lock_path = args.root / "ocr-worker.lock"
    results = []

    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for row in selected:
            started = time.monotonic()
            source = source_paths[row["source_id"]]
            error = None
            image_sha256 = None
            try:
                completed = subprocess.run(
                    [str(helper), str(source), str(row["pdf_page"]), str(args.dpi)],
                    check=True,
                    text=True,
                    capture_output=True,
                    timeout=300,
                )
                vision = json.loads(completed.stdout)
                image_sha256 = vision["inputSha256"]
                text = vision["text"]
                if not text.strip():
                    disposition = "no_text_detected"
                elif len(text) > row["character_count"] * 1.2 and alpha_ratio(text) >= row["alpha_ratio"]:
                    disposition = "candidate_quality_gain"
                else:
                    disposition = "no_deterministic_quality_gain"
            except Exception as exc:
                vision = {
                    "engine": "Apple Vision VNRecognizeTextRequest accurate / PDFKit render",
                    "languages": ["de-DE", "en-US"],
                    "lines": [],
                }
                image_sha256 = None
                text = ""
                disposition = "ocr_error"
                error = f"{type(exc).__name__}:{str(exc)[:500]}"
            output_sha256 = sha256_json({"lines": vision["lines"], "text": text})
            receipt = {
                "schema_version": "1.0",
                "source_id": row["source_id"],
                "volume": row["volume"],
                "pdf_page": row["pdf_page"],
                "owner_shard": args.shard,
                "source_pdf": str(source),
                "source_text_sha256": row["text_sha256"],
                "render_dpi": args.dpi,
                "render_sha256": image_sha256,
                "input_sha256": image_sha256,
                "render_pixel_width": vision.get("pixelWidth"),
                "render_pixel_height": vision.get("pixelHeight"),
                "engine": vision["engine"],
                "engine_version": engine_version,
                "languages": vision["languages"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "embedded_character_count": row["character_count"],
                "ocr_character_count": len(text),
                "ocr_alpha_ratio": alpha_ratio(text),
                "candidate_disposition": disposition,
                "staging_disposition": disposition,
                "terminal": True,
                "review_status": "machine_extracted",
                "error": error,
                "external_model_calls": 0,
                "incremental_usd": 0,
                "lines": vision["lines"],
                "text": text,
                "output_sha256": output_sha256,
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            output = output_dir / f"{row['source_id']}__p{row['pdf_page']:04d}.json"
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            results.append({"pdf_page": row["pdf_page"], "disposition": disposition})

    print(json.dumps({
        "shard": args.shard,
        "processed": len(results),
        "remaining": len(pending) - len(results),
        "results": results,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
