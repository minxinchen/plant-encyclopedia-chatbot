#!/usr/bin/env python3
"""Adversarial checks for v2 drop-unmaterialized repair provenance."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


LAB = Path(__file__).resolve().parents[1]
ROOT = LAB / "data/candidates/preembedding-v1"
BUILD = LAB / "scripts/build-continuation-v2-integration.py"
VALIDATE = LAB / "scripts/validate-continuation-v2-integration.py"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def object_hash(value: dict, field: str) -> str:
    return digest({key: item for key, item in value.items() if key != field})


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(command: list[str], expect_pass: bool) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=LAB, text=True, capture_output=True, timeout=300)
    if expect_pass != (completed.returncode == 0):
        raise SystemExit((completed.stdout or completed.stderr)[-5000:])
    return completed


def copy_fixture(destination: Path) -> None:
    (destination / "boundary-evidence-v1").mkdir(parents=True)
    (destination / "structure").mkdir()
    shutil.copy2(ROOT / "boundary-evidence-v1/boundary-overlay-plan.json", destination / "boundary-evidence-v1")
    shutil.copy2(ROOT / "structure/continuation-work-packages-v2.jsonl", destination / "structure")
    shutil.copy2(ROOT / "structure/continuation-work-packages-v2-manifest.json", destination / "structure")
    shutil.copy2(ROOT / "source-receipt.json", destination / "source-receipt.json")
    shutil.copytree(ROOT / "structure/continuation-v2-maker-receipts", destination / "structure/continuation-v2-maker-receipts")
    shutil.copytree(ROOT / "structure/continuation-v2-attempts", destination / "structure/continuation-v2-attempts")
    (destination / "shards").symlink_to((ROOT / "shards").resolve(), target_is_directory=True)


def repaired_receipt(root: Path) -> tuple[Path, dict]:
    for path in sorted((root / "structure/continuation-v2-maker-receipts").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("changed_strategy_repair"):
            return path, value
    raise SystemExit("fixture lacks a changed-strategy repair")


def receipt_mutation(field: str, value: Any) -> Callable[[Path], None]:
    def mutate(root: Path) -> None:
        path, receipt = repaired_receipt(root)
        receipt["changed_strategy_repair"][field] = value
        receipt["receipt_sha256"] = object_hash(receipt, "receipt_sha256")
        write_json(path, receipt)
    return mutate


def prior_receipt_tamper(root: Path) -> None:
    _, receipt = repaired_receipt(root)
    repair = receipt["changed_strategy_repair"]
    path = (
        root / "structure/continuation-v2-attempts" / receipt["package_id"].replace(":", "__")
        / f"prior-receipt-{repair['source_receipt_sha256']}.json"
    )
    prior = json.loads(path.read_text(encoding="utf-8"))
    prior["errors"] = prior["errors"] + ["injected_error"]
    prior["receipt_sha256"] = object_hash(prior, "receipt_sha256")
    write_json(path, prior)


def main() -> None:
    cases = [
        ("content-added", receipt_mutation("content_added", True)),
        ("line-number-clamp", receipt_mutation("line_numbers_guessed_or_clamped", True)),
        ("drop-index-drift", receipt_mutation("dropped_section_indexes", [0, 1])),
        ("prior-receipt-chain-tamper", prior_receipt_tamper),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix="continuation-v2-repair-test-") as temporary:
        base = Path(temporary) / "base"
        copy_fixture(base)
        run(["python3", str(BUILD), "--root", str(base), "--require-complete"], True)
        run(["python3", str(VALIDATE), "--root", str(base), "--require-complete"], True)
        for name, mutation in cases:
            case_root = Path(temporary) / name
            shutil.copytree(base, case_root, symlinks=True)
            mutation(case_root)
            build = run(["python3", str(BUILD), "--root", str(case_root)], True)
            summary = json.loads(build.stdout.splitlines()[-1])
            if summary["complete"] or summary["embedding_ready_child_candidates"] != 0:
                raise SystemExit(f"repair adversary escaped fail-closed build:{name}")
            run(["python3", str(VALIDATE), "--root", str(case_root)], True)
            run(["python3", str(VALIDATE), "--root", str(case_root), "--require-complete"], False)
            results.append({"case": name, "rejected": True, "candidate_count": 0})
    print(json.dumps({
        "status": "PASS", "baseline_repairs": 2, "baseline_packages_passed": 46,
        "baseline_child_candidates": 34, "cases": results,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
