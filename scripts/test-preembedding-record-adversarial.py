#!/usr/bin/env python3
"""Prove the record validator rejects recomputed-hash staging tampering."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rehash(record: dict, manifest: dict, entry_id: str) -> None:
    record.pop("record_sha256", None)
    record["record_sha256"] = sha256_json(record)
    for row in manifest["records"]:
        if row["entry_id"] == entry_id:
            row["record_sha256"] = record["record_sha256"]
            break
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = sha256_json(manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    source_records = args.root / "records-candidate"
    manifest_source = json.loads((source_records / "manifest.json").read_text(encoding="utf-8"))
    entries = [row["entry_id"] for row in manifest_source["records"]]
    unresolved = None
    for row in manifest_source["records"]:
        record = json.loads((source_records / row["path"]).read_text(encoding="utf-8"))
        if record["name_resolution"]["terminal_status"] == "unresolved":
            unresolved = record["entry_id"]
            break
    if not unresolved or len(entries) < 2:
        raise SystemExit("adversarial suite needs an unresolved record and two entries")

    cases = {
        "source_quote_hash_drift": entries[0],
        "unresolved_name_injection": unresolved,
        "plate_layout_leakage": entries[0],
        "promotion_write_escape": entries[0],
        "cross_chain_substitution": entries[0],
        "naming_source_projection_drift": entries[0],
    }
    results = []
    with tempfile.TemporaryDirectory(prefix="plant-record-adversarial-") as temporary:
        test_root = Path(temporary) / "preembedding-v1"
        # Large immutable frozen inputs are symlinked; only record staging is copied.
        test_root.mkdir()
        for name in ("integration", "naming", "shards"):
            (test_root / name).symlink_to((args.root / name).resolve(), target_is_directory=True)
        for case_name, entry_id in cases.items():
            destination = test_root / "records-candidate"
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source_records, destination)
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            row = next(item for item in manifest["records"] if item["entry_id"] == entry_id)
            record_path = destination / row["path"]
            record = json.loads(record_path.read_text(encoding="utf-8"))

            if case_name == "source_quote_hash_drift":
                record["sections"][0]["original_text"] += " TAMPERED"
                record["sections"][0]["exact_text_sha256"] = hashlib.sha256(
                    record["sections"][0]["original_text"].encode("utf-8")
                ).hexdigest()
            elif case_name == "unresolved_name_injection":
                record["display_name"] = "臆測名稱"
            elif case_name == "plate_layout_leakage":
                record["sections"][0]["section_type"] = "plate_description"
                record["safety"]["layout_or_plate_claims_approved"] = True
            elif case_name == "promotion_write_escape":
                record["review_status"] = "approved"
                record["safety"]["canonical_write_allowed"] = True
                record["safety"]["canonical_target"] = "data/records/escape.json"
            elif case_name == "cross_chain_substitution":
                other = manifest["records"][1]
                record["provenance"]["candidate_sha256"] = other["candidate_sha256"]
            elif case_name == "naming_source_projection_drift":
                record["name_resolution"]["sources"] = [{
                    "authority": "偽造來源",
                    "url": "https://example.invalid/plant",
                    "query_name": record["book_taxon"]["scientific_name"],
                    "retrieved_at": "2026-08-13T12:00:00+08:00",
                    "assertion_scope": "external_naming_and_occurrence_metadata_only",
                }]

            rehash(record, manifest, entry_id)
            write_json(record_path, record)
            write_json(destination / "manifest.json", manifest)
            completed = subprocess.run(
                ["python3", str(LAB / "scripts/validate-preembedding-records.py"), "--root", str(test_root)],
                cwd=LAB, text=True, capture_output=True, timeout=120,
            )
            rejected = completed.returncode != 0
            results.append({
                "case": case_name,
                "rejected": rejected,
                "validator_output": (completed.stdout or completed.stderr).strip()[-2000:],
            })

    passed = all(item["rejected"] for item in results)
    print(json.dumps({"status": "PASS" if passed else "FAIL", "cases": results}, ensure_ascii=False))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
