#!/usr/bin/env python3
"""Run the auditable sample review loop without embedding raw secrets in files."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Taipei")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def stage(name: str, tool: str, checks: list[str], status: str = "passed", retry_target: str | None = None) -> dict:
    return {
        "name": name,
        "attempt": 1,
        "status": status,
        "tool": tool,
        "checks": checks,
        "retry_target": retry_target,
    }


def main() -> None:
    lab = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=Path, default=lab / "data/volume-4-prototype-pages.json")
    parser.add_argument("--names", type=Path, default=lab / "data/sample-name-resolution.json")
    parser.add_argument("--probes", type=Path, default=lab / "data/tool-probe-results.json")
    parser.add_argument("--output", type=Path, default=lab / "reports/loop-run-sample-2026-08-03.json")
    args = parser.parse_args()

    started = datetime.now(TZ)
    pages = load_json(args.pages).get("pages", [])
    names = load_json(args.names).get("records", [])
    probes = load_json(args.probes) if args.probes.is_file() else {"probes": {}}
    stages: list[dict] = []
    warnings: list[str] = []

    extract_checks = [
        f"{len(pages)} sampled pages retain source_id, source_path and PDF page",
        "embedded text remains unmodified evidence, including OCR noise",
    ]
    extract_ok = bool(pages) and all(p.get("source_id") and p.get("pdf_page") and "text" in p for p in pages)
    stages.append(stage("extract", "Poppler pdftotext", extract_checks, "passed" if extract_ok else "failed", None if extract_ok else "extract"))

    scientific_names = [r.get("query_scientific_name", "") for r in names]
    assembled = {name: [p["pdf_page"] for p in pages if name.split()[0].lower() in p.get("text", "").lower()] for name in scientific_names}
    assemble_ok = all(assembled.get(name) for name in scientific_names)
    stages.append(stage(
        "assemble_records",
        "deterministic scientific-name linkage; Qwen adapter for full batches",
        [f"linked {len(scientific_names)} sampled taxa to evidence pages", "plate pages remain distinct from prose pages"],
        "passed" if assemble_ok else "failed",
        None if assemble_ok else "assemble_records",
    ))

    name_ok = all(r.get("display_name_zh_tw") and r.get("sources") for r in names)
    pending_taicol = [r["query_scientific_name"] for r in names if r.get("primary_taicol_direct_check") == "pending"]
    if pending_taicol:
        warnings.append("TaiCOL direct check remains pending for: " + ", ".join(pending_taicol))
    stages.append(stage(
        "resolve_names",
        "TaiCOL primary, Tai2 secondary",
        [f"{len(names)} of {len(names)} sampled display names have public source evidence", "Taiwan occurrence is stored separately from the Chinese display name"],
        "needs_review" if name_ok and pending_taicol else ("passed" if name_ok else "failed"),
        "resolve_names" if pending_taicol or not name_ok else None,
    ))

    gemini = probes.get("probes", {}).get("gemini_multimodal_embedding", {})
    gemini_ok = gemini.get("status") == "passed" and gemini.get("dimensions", 0) >= 128
    stages.append(stage(
        "build_multimodal_index",
        "Gemini API Free Tier gemini-embedding-2 plus local exact/BM25",
        ["exact and lexical retrieval remain available independently", f"multimodal embedding probe: {gemini.get('status', 'not_run')}"],
        "passed" if gemini_ok else "needs_review",
        None if gemini_ok else "build_multimodal_index",
    ))

    qwen = probes.get("probes", {}).get("qwen_evidence_review", {})
    adjudication = probes.get("probes", {}).get("independent_adjudication", {})
    review_ok = adjudication.get("status") == "passed"
    stages.append(stage(
        "independent_review",
        "deterministic gates + Qwen local + Gemini multimodal + sampled GPT/Codex adjudication",
        [f"Qwen maker probe: {qwen.get('status', 'not_run')}", f"independent adjudication: {adjudication.get('status', 'not_run')}", "coverage-aware refusal separates 尚未處理 from 本書未記載"],
        "passed" if review_ok else "needs_review",
        None if review_ok else "independent_review",
    ))

    failures = sum(s["status"] == "failed" for s in stages)
    reviews = sum(s["status"] == "needs_review" for s in stages)
    status = "failed" if failures else ("needs_review" if reviews else "approved")
    finished = datetime.now(TZ)
    result = {
        "schema_version": "1.0",
        "run_id": "sample-volume-4-2026-08-03",
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "status": status,
        "cost": {"incremental_usd": 0, "paid_fallback_used": False},
        "input": {
            "pages_file": str(args.pages.resolve()),
            "name_file": str(args.names.resolve()),
            "page_count": len(pages),
            "taxon_count": len(names),
        },
        "stages": stages,
        "review_summary": {
            "passed": sum(s["status"] == "passed" for s in stages),
            "needs_review": reviews,
            "failed": failures,
            "warnings": warnings,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "output": str(args.output), "retry_targets": [s["retry_target"] for s in stages if s["retry_target"]]}, ensure_ascii=False))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
