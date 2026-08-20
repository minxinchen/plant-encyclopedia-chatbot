#!/usr/bin/env python3
"""Adversarial tests for Google Gem pack source, shard, and secret gates."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
SOURCE = LAB / "data/candidates/preembedding-v1/exports/google-gem/fullbook-beta"
VALIDATOR = LAB / "scripts/validate-fullbook-google-gem-pack.py"


def canonical_sha(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def save_manifest(path: Path, manifest: dict) -> None:
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = canonical_sha(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def validate(path: Path) -> int:
    return subprocess.run(
        ["python3", str(VALIDATOR), "--input", str(path)],
        cwd=LAB, capture_output=True, text=True, timeout=120,
    ).returncode


def main() -> None:
    if validate(SOURCE) != 0:
        raise SystemExit("baseline incremental Gem pack must pass before adversarial tests")
    results: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="kohler-gem-adversarial-") as directory:
        root = Path(directory)

        case = root / "secret"
        shutil.copytree(SOURCE, case)
        target = case / "knowledge-s01.md"
        target.write_text(target.read_text() + "\napi_key=AIza" + "A" * 36 + "\n")
        results["secret_injection"] = validate(case) != 0

        case = root / "section"
        shutil.copytree(SOURCE, case)
        manifest = json.loads((case / "manifest.json").read_text())
        record = next(row for row in manifest["records"] if row["sections"])
        target = case / f"knowledge-{record['shard'].lower()}.md"
        marker = f"exact-text-sha256: {record['sections'][0]['exact_text_sha256']}"
        target.write_text(target.read_text().replace(marker, "exact-text-sha256: " + "0" * 64, 1))
        results["section_hash_removal"] = validate(case) != 0

        case = root / "shard"
        shutil.copytree(SOURCE, case)
        manifest_path = case / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        record = manifest["records"][0]
        record["shard"] = "S08" if record["shard"] != "S08" else "S01"
        save_manifest(manifest_path, manifest)
        results["cross_shard_substitution"] = validate(case) != 0

        case = root / "approved"
        shutil.copytree(SOURCE, case)
        manifest_path = case / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        index = next(i for i, row in enumerate(manifest["records"]) if row["source_kind"] == "approved_baseline")
        manifest["records"].pop(index)
        manifest["record_count"] -= 1
        manifest["approved_baseline_record_count"] -= 1
        save_manifest(manifest_path, manifest)
        results["approved_baseline_omission"] = validate(case) != 0

        case = root / "source"
        shutil.copytree(SOURCE, case)
        manifest_path = case / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        candidates = [row for row in manifest["records"] if row["source_kind"] == "machine_extracted_candidate"]
        candidates[0]["source_path"] = candidates[1]["source_path"]
        candidates[0]["record_file_sha256"] = candidates[1]["record_file_sha256"]
        save_manifest(manifest_path, manifest)
        results["cross_record_source_substitution"] = validate(case) != 0

    result = {"status": "PASS" if all(results.values()) else "FAIL", "cases": results}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
