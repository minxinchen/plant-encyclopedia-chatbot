#!/usr/bin/env python3
"""Extract selected PDF pages into traceable JSON without performing OCR."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def parse_pages(spec: str) -> list[int]:
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"Invalid page range: {part}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    if not pages or min(pages) < 1:
        raise ValueError("At least one positive PDF page is required")
    return sorted(pages)


def extract(source: Path, page: int) -> str:
    return subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(source), "-"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.rstrip("\f\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--pages", required=True, help="Example: 30-32,48-53,79,85")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not args.source.is_file():
        raise SystemExit(f"Source PDF is unavailable: {args.source}")

    records = []
    for page in parse_pages(args.pages):
        text = extract(args.source, page)
        records.append(
            {
                "source_id": args.source_id,
                "source_path": str(args.source.resolve()),
                "pdf_page": page,
                "extraction_method": "embedded_text_pdftotext_layout",
                "character_count": len(text),
                "text": text,
            }
        )

    result = {"schema_version": "1.0", "pages": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
