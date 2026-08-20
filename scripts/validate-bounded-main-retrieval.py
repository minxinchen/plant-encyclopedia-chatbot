#!/usr/bin/env python3
"""Validate a bounded fixture against the portable main index and parent-collapse contract."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=LAB / "data/index/plant-embeddings.sqlite")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("main_query", LAB / "scripts/query-main-index.py")
    if spec is None or spec.loader is None:
        raise SystemExit("FAIL cannot load main query helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tests = json.loads(args.tests.read_text(encoding="utf-8"))
    results = []
    for fixture in tests["queries"]:
        result = module.retrieve_collapsed(args.database, args.tests, fixture["query_id"])
        top = result["collapsed_parent_hits"][0]
        if fixture["expected_status"] == "answerable":
            valid = bool(
                result["answer_gate"] == "supporting_book_terms_found"
                and top["has_required_book_term"]
                and top["pdf_page"] in fixture["expected_pdf_pages"]
                and top["record_id"] == tests["record_id"]
            )
        else:
            valid = result["answer_gate"] == "no_supporting_book_relation"
        if not valid:
            raise SystemExit(f"FAIL {fixture['query_id']}: top={top['record_id']} p{top['pdf_page']} gate={result['answer_gate']}")
        results.append({
            "query_id": fixture["query_id"],
            "top_record_id": top["record_id"],
            "top_pdf_page": top["pdf_page"],
            "answer_gate": result["answer_gate"],
            "duplicate_parent_hits_removed": result["duplicate_parent_hits_removed"],
        })
    print(json.dumps({"valid": True, "queries": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
