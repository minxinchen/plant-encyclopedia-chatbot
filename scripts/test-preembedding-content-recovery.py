#!/usr/bin/env python3
"""Synthetic end-to-end proof for the nine packages recovering eight holds."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(command: list[str]) -> dict:
    completed = subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=180)
    if completed.returncode != 0:
        raise SystemExit((completed.stdout or completed.stderr).strip())
    return json.loads(completed.stdout.strip().splitlines()[-1])


def unique_source_line(page: dict) -> tuple[int, str, int]:
    for line_number, line in enumerate(page["text"].splitlines(), 1):
        if line.strip() and page["text"].find(line) == page["text"].rfind(line):
            return line_number, line, page["text"].find(line)
    raise SystemExit(f"no unique source line on {page['source_id']}/p{page['pdf_page']}")


def make_receipt(package: dict, page: dict) -> dict:
    line_number, quote, char_start = unique_source_line(page)
    locator = {
        "source_id": package["source_id"],
        "volume": package["volume"],
        "pdf_page": page["pdf_page"],
        "source_pdf_sha256": next(
            item["source_pdf_sha256"] for item in package["source_locators"]
            if item["pdf_page"] == page["pdf_page"]
        ),
        "char_start": char_start,
        "char_end": char_start + len(quote),
        "source_line_start": line_number,
        "source_line_end": line_number,
        "page_text_sha256": page["text_sha256"],
        "exact_text_sha256": sha256_text(quote),
        "section_index": 0,
        "section_type": "description",
    }
    draft = {
        "package_id": package["package_id"],
        "parent_entry_id": package["parent_entry_id"],
        "book_taxon": {
            "scientific_name_candidate": package["book_taxon_candidate"],
            "authorship_candidate": None,
            "aliases_candidates": [],
        },
        "display_name": None,
        "name_resolution": {"status": "unresolved", "sources": []},
        "sections": [{
            "section_type": "description",
            "pdf_page": page["pdf_page"],
            "source_line_range": [line_number, line_number],
            "exact_source_quote": quote,
            "normalized_text_candidate": None,
            "zh_tw_rendering_candidate": None,
            "warnings": [],
        }],
        "review_status": "machine_extracted",
        "warnings": [],
    }
    receipt = {
        "schema_version": "1.0",
        "package_id": package["package_id"],
        "parent_entry_id": package["parent_entry_id"],
        "recovered_entry_id": package["recovered_entry_id"],
        "work_id": package["work_id"],
        "owner_shard": package["owner_shard"],
        "package_sha256": package["package_sha256"],
        "model": "synthetic-local-test",
        "prompt_version": "plant-structure-continuation-line-anchors-v1",
        "elapsed_seconds": 0,
        "external_model_calls": 0,
        "incremental_usd": 0,
        "name_resolution_status": "unresolved",
        "layout_or_plate_claims_approved": False,
        "deterministic_status": "pass",
        "errors": [],
        "section_source_locators": [locator],
        "draft": draft,
    }
    receipt["receipt_sha256"] = sha256_text(canonical_json(receipt))
    return receipt


def main() -> None:
    root = DEFAULT_ROOT
    packages = read_jsonl(root / "structure/content-recovery-work-packages.jsonl")
    pages = {}
    for path in sorted((root / "shards").glob("S*/inputs/pages.jsonl")):
        for page in read_jsonl(path):
            pages[(page["source_id"], page["pdf_page"])] = page
    if len(packages) != 9 or len({item["parent_entry_id"] for item in packages}) != 8:
        raise SystemExit("expected nine recovery packages covering eight parents")

    with tempfile.TemporaryDirectory(prefix="preembedding-content-recovery-") as temporary:
        test_root = Path(temporary) / "preembedding-v1"
        test_root.mkdir()
        (test_root / "shards").symlink_to((root / "shards").resolve(), target_is_directory=True)
        for filename in (
            "manifest.json", "source-receipt.json", "batch-status.json",
            "consolidated-ocr-staging-manifest.json",
        ):
            shutil.copy2(root / filename, test_root / filename)
        receipt_dir = test_root / "structure/recovery-maker-receipts"
        receipt_dir.mkdir(parents=True)
        for package in packages:
            page = pages[(package["source_id"], package["pdf_pages"][0])]
            receipt = make_receipt(package, page)
            write_json(receipt_dir / f"{package['package_id'].replace(':', '__')}.json", receipt)

        integration = run([
            "python3", str(LAB / "scripts/validate-preembedding-structure-integration.py"),
            "--root", str(test_root),
        ])
        run([
            "python3", str(LAB / "scripts/validate-preembedding-source-receipt.py"),
            "--root", str(test_root),
        ])
        audit = run([
            "python3", str(LAB / "scripts/build-preembedding-completion-audit.py"),
            "--root", str(test_root),
        ])
        validation = run([
            "python3", str(LAB / "scripts/validate-preembedding-integration-artifacts.py"),
            "--root", str(test_root),
        ])
        dispositions = read_jsonl(test_root / "integration/entry-dispositions.jsonl")
        recovered = [
            item for item in dispositions
            if item["terminal_disposition"] in {
                "page_quality_structure_recovered", "terminal_body_boundaries_recovered",
            }
        ]
        embedding = json.loads(
            (test_root / "integration/embedding-ready-candidate-manifest.json").read_text(encoding="utf-8")
        )
        recovery_candidates = [item for item in embedding["candidates"] if item.get("recovery_package")]
        requirement = next(
            item for item in audit["requirements"]
            if item["requirement_id"] == "content-hold-recovery-complete"
        )
        if integration["content_recovery_receipts_passed"] != 9:
            raise SystemExit("synthetic integration did not pass nine recovery receipts")
        if integration["content_hold_parents_recovered"] != 8 or integration["unresolved_content_holds"] != 0:
            raise SystemExit("synthetic integration did not recover all eight content holds")
        if len(recovered) != 8 or len(recovery_candidates) != 9:
            raise SystemExit("synthetic recovery terminal/candidate cardinality mismatch")
        if any(item["layout_or_plate_claims_approved"] is not False for item in recovery_candidates):
            raise SystemExit("synthetic recovery self-approved layout or plate claims")
        if any(section["section_type"] == "plate_description" for item in recovery_candidates for section in item["sections"]):
            raise SystemExit("plate section leaked into recovery candidates")
        if requirement["status"] != "achieved":
            raise SystemExit("completion audit did not recognize content-hold recovery")

        affected_package = packages[-1]
        (receipt_dir / f"{affected_package['package_id'].replace(':', '__')}.json").unlink()
        incomplete = run([
            "python3", str(LAB / "scripts/validate-preembedding-structure-integration.py"),
            "--root", str(test_root),
        ])
        incomplete_dispositions = read_jsonl(test_root / "integration/entry-dispositions.jsonl")
        affected = next(item for item in incomplete_dispositions if item["entry_id"] == affected_package["parent_entry_id"])
        if affected["terminal"] or affected["embedding_ready_candidate"]:
            raise SystemExit("a recovery parent with a missing receipt was prematurely terminal")
        if incomplete["unresolved_content_holds"] != 1:
            raise SystemExit("missing recovery receipt did not restore exactly one unresolved hold")

        print(json.dumps({
            "status": "PASS",
            "synthetic_receipts": 9,
            "recovered_parents": 8,
            "recovery_candidates": len(recovery_candidates),
            "unresolved_content_holds": 0,
            "plate_excluded": True,
            "missing_receipt_parent_nonterminal": True,
            "independent_validation": validation["status"],
            "post_removal_recovery_passes": incomplete["content_recovery_receipts_passed"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
