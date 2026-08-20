#!/usr/bin/env python3
"""Build and audit the consolidated terminal OCR staging manifest.

The frozen 631-page OCR queues are authoritative. This tool never writes
canonical fulltext, records, chunks, embeddings, indexes, or source PDFs.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import platform
import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
DEFAULT_OUTPUT = DEFAULT_ROOT / "consolidated-ocr-staging-manifest.json"
SHARDS = [f"S{i:02d}" for i in range(1, 9)]
TERMINAL_DISPOSITIONS = {
    "candidate_quality_gain",
    "no_deterministic_quality_gain",
    "no_text_detected",
    "ocr_error",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_json(value: object, *, compact: bool = True) -> str:
    separators = (",", ":") if compact else None
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=separators).encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value))


def apple_engine_version(root: Path) -> str:
    helper = root / "tools/apple-vision-ocr"
    helper_hash = sha256_file(helper) if helper.exists() else "missing"
    product_version = platform.mac_ver()[0] or "unknown"
    completed = subprocess.run(
        ["sw_vers", "-buildVersion"], text=True, capture_output=True, check=False, timeout=10
    )
    build_version = completed.stdout.strip() or "unknown"
    return (
        f"Vision.framework/macOS-{product_version}-{build_version};"
        f"apple-vision-ocr-sha256={helper_hash}"
    )


def verify_receipt_hash(receipt: dict) -> bool:
    stored = receipt.get("receipt_sha256")
    if not valid_sha256(stored):
        return False
    payload = dict(receipt)
    payload.pop("receipt_sha256", None)
    # Existing receipts used json.dumps defaults; retain that exact contract.
    return sha256_json(payload, compact=False) == stored


def normalize_apple(receipt: dict, artifact: Path, engine_version: str) -> dict:
    disposition = receipt.get("staging_disposition") or receipt.get("candidate_disposition")
    input_hash = receipt.get("input_sha256") or receipt.get("render_sha256")
    output_hash = receipt.get("output_sha256") or sha256_json(
        {"lines": receipt.get("lines", []), "text": receipt.get("text", "")}
    )
    return {
        "lane": "apple_vision",
        "artifact": str(artifact),
        "engine": receipt.get("engine"),
        "engine_version": receipt.get("engine_version") or engine_version,
        "input_sha256": input_hash,
        "output_sha256": output_hash,
        "receipt_sha256": receipt.get("receipt_sha256"),
        "staging_disposition": disposition,
        "terminal": disposition in TERMINAL_DISPOSITIONS,
        "receipt_hash_valid": verify_receipt_hash(receipt),
        "review_status": receipt.get("review_status"),
        "error": receipt.get("error"),
    }


def migrate_apple_receipts(root: Path) -> dict:
    """Backfill the terminal/hash contract on legacy Apple receipts only."""
    engine_version = apple_engine_version(root)
    migrated = 0
    already_normalized = 0
    for artifact in sorted(root.glob("shards/S*/maker/ocr-candidates/*.json")):
        receipt = read_json(artifact)
        if all(
            (
                receipt.get("engine_version"),
                receipt.get("input_sha256"),
                receipt.get("output_sha256"),
                receipt.get("staging_disposition"),
                receipt.get("terminal") is True,
            )
        ):
            already_normalized += 1
            continue
        disposition = receipt.get("candidate_disposition")
        if disposition not in TERMINAL_DISPOSITIONS:
            continue
        receipt["engine_version"] = receipt.get("engine_version") or engine_version
        receipt["input_sha256"] = receipt.get("input_sha256") or receipt.get("render_sha256")
        receipt["output_sha256"] = receipt.get("output_sha256") or sha256_json(
            {"lines": receipt.get("lines", []), "text": receipt.get("text", "")}
        )
        receipt["staging_disposition"] = receipt.get("staging_disposition") or disposition
        receipt["terminal"] = True
        receipt.pop("receipt_sha256", None)
        receipt["receipt_sha256"] = sha256_json(receipt, compact=False)
        temporary = artifact.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(artifact)
        migrated += 1
    return {"migrated": migrated, "already_normalized": already_normalized}


def normalize_chandra(receipt: dict, artifact: Path) -> dict:
    disposition = receipt.get("staging_disposition") or receipt.get("candidate_disposition")
    output_hash = receipt.get("output_sha256") or sha256_json(
        {
            "chunks": receipt.get("chunks", []),
            "markdown": receipt.get("markdown", ""),
            "page_box": receipt.get("page_box"),
            "raw_html": receipt.get("raw_html", ""),
            "token_count": receipt.get("token_count"),
        }
    )
    return {
        "lane": "chandra_ocr_2",
        "artifact": str(artifact),
        "engine": receipt.get("engine"),
        "engine_version": receipt.get("engine_version"),
        "input_sha256": receipt.get("input_sha256"),
        "output_sha256": output_hash,
        "receipt_sha256": receipt.get("receipt_sha256"),
        "staging_disposition": disposition,
        "terminal": disposition in TERMINAL_DISPOSITIONS,
        "receipt_hash_valid": verify_receipt_hash(receipt),
        "review_status": receipt.get("review_status"),
        "error": receipt.get("error"),
    }


def candidate_path(root: Path, row: dict, lane: str) -> Path:
    directory = "chandra-ocr-candidates" if lane == "chandra" else "ocr-candidates"
    return (
        root
        / "shards"
        / row["owner_shard"]
        / "maker"
        / directory
        / f"{row['source_id']}__p{row['pdf_page']:04d}.json"
    )


def load_queues(root: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    issues: list[str] = []
    for shard in SHARDS:
        path = root / "shards" / shard / "inputs/ocr-pages.jsonl"
        if not path.exists():
            issues.append(f"missing_queue:{shard}")
            continue
        for row in read_jsonl(path):
            if row.get("owner_shard") != shard:
                issues.append(f"owner_shard_mismatch:{shard}:{row.get('source_id')}:{row.get('pdf_page')}")
            rows.append(row)
    rows.sort(key=lambda row: (row["source_id"], row["pdf_page"]))
    keys = [(row["source_id"], row["pdf_page"]) for row in rows]
    duplicates = [key for key, count in Counter(keys).items() if count > 1]
    issues.extend(f"duplicate_queue_page:{source_id}:{page}" for source_id, page in duplicates)
    if len(rows) != 631:
        issues.append(f"queue_count_expected_631_got_{len(rows)}")
    return rows, issues


def build_manifest(root: Path) -> dict:
    rows, global_issues = load_queues(root)
    chandra_status_path = root / "chandra-ocr-batch-status.json"
    chandra_status = read_json(chandra_status_path) if chandra_status_path.exists() else {}
    qualification = chandra_status.get("qualification") or {}
    qualification_status = qualification.get("status") or "not_run"
    prefer_chandra = qualification_status == "pass"
    apple_version = apple_engine_version(root)
    pages = []
    dispositions: Counter[str] = Counter()
    lanes: Counter[str] = Counter()

    for row in rows:
        key = f"{row['source_id']}::p{row['pdf_page']:04d}"
        apple_path = candidate_path(root, row, "apple")
        chandra_path = candidate_path(root, row, "chandra")
        selected = None
        page_issues: list[str] = []

        if prefer_chandra and chandra_path.exists():
            selected = normalize_chandra(read_json(chandra_path), chandra_path)
        elif apple_path.exists():
            selected = normalize_apple(read_json(apple_path), apple_path, apple_version)
        elif prefer_chandra and chandra_path.exists():
            selected = normalize_chandra(read_json(chandra_path), chandra_path)

        if selected is None:
            selected = {
                "lane": None,
                "artifact": None,
                "engine": None,
                "engine_version": None,
                "input_sha256": None,
                "output_sha256": None,
                "receipt_sha256": None,
                "staging_disposition": "pending",
                "terminal": False,
                "receipt_hash_valid": False,
                "review_status": None,
                "error": None,
            }
            page_issues.append("missing_candidate")

        for field in ("engine", "engine_version"):
            if selected.get("terminal") and not selected.get(field):
                page_issues.append(f"missing_{field}")
        for field in ("input_sha256", "output_sha256", "receipt_sha256"):
            if selected.get("terminal") and not valid_sha256(selected.get(field)):
                page_issues.append(f"invalid_{field}")
        if selected.get("terminal") and not selected.get("receipt_hash_valid"):
            page_issues.append("receipt_hash_mismatch")
        if selected.get("staging_disposition") not in TERMINAL_DISPOSITIONS | {"pending"}:
            page_issues.append("unknown_staging_disposition")
        terminal = bool(selected.get("terminal") and not page_issues)
        selected["terminal"] = terminal
        dispositions[selected["staging_disposition"]] += 1
        lanes[selected["lane"] or "none"] += 1

        pages.append(
            {
                "page_id": key,
                "source_id": row["source_id"],
                "volume": row["volume"],
                "pdf_page": row["pdf_page"],
                "owner_shard": row["owner_shard"],
                "queue_source_text_sha256": row["text_sha256"],
                **selected,
                "issues": page_issues,
            }
        )

    terminal_count = sum(page["terminal"] for page in pages)
    invalid_count = sum(bool(page["issues"] and page["staging_disposition"] != "pending") for page in pages)
    summary = {
        "queue_total": len(pages),
        "terminal_count": terminal_count,
        "pending_count": len(pages) - terminal_count,
        "invalid_terminal_count": invalid_count,
        "coverage_fraction": round(terminal_count / max(len(pages), 1), 6),
        "qualification_status": qualification_status,
        "preferred_lane": "chandra_ocr_2" if prefer_chandra else "apple_vision",
        "dispositions": dict(sorted(dispositions.items())),
        "lanes": dict(sorted(lanes.items())),
        "global_issues": global_issues,
        "complete": len(pages) == 631 and terminal_count == 631 and not global_issues,
    }
    content_sha256 = sha256_json({"summary": summary, "pages": pages})
    return {
        "schema_version": "1.0",
        "generated_at": now(),
        "root": str(root),
        "summary": summary,
        "content_sha256": content_sha256,
        "pages": pages,
    }


def comparable(manifest: dict) -> dict:
    value = dict(manifest)
    value.pop("generated_at", None)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--normalize-apple-receipts", action="store_true")
    args = parser.parse_args()

    lock_path = args.root / "ocr-worker.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if args.normalize_apple_receipts else fcntl.LOCK_SH)
        migration = (
            migrate_apple_receipts(args.root)
            if args.normalize_apple_receipts
            else {"migrated": 0, "already_normalized": 0}
        )
        manifest = build_manifest(args.root)
        if args.write:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            temporary.replace(args.output)
        if args.check:
            if not args.output.exists():
                raise SystemExit(f"missing manifest: {args.output}")
            existing = read_json(args.output)
            if comparable(existing) != comparable(manifest):
                raise SystemExit("manifest is stale or inconsistent with current OCR staging")

    print(json.dumps({"output": str(args.output), "migration": migration, **manifest["summary"]}, ensure_ascii=False))
    if args.require_complete and not manifest["summary"]["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
