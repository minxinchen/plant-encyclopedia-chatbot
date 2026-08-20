#!/usr/bin/env python3
"""Prove the integration validator rejects recomputed-hash staging tampering.

All mutations occur in temporary copies and are removed automatically.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
VALIDATOR = LAB / "scripts/validate-preembedding-integration-artifacts.py"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rehash(value: dict, field: str) -> None:
    value.pop(field, None)
    value[field] = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def first_page(root: Path, source_id: str, pdf_page: int) -> dict:
    for path in sorted((root / "shards").glob("S*/inputs/pages.jsonl")):
        for page in read_jsonl(path):
            if page["source_id"] == source_id and page["pdf_page"] == pdf_page:
                return page
    raise ValueError(f"missing frozen page: {source_id}/p{pdf_page}")


def mutate_taiwan_name(root: Path) -> None:
    path = root / "integration/embedding-ready-candidate-manifest.json"
    manifest = read_json(path)
    candidate = manifest["candidates"][0]
    candidate["display_name"] = "臆測臺灣名"
    rehash(candidate, "candidate_sha256")
    rehash(manifest, "manifest_sha256")
    write_json(path, manifest)


def mutate_partial_entry_locator(root: Path) -> None:
    path = root / "integration/entry-dispositions.jsonl"
    dispositions = read_jsonl(path)
    disposition = dispositions[0]
    locator = disposition["source_locators"][0]
    page = first_page(root, locator["source_id"], locator["pdf_page"])
    if len(page["text"]) < 2:
        raise ValueError("selected adversarial page is unexpectedly empty")
    locator["char_start"] = 1
    locator["exact_text_sha256"] = hashlib.sha256(page["text"][1:locator["char_end"]].encode("utf-8")).hexdigest()
    rehash(disposition, "disposition_sha256")
    write_jsonl(path, dispositions)


def mutate_candidate_quote(root: Path) -> None:
    path = root / "integration/embedding-ready-candidate-manifest.json"
    manifest = read_json(path)
    candidate = manifest["candidates"][0]
    section = candidate["sections"][0]
    quote = section["exact_source_quotes"][0]
    locator = section["source_locators"][0]
    if len(quote) < 2:
        raise ValueError("selected adversarial quote is unexpectedly short")
    section["exact_source_quotes"][0] = quote[1:]
    locator["char_start"] += 1
    locator["exact_text_sha256"] = hashlib.sha256(quote[1:].encode("utf-8")).hexdigest()
    rehash(candidate, "candidate_sha256")
    rehash(manifest, "manifest_sha256")
    write_json(path, manifest)


def mutate_plate_injection(root: Path) -> None:
    path = root / "integration/embedding-ready-candidate-manifest.json"
    manifest = read_json(path)
    candidate = manifest["candidates"][0]
    injected = json.loads(json.dumps(candidate["sections"][0], ensure_ascii=False))
    injected["section_type"] = "plate_description"
    candidate["sections"].append(injected)
    rehash(candidate, "candidate_sha256")
    rehash(manifest, "manifest_sha256")
    write_json(path, manifest)


def mutate_source_pdf_hash(root: Path) -> None:
    path = root / "integration/entry-dispositions.jsonl"
    dispositions = read_jsonl(path)
    dispositions[0]["source_locators"][0]["source_pdf_sha256"] = "0" * 64
    rehash(dispositions[0], "disposition_sha256")
    write_jsonl(path, dispositions)


def run_case(source: Path, name: str, mutate: Callable[[Path], None], expected: str) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"preembedding-{name}-") as temporary:
        root = Path(temporary) / "preembedding-v1"
        shutil.copytree(source, root)
        mutate(root)
        completed = subprocess.run(
            ["python3", str(VALIDATOR), "--root", str(root)],
            cwd=LAB,
            text=True,
            capture_output=True,
            timeout=60,
        )
        output = (completed.stdout + completed.stderr).strip()
        passed = completed.returncode != 0 and expected in output
        if not passed:
            raise SystemExit(
                f"FAIL adversarial case {name}: returncode={completed.returncode} expected={expected!r} output={output[-800:]!r}"
            )
        return {"case": name, "rejected": True, "expected_error": expected}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    cases = [
        ("taiwan-name-invention", mutate_taiwan_name, "candidate invented a Taiwan name"),
        ("partial-entry-locator", mutate_partial_entry_locator, "disposition locator does not cover its full frozen page"),
        ("candidate-quote-drift", mutate_candidate_quote, "candidate sections drifted from validated maker evidence"),
        ("plate-injection", mutate_plate_injection, "plate section entered embedding-ready text candidate"),
        ("source-pdf-hash-drift", mutate_source_pdf_hash, "source PDF hash mismatch"),
    ]
    results = [run_case(args.root, name, mutate, expected) for name, mutate, expected in cases]
    print(json.dumps({"status": "PASS", "cases": len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
