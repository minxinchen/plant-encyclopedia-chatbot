#!/usr/bin/env python3
"""Verify that chunk validation fails closed after self-consistent tampering."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
SOURCE = LAB / "data/candidates/preembedding-v1"


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def run_case(case_id: str, mutate) -> dict:
    with tempfile.TemporaryDirectory(prefix="plant-chunk-adversarial-") as raw:
        root = Path(raw)
        (root / "chunks-candidate").mkdir()
        (root / "integration").symlink_to(SOURCE / "integration", target_is_directory=True)
        (root / "naming").symlink_to(SOURCE / "naming", target_is_directory=True)
        source_chunks = SOURCE / "chunks-candidate/section-aware-512-100-v1.jsonl"
        rows = [json.loads(line) for line in source_chunks.read_text().splitlines() if line]
        mutate(rows[0])
        unhashed = dict(rows[0])
        unhashed.pop("chunk_sha256", None)
        rows[0]["chunk_sha256"] = canonical_hash(unhashed)
        (root / "chunks-candidate/section-aware-512-100-v1.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
        )
        (root / "chunks-candidate/manifest.json").write_bytes(
            (SOURCE / "chunks-candidate/manifest.json").read_bytes()
        )
        result = subprocess.run(
            ["python3", str(LAB / "scripts/validate-preembedding-chunks.py"),
             "--root", str(root), "--require-caught-up"],
            cwd=LAB, text=True, capture_output=True,
        )
        return {"case_id": case_id, "passed": result.returncode != 0,
                "validator_output": (result.stdout or result.stderr).strip()[-1200:]}


def main() -> None:
    cases = [
        run_case("candidate-source-chain-drift", lambda row: row["candidate_source_chain"].update(
            {"receipt_sha256": "0" * 64}
        )),
        run_case("integration-manifest-substitution", lambda row: row.update(
            {"integration_manifest_sha256": "1" * 64}
        )),
        run_case("naming-artifact-substitution", lambda row: row.update(
            {"naming_artifact_sha256": "2" * 64}
        )),
    ]
    report = {"status": "PASS" if all(case["passed"] for case in cases) else "FAIL",
              "cases": cases}
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
