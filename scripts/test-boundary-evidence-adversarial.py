#!/usr/bin/env python3
"""Prove that boundary validation rejects high-risk mutations.

author: Codex (GPT-5)
date: 2026-08-20
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
SOURCE = LAB / "data/candidates/preembedding-v1/boundary-evidence-v1"
VALIDATOR = LAB / "scripts/validate-boundary-evidence.py"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def load(root: Path) -> tuple[list[dict], dict]:
    rows = [json.loads(line) for line in (root / "boundary-evidence.jsonl").read_text().splitlines() if line]
    manifest = json.loads((root / "manifest.json").read_text())
    return rows, manifest


def save(root: Path, rows: list[dict], manifest: dict, rehash_rows: bool = True) -> None:
    if rehash_rows:
        for row in rows:
            row.pop("evidence_sha256", None)
            row["evidence_sha256"] = digest(row)
    manifest["scope"]["evaluated_pages"] = len(rows)
    manifest["decision_counts"] = dict(sorted(Counter(row["decision"] for row in rows).items()))
    hidden = [row for row in rows if row["decision"] == "confirmed_hidden_heading"]
    manifest["confirmed_hidden_heading_count"] = len(hidden)
    manifest["confirmed_hidden_headings"] = [
        {
            "parent_entry_id": row["parent_entry_id"],
            "source_id": row["source_id"],
            "pdf_page": row["pdf_page"],
            "taxon_candidate": row["boundary_taxon_candidate"],
            "evidence_sha256": row["evidence_sha256"],
        }
        for row in hidden
    ]
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = digest(manifest)
    (root / "boundary-evidence.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def rejected(root: Path) -> bool:
    result = subprocess.run(
        ["python3", str(VALIDATOR), "--output", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode != 0


def main() -> None:
    outcomes: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="kohler-boundary-adversarial-") as temp:
        base = Path(temp)
        for case in (
            "prose_as_taxon",
            "scientific_gate_bypass",
            "row_hash_drift",
            "canonical_write_escape",
            "duplicate_page_key",
        ):
            root = base / case
            root.mkdir()
            shutil.copy2(SOURCE / "boundary-evidence.jsonl", root)
            shutil.copy2(SOURCE / "manifest.json", root)
            rows, manifest = load(root)

            if case == "prose_as_taxon":
                row = next(item for item in rows if (item.get("local_heading") or {}).get("taxon_candidate") == "Der wirksame")
                row["decision"] = "confirmed_hidden_heading"
                row["canonical_taxon_agreement"] = True
                row["scientific_name_evidence"]["finder_exact_binomial_at_start"] = True
                row["scientific_name_evidence"]["verifier_match_type"] = "Exact"
                row["scientific_name_evidence"]["exact_species_gate"] = True
                save(root, rows, manifest)
            elif case == "scientific_gate_bypass":
                row = next(item for item in rows if item["decision"] == "confirmed_hidden_heading")
                row["scientific_name_evidence"]["exact_species_gate"] = False
                save(root, rows, manifest)
            elif case == "row_hash_drift":
                rows[0]["local_page_text_sha256"] = "0" * 64
                save(root, rows, manifest, rehash_rows=False)
            elif case == "canonical_write_escape":
                rows[0]["canonical_write_allowed"] = True
                save(root, rows, manifest)
            elif case == "duplicate_page_key":
                rows.append(dict(rows[0]))
                save(root, rows, manifest)
            outcomes[case] = rejected(root)

    result = {"valid": all(outcomes.values()), "cases_rejected": outcomes}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
