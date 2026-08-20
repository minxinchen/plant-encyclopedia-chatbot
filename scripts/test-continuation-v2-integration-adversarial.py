#!/usr/bin/env python3
"""Synthetic full-chain and adversarial tests for continuation-v2 integration."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
ROOT = LAB / "data/candidates/preembedding-v1"
BUILD = LAB / "scripts/build-continuation-v2-integration.py"
VALIDATE = LAB / "scripts/validate-continuation-v2-integration.py"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def object_hash(value: dict, field: str) -> str:
    return digest({key: item for key, item in value.items() if key != field})


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(command: list[str], expect_pass: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=300)
    if expect_pass != (completed.returncode == 0):
        raise SystemExit((completed.stdout or completed.stderr)[-5000:])
    return completed


def unique_line(page: dict) -> tuple[int, str, int]:
    for number, line in enumerate(page["text"].splitlines(), 1):
        if line.strip() and page["text"].find(line) == page["text"].rfind(line):
            return number, line, page["text"].find(line)
    raise SystemExit(f"no unique line:{page['source_id']}/p{page['pdf_page']}")


def synthesize_receipts(root: Path) -> None:
    packages = read_jsonl(root / "structure/continuation-work-packages-v2.jsonl")
    pages = {}
    for path in sorted((root / "shards").glob("S*/inputs/pages.jsonl")):
        for page in read_jsonl(path):
            pages[(page["source_id"], page["pdf_page"])] = page
    receipt_dir = root / "structure/continuation-v2-maker-receipts"
    attempt_root = root / "structure/continuation-v2-attempts"
    if receipt_dir.exists():
        shutil.rmtree(receipt_dir)
    if attempt_root.exists():
        shutil.rmtree(attempt_root)
    receipt_dir.mkdir(parents=True)
    attempt_root.mkdir(parents=True)
    for package in packages:
        page = pages[(package["source_id"], package["pdf_pages"][0])]
        line_number, quote, char_start = unique_line(page)
        raw_draft = {
            "package_id": package["package_id"], "parent_entry_id": package["parent_entry_id"],
            "book_taxon": {"scientific_name_candidate": package["book_taxon_candidate"]},
            "display_name": None, "name_resolution": {"status": "unresolved", "sources": []},
            "sections": [{
                "section_type": "description", "pdf_page": page["pdf_page"],
                "source_line_start": line_number, "source_line_end": line_number,
            }],
            "review_status": "machine_extracted", "warnings": [],
        }
        raw_response = json.dumps(raw_draft, ensure_ascii=False)
        attempt = {
            "schema_version": "1.0", "package_id": package["package_id"],
            "package_sha256": package["package_sha256"], "model": "synthetic-local-test",
            "prompt_version": "plant-structure-line-coordinates-v2",
            "raw_response": raw_response,
            "raw_response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
            "parse_or_validation_errors": [],
        }
        attempt["attempt_sha256"] = object_hash(attempt, "attempt_sha256")
        materialized = dict(raw_draft)
        materialized["sections"] = [{
            "section_type": "description", "pdf_page": page["pdf_page"],
            "source_line_range": [line_number, line_number], "exact_source_quote": quote,
        }]
        locator = {
            "source_id": package["source_id"], "volume": package["volume"],
            "pdf_page": page["pdf_page"],
            "source_pdf_sha256": package["source_locators"][0]["source_pdf_sha256"],
            "char_start": char_start, "char_end": char_start + len(quote),
            "source_line_start": line_number, "source_line_end": line_number,
            "page_text_sha256": page["text_sha256"],
            "exact_text_sha256": hashlib.sha256(quote.encode()).hexdigest(),
            "section_index": 0, "section_type": "description",
        }
        receipt = {
            "schema_version": "2.0", "package_id": package["package_id"],
            "parent_entry_id": package["parent_entry_id"], "child_entry_id": package["child_entry_id"],
            "recovered_entry_id": None, "work_id": package["work_id"],
            "owner_shard": package["owner_shard"], "package_sha256": package["package_sha256"],
            "model": "synthetic-local-test", "prompt_version": "plant-structure-line-coordinates-v2",
            "attempt_sha256": attempt["attempt_sha256"],
            "raw_response_sha256": attempt["raw_response_sha256"], "elapsed_seconds": 0,
            "external_model_calls": 0, "incremental_usd": 0,
            "name_resolution_status": "unresolved", "layout_or_plate_claims_approved": False,
            "deterministic_status": "pass", "errors": [],
            "section_source_locators": [locator], "draft": materialized,
        }
        receipt["receipt_sha256"] = object_hash(receipt, "receipt_sha256")
        attempt_dir = attempt_root / package["package_id"].replace(":", "__")
        write_json(attempt_dir / f"attempt-{attempt['attempt_sha256']}.json", attempt)
        write_json(receipt_dir / f"{package['package_id'].replace(':', '__')}.json", receipt)


def mutate_manifest(root: Path, mutation) -> None:
    path = root / "integration-v2/embedding-ready-child-candidate-manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    for candidate in value.get("candidates", []):
        candidate["candidate_sha256"] = object_hash(candidate, "candidate_sha256")
    value["manifest_sha256"] = object_hash(value, "manifest_sha256")
    write_json(path, value)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="continuation-v2-integration-test-") as temporary:
        root = Path(temporary) / "preembedding-v1"
        (root / "boundary-evidence-v1").mkdir(parents=True)
        (root / "structure").mkdir()
        shutil.copy2(ROOT / "boundary-evidence-v1/boundary-overlay-plan.json", root / "boundary-evidence-v1")
        shutil.copy2(ROOT / "structure/continuation-work-packages-v2.jsonl", root / "structure")
        shutil.copy2(ROOT / "structure/continuation-work-packages-v2-manifest.json", root / "structure")
        shutil.copy2(ROOT / "source-receipt.json", root / "source-receipt.json")
        (root / "shards").symlink_to((ROOT / "shards").resolve(), target_is_directory=True)
        synthesize_receipts(root)
        run(["python3", str(BUILD), "--root", str(root), "--require-complete"])
        baseline = run(["python3", str(VALIDATE), "--root", str(root), "--require-complete"])
        result = json.loads(baseline.stdout.splitlines()[-1])
        if result["package_checks_passed"] != 46 or result["embedding_ready_child_candidates"] != 34:
            raise SystemExit("synthetic full gate cardinality mismatch")

        manifest_path = root / "integration-v2/embedding-ready-child-candidate-manifest.json"
        original_manifest = manifest_path.read_bytes()
        cases = []
        for name, mutation in [
            ("taiwan-name-invention", lambda value: value["candidates"][0].update({"display_name": "虛構臺灣名"})),
            ("plate-injection", lambda value: value["candidates"][0]["sections"][0].update({"section_type": "plate_description"})),
            ("source-locator-drift", lambda value: value["candidates"][0]["sections"][0]["source_locators"][0].update({"char_end": value["candidates"][0]["sections"][0]["source_locators"][0]["char_end"] + 1})),
        ]:
            manifest_path.write_bytes(original_manifest)
            mutate_manifest(root, mutation)
            rejected = run(["python3", str(VALIDATE), "--root", str(root)], expect_pass=False)
            cases.append({"case": name, "rejected": rejected.returncode != 0})
        manifest_path.write_bytes(original_manifest)

        receipt_files = sorted((root / "structure/continuation-v2-maker-receipts").glob("*.json"))
        removed = receipt_files[-1]
        removed_bytes = removed.read_bytes()
        removed.unlink()
        run(["python3", str(BUILD), "--root", str(root)])
        partial = run(["python3", str(VALIDATE), "--root", str(root)])
        partial_result = json.loads(partial.stdout.splitlines()[-1])
        if partial_result["embedding_ready_child_candidates"] != 0 or partial_result["complete"]:
            raise SystemExit("missing receipt did not close the global release gate")
        cases.append({"case": "missing-receipt-global-gate", "rejected": True})
        removed.write_bytes(removed_bytes)

        print(json.dumps({
            "status": "PASS", "synthetic_receipts": 46, "released_child_candidates": 34,
            "independent_complete_validation": True, "adversarial_cases": cases,
            "partial_gate_candidate_count": 0,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
