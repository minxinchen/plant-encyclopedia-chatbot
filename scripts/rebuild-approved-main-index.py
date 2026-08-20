#!/usr/bin/env python3
"""Replay every approved bounded child-vector package into the disposable main index."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run(arguments: list[str]) -> None:
    subprocess.run([PYTHON, *arguments], cwd=LAB, check=True)


def main() -> None:
    run(["scripts/migrate-main-child-index.py"])
    run([
        "scripts/migrate-main-child-index.py",
        "--experiment-db", "data/index/experiments/strychnos-volume2-profile-check.sqlite",
        "--chunks-jsonl", "data/chunks/strychnos-nux-vomica-section-aware-512-100-v1.jsonl",
        "--tests", "data/tests/strychnos-nux-vomica-volume2.json",
        "--experiment-report", "reports/volume2-strychnos-profile-check-2026-08-11.json",
        "--plant-record", "data/records/strychnos-nux-vomica.json",
        "--report", "reports/main-index-child-migration-strychnos-2026-08-11.json",
    ])
    run([
        "scripts/migrate-main-child-index.py",
        "--experiment-db", "data/index/experiments/carica-papaya-volume3-profile-check.sqlite",
        "--chunks-jsonl", "data/chunks/carica-papaya-section-aware-512-100-v1.jsonl",
        "--tests", "data/tests/carica-papaya-volume3.json",
        "--experiment-report", "reports/volume3-carica-papaya-profile-check-2026-08-11.json",
        "--plant-record", "data/records/carica-papaya.json",
        "--report", "reports/main-index-child-migration-carica-papaya-2026-08-11.json",
    ])
    run([
        "scripts/migrate-main-child-index.py",
        "--experiment-db", "data/index/experiments/cibotium-barometz-volume4-profile-check.sqlite",
        "--chunks-jsonl", "data/chunks/cibotium-barometz-section-aware-512-100-v1.jsonl",
        "--tests", "data/tests/cibotium-barometz-volume4.json",
        "--experiment-report", "reports/volume4-cibotium-barometz-profile-check-2026-08-12.json",
        "--plant-record", "data/records/cibotium-barometz.json",
        "--report", "reports/main-index-child-migration-cibotium-barometz-2026-08-12.json",
    ])
    run([
        "scripts/migrate-main-child-index.py",
        "--experiment-db", "data/index/experiments/atropa-belladonna-production1-profile-check.sqlite",
        "--chunks-jsonl", "data/chunks/atropa-belladonna-section-aware-512-100-v1.jsonl",
        "--tests", "data/tests/atropa-belladonna-volume1.json",
        "--experiment-report", "reports/production1-atropa-belladonna-profile-check-2026-08-12.json",
        "--plant-record", "data/records/atropa-belladonna.json",
        "--report", "reports/main-index-child-migration-atropa-belladonna-2026-08-12.json",
    ])
    run([
        "scripts/migrate-main-child-index.py",
        "--experiment-db", "data/index/experiments/piper-nigrum-production2-profile-check.sqlite",
        "--chunks-jsonl", "data/chunks/piper-nigrum-section-aware-512-100-v1.jsonl",
        "--tests", "data/tests/piper-nigrum-volume2.json",
        "--experiment-report", "reports/production2-piper-nigrum-profile-check-2026-08-12.json",
        "--plant-record", "data/records/piper-nigrum.json",
        "--report", "reports/main-index-child-migration-piper-nigrum-2026-08-12.json",
    ])
    run([
        "scripts/migrate-main-child-index.py",
        "--experiment-db", "data/index/experiments/polygala-senega-production3-profile-check.sqlite",
        "--chunks-jsonl", "data/chunks/polygala-senega-section-aware-512-100-v1.jsonl",
        "--tests", "data/tests/polygala-senega-volume2.json",
        "--experiment-report", "reports/production3-polygala-senega-profile-check-2026-08-12.json",
        "--plant-record", "data/records/polygala-senega.json",
        "--report", "reports/main-index-child-migration-polygala-senega-2026-08-12.json",
    ])
    run([
        "scripts/migrate-main-child-index.py",
        "--experiment-db", "data/index/experiments/laminaria-hyperborea-production4-profile-check.sqlite",
        "--chunks-jsonl", "data/chunks/laminaria-hyperborea-section-aware-512-100-v1.jsonl",
        "--tests", "data/tests/laminaria-hyperborea-volume2.json",
        "--experiment-report", "reports/production4-laminaria-hyperborea-profile-check-2026-08-13.json",
        "--plant-record", "data/records/laminaria-hyperborea.json",
        "--report", "reports/main-index-child-migration-laminaria-hyperborea-2026-08-13.json",
    ])
    print("approved bounded packages rebuilt: podophyllum-peltatum, strychnos-nux-vomica, carica-papaya, cibotium-barometz, atropa-belladonna, piper-nigrum, polygala-senega, laminaria-hyperborea")


if __name__ == "__main__":
    main()
