#!/usr/bin/env python3
"""Drop only unmaterialized sections from otherwise valid v2 receipts.

This changed strategy is intentionally narrower than a model retry. It is
allowed only when every declared failure is section-scoped, at least two other
sections already have exact deterministic locators, and the retained draft
passes the normal materializer from scratch. No line number is clamped or
guessed and no content is added.

author: Nio (Master)
date: 2026-08-20
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def object_hash(value: dict, field: str) -> str:
    return sha256_text(canonical_json({key: item for key, item in value.items() if key != field}))


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_runner():
    path = LAB / "scripts/run-local-preembedding-continuations.py"
    spec = importlib.util.spec_from_file_location("continuation_runner", path)
    if spec is None or spec.loader is None:
        raise SystemExit("unable to load continuation runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--lane", choices=["continuation-v2", "recovery"], default="continuation-v2")
    parser.add_argument("--package-id", action="append", default=[])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = args.root
    runner = load_runner()
    package_file = {
        "continuation-v2": "continuation-work-packages-v2.jsonl",
        "recovery": "content-recovery-work-packages.jsonl",
    }[args.lane]
    receipt_directory = {
        "continuation-v2": "continuation-v2-maker-receipts",
        "recovery": "recovery-maker-receipts",
    }[args.lane]
    attempt_directory = {
        "continuation-v2": "continuation-v2-attempts",
        "recovery": "recovery-attempts",
    }[args.lane]
    status_file = {
        "continuation-v2": "continuation-v2-batch-status.json",
        "recovery": "recovery-batch-status.json",
    }[args.lane]
    prompt_version = {
        "continuation-v2": "plant-structure-line-coordinates-v2",
        "recovery": "plant-structure-continuation-line-anchors-v1",
    }[args.lane]
    packages = {
        item["package_id"]: item
        for item in read_jsonl(root / "structure" / package_file)
    }
    pages = runner.load_pages(root)
    receipt_dir = root / "structure" / receipt_directory
    repairs: list[dict] = []
    prior_pass_count = 0

    for path in sorted(receipt_dir.glob("*.json")):
        prior = read_json(path)
        if prior.get("deterministic_status") == "pass":
            prior_pass_count += 1
            continue
        package_id = prior.get("package_id")
        if args.package_id and package_id not in set(args.package_id):
            continue
        package = packages.get(package_id)
        declared_errors = prior.get("errors")
        prior_locators = prior.get("section_source_locators")
        if package is None or not isinstance(declared_errors, list) or not declared_errors:
            raise SystemExit(f"unsafe repair candidate identity: {package_id}")
        if any(not isinstance(item, str) or not item.startswith("section_") for item in declared_errors):
            raise SystemExit(f"non-section error cannot be repaired: {package_id}")
        if not isinstance(prior_locators, list) or len(prior_locators) < 2:
            raise SystemExit(f"insufficient valid sections for repair: {package_id}")

        failed_indexes = {
            int(match.group(1))
            for item in declared_errors
            if (match := re.match(r"section_(\d+):", item))
        }
        valid_indexes = sorted(
            item["section_index"] for item in prior_locators
            if item["section_index"] not in failed_indexes
        )
        original_sections = prior.get("draft", {}).get("sections", [])
        if len(set(valid_indexes)) != len(valid_indexes) or max(valid_indexes) >= len(original_sections):
            raise SystemExit(f"invalid prior locator indexes: {package_id}")
        draft = copy.deepcopy(prior["draft"])
        retained = []
        for index in valid_indexes:
            section = copy.deepcopy(original_sections[index])
            line_range = section.pop("source_line_range", None)
            section.pop("exact_source_quote", None)
            if not isinstance(line_range, list) or len(line_range) != 2:
                raise SystemExit(f"retained section lacks line range: {package_id}/s{index}")
            section["source_line_start"], section["source_line_end"] = line_range
            retained.append(section)
        draft["sections"] = retained
        draft.setdefault("warnings", []).append(
            "Deterministic changed strategy dropped only sections that lacked an exact source locator; no line number was repaired or guessed."
        )
        errors, locators = runner.materialize_and_validate(draft, package, pages)
        if errors or len(locators) != len(retained):
            raise SystemExit(f"repaired receipt did not revalidate: {package_id}:{errors}")

        repair_payload = {
            "schema_version": "1.0",
            "package_id": package_id,
            "package_sha256": package["package_sha256"],
            "model": "deterministic-local-repair-no-model-call",
            "prompt_version": prompt_version,
            "source_receipt_sha256": prior["receipt_sha256"],
            "source_attempt_sha256": prior.get("attempt_sha256"),
            "repair_strategy": "drop-unmaterialized-sections-v1",
            "dropped_section_indexes": sorted(set(range(len(original_sections))) - set(valid_indexes)),
            "source_errors": declared_errors,
            "raw_response": canonical_json({
                "package_id": package_id,
                "retained_source_line_coordinates": [
                    {
                        "section_type": item["section_type"],
                        "pdf_page": item["pdf_page"],
                        "source_line_range": item["source_line_range"],
                    }
                    for item in draft["sections"]
                ],
            }),
            "parse_or_validation_errors": [],
        }
        repair_payload["raw_response_sha256"] = sha256_text(repair_payload["raw_response"])
        repair_payload["attempt_sha256"] = object_hash(repair_payload, "attempt_sha256")
        repaired = copy.deepcopy(prior)
        repaired.update({
            "model": "deterministic-local-repair-no-model-call",
            "attempt_sha256": repair_payload["attempt_sha256"],
            "raw_response_sha256": repair_payload["raw_response_sha256"],
            "elapsed_seconds": 0.0,
            "deterministic_status": "pass",
            "errors": [],
            "section_source_locators": locators,
            "draft": draft,
            "changed_strategy_repair": {
                "strategy": repair_payload["repair_strategy"],
                "source_receipt_sha256": prior["receipt_sha256"],
                "source_attempt_sha256": prior.get("attempt_sha256"),
                "dropped_section_indexes": repair_payload["dropped_section_indexes"],
                "source_errors": declared_errors,
                "external_model_calls": 0,
                "content_added": False,
                "line_numbers_guessed_or_clamped": False,
            },
        })
        repaired["receipt_sha256"] = object_hash(repaired, "receipt_sha256")
        repairs.append({
            "path": path,
            "prior": prior,
            "attempt": repair_payload,
            "receipt": repaired,
        })

    summary = {
        "status": "PASS",
        "repair_candidates": len(repairs),
        "package_ids": [item["receipt"]["package_id"] for item in repairs],
        "write_requested": args.write,
        "external_model_calls": 0,
        "content_added": False,
    }
    if args.package_id and set(args.package_id) != set(summary["package_ids"]):
        missing = sorted(set(args.package_id) - set(summary["package_ids"]))
        raise SystemExit(f"requested repair packages were not safely repairable: {missing}")
    if args.write:
        for item in repairs:
            package_id = item["receipt"]["package_id"]
            attempt_dir = root / "structure" / attempt_directory / package_id.replace(":", "__")
            write_json(attempt_dir / f"prior-receipt-{item['prior']['receipt_sha256']}.json", item["prior"])
            write_json(attempt_dir / f"attempt-{item['attempt']['attempt_sha256']}.json", item["attempt"])
            write_json(item["path"], item["receipt"])

        status_path = root / "checks" / status_file
        status = read_json(status_path)
        remaining = len(packages) - prior_pass_count - len(repairs)
        status.update({
            "started_at": now(),
            "status": "complete" if remaining == 0 else "partial",
            "valid_receipts_before_run": prior_pass_count,
            "processed_this_run": len(repairs),
            "pass_this_run": len(repairs),
            "needs_review_this_run": 0,
            "remaining": remaining,
            "current_package_id": None,
            "errors": [],
            "updated_at": now(),
        })
        status["status_sha256"] = object_hash(status, "status_sha256")
        write_json(status_path, status)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
