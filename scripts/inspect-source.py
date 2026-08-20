#!/usr/bin/env python3
"""Token-light PDF inventory and embedded-text sampling for the source volumes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def pdf_metadata(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in run("pdfinfo", str(path)).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def sample_page(path: Path, page: int) -> dict[str, object]:
    text = run("pdftotext", "-f", str(page), "-l", str(page), "-layout", str(path), "-")
    compact = re.sub(r"\s+", " ", text).strip()
    return {
        "pdf_page": page,
        "characters": len(compact),
        "words": len(compact.split()),
        "preview": compact[:240],
    }


def inspect(path: Path) -> dict[str, object]:
    metadata = pdf_metadata(path)
    pages = int(metadata["Pages"])
    candidates = sorted({1, min(10, pages), max(1, pages // 4), max(1, pages // 2), max(1, pages - 10)})
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "pages": pages,
        "title": metadata.get("Title"),
        "encrypted": metadata.get("Encrypted"),
        "page_size": metadata.get("Page size"),
        "samples": [sample_page(path, page) for page in candidates],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=Path("/Volumes/NO NAME"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not args.source_dir.is_dir():
        raise SystemExit(f"Source directory is unavailable: {args.source_dir}")

    pdfs = sorted(path for path in args.source_dir.glob("*.pdf") if not path.name.startswith("._"))
    if not pdfs:
        raise SystemExit(f"No PDF files found in: {args.source_dir}")

    result = {"source_dir": str(args.source_dir.resolve()), "files": [inspect(path) for path in pdfs]}
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
