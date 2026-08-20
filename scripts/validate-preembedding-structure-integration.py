#!/usr/bin/env python3
"""Validate and integrate local pre-embedding structure candidates.

This is a staging-only checker. It never writes canonical records, chunks,
indexes, source PDFs, Taiwan names, or model outputs. It may be rerun while the
local maker batch is active; missing maker receipts remain non-terminal.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
SECTION_TYPES = {
    "taxonomy", "description", "anatomy", "distribution", "history",
    "flowering", "harvest", "constituents", "historical_use", "literature",
    "plate_description", "other",
}
TERMINAL_SPECIAL = {
    "already_approved_overlap",
    "hold_page_quality",
    "hold_span_over_limit",
    "hold_terminal_no_next_heading",
}
SOURCE_PDF_HASHES: dict[str, str] = {}
DETERMINISTIC_REPAIRS = {
    "kohler-volume-1:p0159-p0160": {
        "repair_id": "malva-plate-caption-line-end-v1",
        "reason": "maker_included_nonexistent_trailing_empty_line_in_plate_caption_range",
        "strategy": "source_exact_line_range_correction",
        "section_index": 5,
        "section_type": "plate_description",
        "source_line_spans": [
            {"pdf_page": 160, "source_line_start": 68, "source_line_end": 71},
        ],
        "allowed_original_errors": {
            "section_5:source_line_range_out_of_bounds:p160:L68-L72",
        },
    },
    "kohler-volume-2:p0089-p0092": {
        "repair_id": "quercus-anatomy-cross-page-v1",
        "reason": "maker_encoded_cross_page_anatomy_as_one_same_page_line_range",
        "section_index": 2,
        "section_type": "anatomy",
        "source_line_spans": [
            {"pdf_page": 89, "source_line_start": 37, "source_line_end": 40},
            {"pdf_page": 90, "source_line_start": 1, "source_line_end": 12},
        ],
        "allowed_original_errors": {
            "section_2:source_line_range_out_of_bounds:p89:L37-L42",
        },
    },
    "kohler-volume-2:p0247-p0250": {
        "repair_id": "pterocarpus-use-cross-page-v1",
        "reason": "maker_encoded_cross_page_historical_use_as_reversed_same_page_range",
        "section_index": 4,
        "section_type": "historical_use",
        "source_line_spans": [
            {"pdf_page": 249, "source_line_start": 52, "source_line_end": 53},
            {"pdf_page": 250, "source_line_start": 1, "source_line_end": 7},
        ],
        "allowed_original_errors": {
            "section_4:source_line_range_out_of_bounds:p249:L52-L6",
        },
    },
    "kohler-volume-2:p0477-p0482": {
        "repair_id": "elettaria-history-cross-page-v1",
        "reason": "maker_encoded_cross_page_history_as_one_same_page_line_range",
        "section_index": 3,
        "section_type": "history",
        "source_line_spans": [
            {"pdf_page": 478, "source_line_start": 64, "source_line_end": 66},
            {"pdf_page": 479, "source_line_start": 1, "source_line_end": 27},
        ],
        "allowed_original_errors": {
            "section_3:source_line_range_out_of_bounds:p478:L64-L72",
        },
    },
    "kohler-volume-3:p0043-p0044": {
        "repair_id": "palaquium-history-cross-page-v1",
        "reason": "maker_reversed_same_page_line_range_for_cross_page_history",
        "section_index": 4,
        "section_type": "history",
        "source_line_spans": [
            {"pdf_page": 43, "source_line_start": 53, "source_line_end": 57},
            {"pdf_page": 44, "source_line_start": 1, "source_line_end": 38},
        ],
        "allowed_original_errors": {
            "section_4:source_line_range_out_of_bounds:p43:L53-L38",
        },
    },
    "kohler-volume-2:p0073-p0076": {
        "repair_id": "inula-cross-page-sections-v1",
        "reason": "maker_reversed_same_page_line_ranges_for_two_cross_page_sections",
        "repairs": [
            {
                "section_index": 1,
                "section_type": "anatomy",
                "source_line_spans": [
                    {"pdf_page": 73, "source_line_start": 31, "source_line_end": 35},
                    {"pdf_page": 74, "source_line_start": 1, "source_line_end": 4},
                ],
            },
            {
                "section_index": 4,
                "section_type": "constituents",
                "source_line_spans": [
                    {"pdf_page": 74, "source_line_start": 48, "source_line_end": 62},
                    {"pdf_page": 75, "source_line_start": 1, "source_line_end": 28},
                ],
            },
        ],
        "allowed_original_errors": {
            "section_1:source_line_range_out_of_bounds:p73:L31-L4",
            "section_4:source_line_range_out_of_bounds:p74:L48-L28",
        },
    },
}
PAGE_QUALITY_RECOVERY_CONTRACTS = {
    "kohler-volume-1:p0111-p0115": {"pdf_pages": [111, 112, 113, 115], "ocr_excluded_pages": [114]},
    "kohler-volume-1:p0116-p0117": {"pdf_pages": [116], "ocr_excluded_pages": [117]},
    "kohler-volume-1:p0121-p0125": {"pdf_pages": [121, 122, 123, 125], "ocr_excluded_pages": [124]},
    "kohler-volume-1:p0126-p0127": {"pdf_pages": [126], "ocr_excluded_pages": [127]},
}
TERMINAL_BOUNDARY_RECOVERY_CONTRACTS = {
    "kohler-volume-1:p0233-p0410": {
        "body_end_pdf_page": 234,
        "trailing_plate_start_pdf_page": 235,
        "boundary_marker": "L aurine a e.",
        "children": [
            {"recovered_entry_id": "kohler-volume-1:p0233-p0234", "book_taxon_candidate": "Hagenia abyssinica", "pdf_pages": [233, 234]},
        ],
    },
    "kohler-volume-2:p0505-p0738": {
        "body_end_pdf_page": 508,
        "trailing_plate_start_pdf_page": 509,
        "boundary_marker": "Phellandrium",
        "children": [
            {"recovered_entry_id": "kohler-volume-2:p0505-p0508", "book_taxon_candidate": "Strophanthus hispidus", "pdf_pages": [505, 506, 507, 508]},
        ],
    },
    "kohler-volume-3:p0373-p0536": {
        "body_end_pdf_page": 376,
        "trailing_plate_start_pdf_page": 377,
        "boundary_marker": "Palaquium Gulta Burck.",
        "children": [
            {"recovered_entry_id": "kohler-volume-3:p0373-p0374", "book_taxon_candidate": "Juniperus Oxycedrus", "pdf_pages": [373, 374]},
            {"recovered_entry_id": "kohler-volume-3:p0375-p0376", "book_taxon_candidate": "Convallaria majalis", "pdf_pages": [375, 376]},
        ],
    },
    "kohler-volume-4:p0066-p0090": {
        "body_end_pdf_page": 69,
        "trailing_plate_start_pdf_page": 70,
        "boundary_marker": "Tafel   1",
        "children": [
            {"recovered_entry_id": "kohler-volume-4:p0066-p0069", "book_taxon_candidate": "Lupinus albus", "pdf_pages": [66, 67, 68, 69]},
        ],
    },
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(
            "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values)
        )
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def source_locator(page: dict, *, char_start: int = 0, char_end: int | None = None) -> dict:
    text = page["text"]
    end = len(text) if char_end is None else char_end
    exact = text[char_start:end]
    return {
        "source_id": page["source_id"],
        "volume": page["volume"],
        "pdf_page": page["pdf_page"],
        "source_pdf_sha256": SOURCE_PDF_HASHES[page["source_id"]],
        "char_start": char_start,
        "char_end": end,
        "page_text_sha256": page["text_sha256"],
        "exact_text_sha256": sha256_text(exact),
    }


def locator_from_line_span(page: dict, start: int, end: int) -> tuple[dict | None, str | None]:
    lines = page["text"].splitlines()
    if start < 1 or end < start or end > len(lines) or end - start + 1 > 60:
        return None, f"source_line_range_out_of_bounds:p{page['pdf_page']}:L{start}-L{end}"
    quote = "\n".join(lines[start - 1:end])
    if not quote.strip():
        return None, f"empty_source_line_range:p{page['pdf_page']}:L{start}-L{end}"
    text = page["text"]
    char_start = text.find(quote)
    if char_start < 0:
        return None, f"source_quote_not_exact:p{page['pdf_page']}"
    if text.find(quote, char_start + 1) >= 0:
        return None, f"source_quote_ambiguous_without_unique_char_locator:p{page['pdf_page']}"
    locator = source_locator(page, char_start=char_start, char_end=char_start + len(quote))
    locator.update({
        "source_line_start": start,
        "source_line_end": end,
        "exact_source_quote": quote,
    })
    return locator, None


def entry_locators(entry: dict, pages: dict[tuple[str, int], dict]) -> list[dict]:
    return [source_locator(pages[(entry["source_id"], page)]) for page in entry["pdf_pages"]]


def receipt_hash(receipt: dict) -> str:
    material = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    # Match the maker's receipt hashing contract, including default separators.
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def compact_object_hash(value: dict, field: str) -> str:
    material = {key: item for key, item in value.items() if key != field}
    return sha256_text(canonical_json(material))


def taxon_binomial(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    tokens = re.findall(r"[A-Za-z][A-Za-z.-]*", value)
    if len(tokens) < 2:
        return None
    return tokens[0].casefold(), tokens[1].casefold()


def continuation_receipt_path(root: Path, package_id: str) -> Path:
    return root / "structure/continuation-maker-receipts" / f"{package_id.replace(':', '__')}.json"


def validate_continuation_receipt(
    receipt: dict,
    package: dict,
    pages: dict[tuple[str, int], dict],
) -> tuple[list[str], list[dict], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    locators: list[dict] = []
    if receipt.get("schema_version") != "1.0":
        errors.append("invalid_receipt_schema_version")
    identity = {
        "package_id": package["package_id"],
        "parent_entry_id": package["parent_entry_id"],
        "work_id": package["work_id"],
        "owner_shard": package["owner_shard"],
        "package_sha256": package["package_sha256"],
    }
    for field, expected in identity.items():
        if receipt.get(field) != expected:
            errors.append(f"{field}_mismatch")
    if "recovered_entry_id" in package and receipt.get("recovered_entry_id") != package["recovered_entry_id"]:
        errors.append("recovered_entry_id_mismatch")
    if receipt.get("receipt_sha256") != compact_object_hash(receipt, "receipt_sha256"):
        errors.append("receipt_sha256_mismatch")
    if receipt.get("external_model_calls") != 0 or receipt.get("incremental_usd") != 0:
        errors.append("nonlocal_or_nonzero_cost_receipt")
    if receipt.get("name_resolution_status") != "unresolved":
        errors.append("name_resolution_status_must_be_unresolved")
    if receipt.get("layout_or_plate_claims_approved") is not False:
        errors.append("layout_or_plate_claim_was_approved")
    draft = receipt.get("draft")
    if not isinstance(draft, dict):
        return sorted(set(errors + ["draft_missing_or_not_object"])), locators, warnings
    if draft.get("package_id") != package["package_id"] or draft.get("parent_entry_id") != package["parent_entry_id"]:
        errors.append("draft_identity_mismatch")
    if draft.get("review_status") != "machine_extracted":
        errors.append("review_status_must_be_machine_extracted")
    if draft.get("display_name") is not None:
        errors.append("display_name_must_be_null")
    if draft.get("name_resolution") != {"status": "unresolved", "sources": []}:
        errors.append("name_resolution_must_be_unresolved_without_sources")
    book_taxon = draft.get("book_taxon")
    if not isinstance(book_taxon, dict) or (
        taxon_binomial(book_taxon.get("scientific_name_candidate"))
        != taxon_binomial(package["book_taxon_candidate"])
    ):
        errors.append("book_taxon_candidate_mismatch")
    sections = draft.get("sections")
    if not isinstance(sections, list) or not sections:
        return sorted(set(errors + ["no_sections"])), locators, warnings
    if len(sections) > 6:
        errors.append("too_many_sections")
    seen_types: set[str] = set()
    for position, section in enumerate(sections):
        prefix = f"section_{position}"
        if not isinstance(section, dict):
            errors.append(f"{prefix}:not_object")
            continue
        section_type = section.get("section_type")
        if section_type not in SECTION_TYPES:
            errors.append(f"{prefix}:invalid_section_type")
        elif section_type in seen_types:
            errors.append(f"{prefix}:duplicate_section_type:{section_type}")
        else:
            seen_types.add(section_type)
        page_number = section.get("pdf_page")
        page = pages.get((package["source_id"], page_number))
        if page is None:
            errors.append(f"{prefix}:source_page_missing:p{page_number}")
            continue
        locator, locator_errors = section_locator(section, package, page)
        errors.extend(f"{prefix}:{error}" for error in locator_errors)
        if locator:
            locator["section_index"] = position
            locator["section_type"] = section_type
            locators.append(locator)
        normalized = section.get("normalized_text_candidate")
        if normalized is not None and not isinstance(normalized, str):
            errors.append(f"{prefix}:normalized_text_candidate_not_string_or_null")
        zh_tw = section.get("zh_tw_rendering_candidate")
        if zh_tw is not None and not isinstance(zh_tw, str):
            errors.append(f"{prefix}:zh_tw_rendering_candidate_not_string_or_null")
        elif isinstance(zh_tw, str) and len(zh_tw) > 120:
            errors.append(f"{prefix}:zh_tw_rendering_candidate_over_120_chars")
        if section_type == "plate_description":
            warnings.append(f"{prefix}:plate_text_only_not_visually_approved")
    declared_locators = receipt.get("section_source_locators")
    if declared_locators != locators:
        errors.append("receipt_section_source_locators_drift")
    if receipt.get("deterministic_status") != "pass" or receipt.get("errors") != []:
        errors.append("continuation_maker_not_deterministic_pass")
    return sorted(set(errors)), locators, sorted(set(warnings))


def recovery_candidate_from_pass(package: dict, receipt: dict, check: dict) -> dict | None:
    locators_by_section = {
        locator["section_index"]: locator for locator in check["section_source_locators"]
    }
    sections = []
    excluded_plate_count = 0
    for section_index, section in enumerate(receipt["draft"]["sections"]):
        locator = locators_by_section.get(section_index)
        if locator is None:
            continue
        if section["section_type"] == "plate_description":
            excluded_plate_count += 1
            continue
        quote = section.get("exact_source_quote")
        if not isinstance(quote, str) or not quote:
            continue
        sections.append({
            "section_type": section["section_type"],
            "exact_source_quotes": [quote],
            "normalized_text_candidate": section.get("normalized_text_candidate"),
            "zh_tw_rendering_candidate": section.get("zh_tw_rendering_candidate"),
            "source_locators": [dict(locator)],
            "recovery_provenance": [{
                "package_id": package["package_id"],
                "package_sha256": package["package_sha256"],
                "receipt_sha256": receipt["receipt_sha256"],
                "validation_check_sha256": check["check_sha256"],
                "package_section_index": section_index,
                "recovery_kind": package["recovery_kind"],
            }],
        })
    if not sections:
        return None
    candidate = {
        "schema_version": "1.0",
        "entry_id": package["recovered_entry_id"],
        "source_parent_entry_id": package["parent_entry_id"],
        "owner_shard": package["owner_shard"],
        "source_id": package["source_id"],
        "volume": package["volume"],
        "book_taxon_candidate": package["book_taxon_candidate"],
        "maker_receipt_sha256": receipt["receipt_sha256"],
        "validation_check_sha256": check["check_sha256"],
        "recovery_package": {
            "package_id": package["package_id"],
            "package_sha256": package["package_sha256"],
            "recovery_kind": package["recovery_kind"],
        },
        "display_name": None,
        "name_resolution": {"status": "unresolved", "sources": []},
        "review_status": "machine_extracted",
        "sections": sections,
        "excluded_plate_section_count": excluded_plate_count,
        "layout_or_plate_claims_approved": False,
        "canonical_write_allowed": False,
        "embedding_call_performed": False,
    }
    candidate["candidate_sha256"] = sha256_text(canonical_json(candidate))
    return candidate


def section_locator(section: dict, entry: dict, page: dict) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    page_number = section.get("pdf_page")
    if page_number not in entry["pdf_pages"]:
        errors.append(f"section_page_outside_entry:p{page_number}")
        return None, errors
    line_range = section.get("source_line_range")
    if (
        not isinstance(line_range, list) or len(line_range) != 2
        or not all(isinstance(value, int) for value in line_range)
    ):
        errors.append(f"invalid_source_line_range:p{page_number}")
        return None, errors
    start, end = line_range
    locator, locator_error = locator_from_line_span(page, start, end)
    if locator_error:
        errors.append(locator_error)
        return None, errors
    assert locator is not None
    expected_quote = locator["exact_source_quote"]
    quote = section.get("exact_source_quote")
    if not isinstance(quote, str) or quote != expected_quote or not quote.strip():
        errors.append(f"source_line_quote_mismatch:p{page_number}:L{start}-L{end}")
        return None, errors
    locator.pop("exact_source_quote")
    locator["source_line_start"] = start
    locator["source_line_end"] = end
    return locator, errors


def apply_changed_strategy_repair(
    entry: dict,
    receipt: dict,
    errors: list[str],
    locators: list[dict],
    pages: dict[tuple[str, int], dict],
) -> tuple[list[str], list[dict], dict | None]:
    contract = DETERMINISTIC_REPAIRS.get(entry["entry_id"])
    if contract is None:
        return errors, locators, None
    original_errors = set(errors)
    allowed = set(contract["allowed_original_errors"])
    if not original_errors or not original_errors.issubset(allowed):
        return errors, locators, None
    draft = receipt.get("draft", {})
    sections = draft.get("sections", [])
    repair_specs = contract.get("repairs") or [{
        "section_index": contract["section_index"],
        "section_type": contract["section_type"],
        "source_line_spans": contract["source_line_spans"],
    }]
    repaired_locators = []
    repair_errors = []
    repaired_section_indexes = set()
    for specification in repair_specs:
        section_index = specification["section_index"]
        section_type = specification["section_type"]
        if section_index >= len(sections) or sections[section_index].get("section_type") != section_type:
            repair_errors.append(f"repair_contract_section_mismatch:s{section_index}")
            continue
        repaired_section_indexes.add(section_index)
        for span in specification["source_line_spans"]:
            page = pages.get((entry["source_id"], span["pdf_page"]))
            if page is None or span["pdf_page"] not in entry["pdf_pages"]:
                repair_errors.append(f"repair_source_page_missing:p{span['pdf_page']}")
                continue
            locator, locator_error = locator_from_line_span(
                page, span["source_line_start"], span["source_line_end"]
            )
            if locator_error:
                repair_errors.append("repair_" + locator_error)
                continue
            assert locator is not None
            locator["section_index"] = section_index
            locator["section_type"] = section_type
            repaired_locators.append(locator)
    expected_locator_count = sum(len(item["source_line_spans"]) for item in repair_specs)
    if repair_errors or len(repaired_locators) != expected_locator_count:
        return sorted(set(errors + repair_errors)), locators, None
    clean_locators = [item for item in locators if item["section_index"] not in repaired_section_indexes]
    repair = {
        "schema_version": "1.0",
        "repair_id": contract["repair_id"],
        "entry_id": entry["entry_id"],
        "owner_shard": entry["owner_shard"],
        "strategy": contract.get("strategy", "split_cross_page_exact_line_spans"),
        "reason": contract["reason"],
        "original_receipt_sha256": receipt.get("receipt_sha256"),
        "original_errors": sorted(original_errors),
        "repaired_sections": [
            {"section_index": item["section_index"], "section_type": item["section_type"]}
            for item in repair_specs
        ],
        "source_locators": repaired_locators,
        "review_status": "machine_extracted",
        "name_resolution_status": "unresolved",
        "layout_or_plate_claims_approved": False,
        "status": "deterministic_pass_changed_strategy",
    }
    repair["repair_sha256"] = sha256_text(canonical_json(repair))
    return [], clean_locators + repaired_locators, repair


def validate_receipt(receipt: dict, entry: dict, pages: dict[tuple[str, int], dict]) -> tuple[list[str], list[dict], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    locators: list[dict] = []
    if receipt.get("schema_version") != "1.0":
        errors.append("invalid_receipt_schema_version")
    if receipt.get("entry_id") != entry["entry_id"]:
        errors.append("receipt_entry_id_mismatch")
    if receipt.get("owner_shard") != entry["owner_shard"]:
        errors.append("receipt_owner_shard_mismatch")
    if receipt.get("external_model_calls") != 0 or receipt.get("incremental_usd") != 0:
        errors.append("nonlocal_or_nonzero_cost_receipt")
    if receipt.get("receipt_sha256") != receipt_hash(receipt):
        errors.append("receipt_sha256_mismatch")
    draft = receipt.get("draft")
    if not isinstance(draft, dict):
        errors.append("draft_missing_or_not_object")
        return errors, locators, warnings
    required_draft = {"entry_id", "book_taxon", "display_name", "name_resolution", "sections", "review_status", "warnings"}
    missing = sorted(required_draft - set(draft))
    if missing:
        errors.append("draft_missing_fields:" + ",".join(missing))
    if draft.get("entry_id") != entry["entry_id"]:
        errors.append("draft_entry_id_mismatch")
    if draft.get("review_status") != "machine_extracted":
        errors.append("review_status_must_be_machine_extracted")
    if draft.get("display_name") is not None:
        errors.append("display_name_must_be_null")
    name_resolution = draft.get("name_resolution")
    if not isinstance(name_resolution, dict) or name_resolution.get("status") != "unresolved":
        errors.append("name_resolution_must_be_unresolved")
    elif name_resolution.get("sources") != []:
        errors.append("maker_must_not_supply_name_sources")
    book_taxon = draft.get("book_taxon")
    if not isinstance(book_taxon, dict) or not isinstance(book_taxon.get("scientific_name_candidate"), str):
        errors.append("invalid_book_taxon_candidate")
    sections = draft.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("no_sections")
        return errors, locators, warnings
    if len(sections) > 6:
        errors.append("too_many_sections")
    seen_types: set[str] = set()
    for position, section in enumerate(sections):
        prefix = f"section_{position}"
        if not isinstance(section, dict):
            errors.append(f"{prefix}:not_object")
            continue
        section_type = section.get("section_type")
        if section_type not in SECTION_TYPES:
            errors.append(f"{prefix}:invalid_section_type")
        elif section_type in seen_types:
            errors.append(f"{prefix}:duplicate_section_type:{section_type}")
        else:
            seen_types.add(section_type)
        page_number = section.get("pdf_page")
        page = pages.get((entry["source_id"], page_number))
        if page is None:
            errors.append(f"{prefix}:source_page_missing:p{page_number}")
            continue
        locator, locator_errors = section_locator(section, entry, page)
        errors.extend(f"{prefix}:{error}" for error in locator_errors)
        if locator:
            locator["section_index"] = position
            locator["section_type"] = section_type
            locators.append(locator)
        normalized = section.get("normalized_text_candidate")
        if normalized is not None and not isinstance(normalized, str):
            errors.append(f"{prefix}:normalized_text_candidate_not_string_or_null")
        zh_tw = section.get("zh_tw_rendering_candidate")
        if zh_tw is not None and not isinstance(zh_tw, str):
            errors.append(f"{prefix}:zh_tw_rendering_candidate_not_string_or_null")
        elif isinstance(zh_tw, str) and len(zh_tw) > 120:
            errors.append(f"{prefix}:zh_tw_rendering_candidate_over_120_chars")
        if section_type == "plate_description":
            warnings.append(f"{prefix}:plate_text_only_not_visually_approved")
    if receipt.get("deterministic_status") == "pass" and receipt.get("errors"):
        errors.append("maker_pass_contains_errors")
    return sorted(set(errors)), locators, sorted(set(warnings))


def split_continuations(entry: dict, pages: dict[tuple[str, int], dict], maximum_pages: int) -> list[dict]:
    packages = []
    source_pages = entry["pdf_pages"]
    for position in range(0, len(source_pages), maximum_pages):
        group = source_pages[position:position + maximum_pages]
        package_id = f"{entry['entry_id']}:continuation-{position // maximum_pages + 1:02d}"
        package = {
            "schema_version": "1.0",
            "package_id": package_id,
            "work_id": f"structure-continuation:{package_id}:v1",
            "stage": "local_structure_continuation",
            "parent_entry_id": entry["entry_id"],
            "owner_shard": entry["owner_shard"],
            "source_id": entry["source_id"],
            "volume": entry["volume"],
            "pdf_pages": group,
            "page_count": len(group),
            "sequence": position // maximum_pages + 1,
            "sequence_count": (len(source_pages) + maximum_pages - 1) // maximum_pages,
            "book_taxon_candidate": entry["book_taxon_candidate"],
            "source_locators": [source_locator(pages[(entry["source_id"], page)]) for page in group],
            "review_status": "candidate",
            "name_resolution_status": "unresolved",
            "layout_or_plate_claims_approved": False,
            "status": "planned_local_structure_continuation",
            "dependencies": ["primary_local_structure_batch_complete"],
            "route": {
                "maker": "local_qwen_structure",
                "checker": "deterministic_source_hash_schema_validator",
            },
            "proof_of_done": [
                "all proposed sections resolve to exact page char locators and source hashes",
                "review_status remains machine_extracted",
                "Taiwan display name remains null and name resolution remains unresolved",
                "layout and plate claims remain unapproved",
            ],
            "forbidden": [
                "canonical_record_write",
                "canonical_chunk_write",
                "embedding_index_write",
                "source_pdf_write",
                "external_api",
                "taiwan_name_invention",
                "layout_or_plate_self_approval",
            ],
        }
        package["package_sha256"] = sha256_text(canonical_json(package))
        packages.append(package)
    return packages


def build_content_recovery_packages(
    root: Path,
    entries_by_id: dict[str, dict],
    pages: dict[tuple[str, int], dict],
) -> list[dict]:
    ocr_manifest = read_json(root / "consolidated-ocr-staging-manifest.json")
    expected_ocr_hash = sha256_text(canonical_json({
        "summary": ocr_manifest["summary"],
        "pages": ocr_manifest["pages"],
    }))
    if ocr_manifest.get("content_sha256") != expected_ocr_hash:
        raise SystemExit("consolidated OCR staging manifest content hash mismatch")
    ocr_by_page = {
        (item["source_id"], item["pdf_page"]): item for item in ocr_manifest["pages"]
    }
    packages = []
    for parent_entry_id, contract in PAGE_QUALITY_RECOVERY_CONTRACTS.items():
        entry = entries_by_id[parent_entry_id]
        exclusions = []
        for pdf_page in contract["ocr_excluded_pages"]:
            ocr = ocr_by_page.get((entry["source_id"], pdf_page))
            if (
                ocr is None or ocr.get("terminal") is not True
                or ocr.get("staging_disposition") != "no_text_detected"
                or ocr.get("receipt_hash_valid") is not True
            ):
                raise SystemExit(f"page-quality OCR exclusion is not terminal no-text: {parent_entry_id}/p{pdf_page}")
            artifact = Path(ocr["artifact"])
            receipt = read_json(artifact)
            if receipt.get("receipt_sha256") != receipt_hash(receipt) or receipt.get("text") != "":
                raise SystemExit(f"page-quality OCR receipt drift: {parent_entry_id}/p{pdf_page}")
            try:
                artifact_reference = str(artifact.relative_to(root))
            except ValueError:
                # Synthetic validators intentionally reuse the immutable real OCR
                # receipt through a temporary root whose shards path is a symlink.
                # Keep the package contract byte-identical to the production one.
                try:
                    artifact_reference = str(artifact.relative_to(DEFAULT_ROOT))
                except ValueError:
                    # pathlib preserves an absolute right operand when the
                    # independent validator resolves ``root / reference``.
                    artifact_reference = str(artifact)
            exclusions.append({
                "pdf_page": pdf_page,
                "source_locator": source_locator(pages[(entry["source_id"], pdf_page)]),
                "ocr_artifact": artifact_reference,
                "ocr_receipt_sha256": receipt["receipt_sha256"],
                "ocr_output_sha256": receipt["output_sha256"],
                "ocr_staging_disposition": "no_text_detected",
                "review_status": "machine_extracted",
            })
        package_id = f"{parent_entry_id}:quality-recovery-01"
        package = {
            "schema_version": "1.0",
            "package_id": package_id,
            "work_id": f"structure-recovery:{package_id}:v1",
            "stage": "local_structure_recovery",
            "recovery_kind": "page_quality_with_terminal_no_text_exclusions",
            "parent_entry_id": parent_entry_id,
            "recovered_entry_id": parent_entry_id,
            "owner_shard": entry["owner_shard"],
            "source_id": entry["source_id"],
            "volume": entry["volume"],
            "pdf_pages": contract["pdf_pages"],
            "page_count": len(contract["pdf_pages"]),
            "sequence": 1,
            "sequence_count": 1,
            "book_taxon_candidate": entry["book_taxon_candidate"],
            "source_locators": [source_locator(pages[(entry["source_id"], page)]) for page in contract["pdf_pages"]],
            "ocr_exclusions": exclusions,
            "review_status": "candidate",
            "name_resolution_status": "unresolved",
            "layout_or_plate_claims_approved": False,
            "status": "planned_local_structure_recovery",
            "dependencies": ["primary_local_structure_batch_complete"],
            "route": {"maker": "local_qwen_structure", "checker": "deterministic_source_hash_schema_validator"},
            "proof_of_done": [
                "all proposed sections resolve to included exact page char locators and source hashes",
                "excluded page has a hash-valid terminal local OCR no-text receipt",
                "review_status remains machine_extracted and Taiwan name remains unresolved",
                "layout and plate claims remain unapproved",
            ],
            "forbidden": [
                "canonical_record_write", "canonical_chunk_write", "embedding_index_write",
                "source_pdf_write", "external_api", "taiwan_name_invention",
                "layout_or_plate_self_approval",
            ],
        }
        package["package_sha256"] = sha256_text(canonical_json(package))
        packages.append(package)

    for parent_entry_id, contract in TERMINAL_BOUNDARY_RECOVERY_CONTRACTS.items():
        entry = entries_by_id[parent_entry_id]
        tail_page = pages[(entry["source_id"], contract["trailing_plate_start_pdf_page"])]
        marker_start = tail_page["text"].find(contract["boundary_marker"])
        if marker_start < 0 or tail_page["quality"] == "usable":
            raise SystemExit(f"terminal boundary marker/quality mismatch: {parent_entry_id}")
        boundary_locator = source_locator(
            tail_page,
            char_start=marker_start,
            char_end=marker_start + len(contract["boundary_marker"]),
        )
        if any(
            pages[(entry["source_id"], page)]["quality"] != "usable"
            for child in contract["children"] for page in child["pdf_pages"]
        ):
            raise SystemExit(f"terminal recovery body page is not usable: {parent_entry_id}")
        for sequence, child in enumerate(contract["children"], 1):
            if len(child["pdf_pages"]) > 6:
                raise SystemExit(f"terminal recovery child exceeds six pages: {child['recovered_entry_id']}")
            package_id = f"{parent_entry_id}:boundary-recovery-{sequence:02d}"
            package = {
                "schema_version": "1.0",
                "package_id": package_id,
                "work_id": f"structure-recovery:{package_id}:v1",
                "stage": "local_structure_recovery",
                "recovery_kind": "terminal_body_boundary_split",
                "parent_entry_id": parent_entry_id,
                "recovered_entry_id": child["recovered_entry_id"],
                "owner_shard": entry["owner_shard"],
                "source_id": entry["source_id"],
                "volume": entry["volume"],
                "pdf_pages": child["pdf_pages"],
                "page_count": len(child["pdf_pages"]),
                "sequence": sequence,
                "sequence_count": len(contract["children"]),
                "book_taxon_candidate": child["book_taxon_candidate"],
                "source_locators": [source_locator(pages[(entry["source_id"], page)]) for page in child["pdf_pages"]],
                "terminal_boundary": {
                    "body_end_pdf_page": contract["body_end_pdf_page"],
                    "trailing_plate_start_pdf_page": contract["trailing_plate_start_pdf_page"],
                    "trailing_plate_end_pdf_page": entry["end_pdf_page"],
                    "excluded_trailing_page_count": entry["end_pdf_page"] - contract["trailing_plate_start_pdf_page"] + 1,
                    "boundary_marker": contract["boundary_marker"],
                    "boundary_source_locator": boundary_locator,
                    "basis": "source-text caption marker plus usable-to-nonusable quality transition; no visual inference",
                },
                "review_status": "candidate",
                "name_resolution_status": "unresolved",
                "layout_or_plate_claims_approved": False,
                "status": "planned_local_structure_recovery",
                "dependencies": ["primary_local_structure_batch_complete"],
                "route": {"maker": "local_qwen_structure", "checker": "deterministic_source_hash_schema_validator"},
                "proof_of_done": [
                    "body pages are at most six usable source-exact pages",
                    "trailing section exclusion has an exact textual boundary marker and hashes",
                    "each recovered taxon is a separate candidate rather than one merged plant",
                    "Taiwan name stays unresolved and layout/plate claims remain unapproved",
                ],
                "forbidden": [
                    "canonical_record_write", "canonical_chunk_write", "embedding_index_write",
                    "source_pdf_write", "external_api", "taiwan_name_invention",
                    "layout_or_plate_self_approval",
                ],
            }
            package["package_sha256"] = sha256_text(canonical_json(package))
            packages.append(package)
    return packages


def approved_record_refs(entry: dict) -> list[str]:
    refs = []
    for path in sorted((LAB / "data/records").glob("*.json")):
        record = read_json(path)
        evidence = {
            (item.get("source_id"), item.get("pdf_page"))
            for item in record.get("book_evidence", [])
        }
        if any((entry["source_id"], page) in evidence for page in entry.get("approved_overlap_pages", [])):
            refs.append(record.get("record_id", path.stem))
    return sorted(set(refs))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--maximum-continuation-pages", type=int, default=6)
    parser.add_argument("--require-maker-complete", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.maximum_continuation_pages <= 6:
        raise SystemExit("maximum continuation pages must be between 1 and 6")

    root = args.root
    manifest = read_json(root / "manifest.json")
    source_receipt_path = root / "source-receipt.json"
    source_receipt = read_json(source_receipt_path)
    SOURCE_PDF_HASHES.clear()
    SOURCE_PDF_HASHES.update({item["source_id"]: item["sha256"] for item in source_receipt["sources"]})
    expected_source_ids = {item["source_id"] for item in manifest["shards"]}
    if set(SOURCE_PDF_HASHES) != expected_source_ids or any(len(value) != 64 for value in SOURCE_PDF_HASHES.values()):
        raise SystemExit("source receipt does not cover the four frozen source IDs")
    source_receipt_sha256 = hashlib.sha256(source_receipt_path.read_bytes()).hexdigest()
    batch_status = read_json(root / "batch-status.json")
    pages: dict[tuple[str, int], dict] = {}
    entries: list[dict] = []
    for shard in manifest["shards"]:
        shard_root = root / "shards" / shard["shard_id"]
        for page in read_jsonl(shard_root / "inputs/pages.jsonl"):
            pages[(page["source_id"], page["pdf_page"])] = page
        entries.extend(read_jsonl(shard_root / "inputs/entries.jsonl"))
    if len(pages) != manifest["totals"]["pages"] or len(entries) != manifest["totals"]["detected_entries"]:
        raise SystemExit("frozen input totals do not match manifest")
    entries_by_id = {item["entry_id"]: item for item in entries}
    recovery_packages = build_content_recovery_packages(root, entries_by_id, pages)

    maker_complete = batch_status.get("status") == "complete"
    if args.require_maker_complete and not maker_complete:
        raise SystemExit("structure maker batch is not complete")

    checks = []
    continuation_checks = []
    recovery_checks = []
    repairs = []
    dispositions = []
    continuations = []
    embedding_candidates = []
    recovery_packages_by_parent: dict[str, list[dict]] = {}
    for package in recovery_packages:
        recovery_packages_by_parent.setdefault(package["parent_entry_id"], []).append(package)
    for entry in sorted(entries, key=lambda item: (item["volume"], item["start_pdf_page"], item["entry_id"])):
        base = {
            "schema_version": "1.0",
            "entry_id": entry["entry_id"],
            "owner_shard": entry["owner_shard"],
            "source_id": entry["source_id"],
            "volume": entry["volume"],
            "book_taxon_candidate": entry["book_taxon_candidate"],
            "input_disposition": entry["disposition"],
            "source_locators": entry_locators(entry, pages),
            "name_resolution_status": "unresolved",
            "layout_or_plate_claims_approved": False,
        }
        disposition = dict(base)
        if entry["disposition"] == "eligible_local_structure":
            receipt_path = (
                root / "shards" / entry["owner_shard"] / "maker/structure-drafts"
                / f"{entry['entry_id'].replace(':', '__')}.json"
            )
            if not receipt_path.is_file():
                disposition.update({
                    "terminal": False,
                    "terminal_disposition": "awaiting_local_structure_maker",
                    "embedding_ready_candidate": False,
                })
            else:
                receipt = read_json(receipt_path)
                errors, section_locators, warnings = validate_receipt(receipt, entry, pages)
                errors, section_locators, repair = apply_changed_strategy_repair(
                    entry, receipt, errors, section_locators, pages
                )
                if repair:
                    repairs.append(repair)
                    warnings.append("changed_strategy_deterministic_cross_page_repair")
                check = {
                    "schema_version": "1.0",
                    "entry_id": entry["entry_id"],
                    "owner_shard": entry["owner_shard"],
                    "receipt_path": str(receipt_path.relative_to(root)),
                    "receipt_sha256": receipt.get("receipt_sha256"),
                    "status": (
                        "pass_changed_strategy_repair" if repair and not errors
                        else ("pass" if not errors else "needs_review")
                    ),
                    "errors": errors,
                    "warnings": warnings,
                    "section_source_locators": section_locators,
                    "checked_at": now(),
                }
                check["check_sha256"] = sha256_text(canonical_json(check))
                checks.append(check)
                if errors:
                    disposition.update({
                        "terminal": maker_complete,
                        "terminal_disposition": (
                            "held_structure_validation_failure" if maker_complete
                            else "maker_receipt_needs_review_batch_running"
                        ),
                        "embedding_ready_candidate": False,
                        "validation_errors": errors,
                    })
                else:
                    plate_pending = any(locator["section_type"] == "plate_description" for locator in section_locators)
                    disposition.update({
                        "terminal": True,
                        "terminal_disposition": (
                            "structure_validated_text_candidate_plate_claims_held"
                            if plate_pending else "structure_validated_text_candidate"
                        ),
                        "embedding_ready_candidate": True,
                        "section_source_locators": section_locators,
                        "validation_warnings": warnings,
                    })
                    draft = receipt["draft"]
                    safe_sections = []
                    locators_by_section: dict[int, list[dict]] = {}
                    for locator in section_locators:
                        locators_by_section.setdefault(locator["section_index"], []).append(locator)
                    for section_index, section in enumerate(draft["sections"]):
                        if section["section_type"] == "plate_description":
                            continue
                        source_locators = locators_by_section.get(section_index, [])
                        if not source_locators:
                            continue
                        quotes = [locator.get("exact_source_quote", section.get("exact_source_quote")) for locator in source_locators]
                        if any(not isinstance(quote, str) or not quote for quote in quotes):
                            continue
                        clean_locators = []
                        for locator in source_locators:
                            clean_locator = dict(locator)
                            clean_locator.pop("exact_source_quote", None)
                            clean_locators.append(clean_locator)
                        safe_sections.append({
                            "section_type": section["section_type"],
                            "exact_source_quotes": quotes,
                            "normalized_text_candidate": section.get("normalized_text_candidate"),
                            "zh_tw_rendering_candidate": section.get("zh_tw_rendering_candidate"),
                            "source_locators": clean_locators,
                        })
                    if safe_sections:
                        candidate = {
                            "schema_version": "1.0",
                            "entry_id": entry["entry_id"],
                            "owner_shard": entry["owner_shard"],
                            "source_id": entry["source_id"],
                            "volume": entry["volume"],
                            "book_taxon_candidate": entry["book_taxon_candidate"],
                            "maker_receipt_sha256": receipt["receipt_sha256"],
                            "validation_check_sha256": check["check_sha256"],
                            "display_name": None,
                            "name_resolution": {"status": "unresolved", "sources": []},
                            "review_status": "machine_extracted",
                            "sections": safe_sections,
                            "excluded_plate_section_count": len(draft["sections"]) - len(safe_sections),
                            "layout_or_plate_claims_approved": False,
                            "canonical_write_allowed": False,
                            "embedding_call_performed": False,
                        }
                        candidate["candidate_sha256"] = sha256_text(canonical_json(candidate))
                        embedding_candidates.append(candidate)
            dispositions.append(disposition)
            continue

        if entry["disposition"] not in TERMINAL_SPECIAL:
            raise SystemExit(f"unknown input disposition: {entry['disposition']}")
        if entry["disposition"] == "hold_span_over_limit":
            packages = split_continuations(entry, pages, args.maximum_continuation_pages)
            continuations.extend(packages)
            package_results = []
            for package in packages:
                path = continuation_receipt_path(root, package["package_id"])
                if not path.is_file():
                    package_results.append({"package": package, "path": path, "receipt": None, "check": None})
                    continue
                receipt = read_json(path)
                receipt_errors, receipt_locators, receipt_warnings = validate_continuation_receipt(
                    receipt, package, pages
                )
                check = {
                    "schema_version": "1.0",
                    "package_id": package["package_id"],
                    "parent_entry_id": entry["entry_id"],
                    "owner_shard": entry["owner_shard"],
                    "package_sha256": package["package_sha256"],
                    "receipt_path": str(path.relative_to(root)),
                    "receipt_sha256": receipt.get("receipt_sha256"),
                    "status": "pass" if not receipt_errors else "needs_review",
                    "errors": receipt_errors,
                    "warnings": receipt_warnings,
                    "section_source_locators": receipt_locators,
                    "checked_at": now(),
                }
                check["check_sha256"] = sha256_text(canonical_json(check))
                continuation_checks.append(check)
                package_results.append({"package": package, "path": path, "receipt": receipt, "check": check})

            present_results = [item for item in package_results if item["receipt"] is not None]
            passing_results = [
                item for item in present_results
                if item["check"] is not None and item["check"]["status"] == "pass"
            ]
            continuation_base = {
                "continuation_package_ids": [item["package_id"] for item in packages],
                "continuation_receipts_found": len(present_results),
                "continuation_receipts_passed": len(passing_results),
                "continuation_receipts_required": len(packages),
            }
            if len(passing_results) != len(packages):
                failed_packages = [
                    item["package"]["package_id"] for item in present_results
                    if item["check"] is not None and item["check"]["status"] != "pass"
                ]
                disposition.update({
                    **continuation_base,
                    "terminal": False,
                    "terminal_disposition": (
                        "continuation_receipt_needs_review" if failed_packages
                        else "split_into_continuation_work_packages_awaiting_receipts"
                    ),
                    "embedding_ready_candidate": False,
                    "continuation_needs_review_package_ids": failed_packages,
                })
            else:
                merged_sections = []
                merged_by_key: dict[tuple, dict] = {}
                continuation_provenance = []
                excluded_plate_count = 0
                for item in sorted(passing_results, key=lambda value: value["package"]["sequence"]):
                    package = item["package"]
                    receipt = item["receipt"]
                    check = item["check"]
                    assert receipt is not None and check is not None
                    continuation_provenance.append({
                        "package_id": package["package_id"],
                        "package_sha256": package["package_sha256"],
                        "receipt_sha256": receipt["receipt_sha256"],
                        "validation_check_sha256": check["check_sha256"],
                    })
                    locators_by_section = {
                        locator["section_index"]: locator
                        for locator in check["section_source_locators"]
                    }
                    for section_index, section in enumerate(receipt["draft"]["sections"]):
                        locator = locators_by_section.get(section_index)
                        if locator is None:
                            continue
                        if section["section_type"] == "plate_description":
                            excluded_plate_count += 1
                            continue
                        quote = section.get("exact_source_quote")
                        if not isinstance(quote, str) or not quote:
                            continue
                        clean_locator = dict(locator)
                        key = (
                            section["section_type"], clean_locator["source_id"],
                            clean_locator["pdf_page"], clean_locator["char_start"],
                            clean_locator["char_end"], clean_locator["exact_text_sha256"],
                        )
                        span_provenance = {
                            "package_id": package["package_id"],
                            "package_sha256": package["package_sha256"],
                            "receipt_sha256": receipt["receipt_sha256"],
                            "validation_check_sha256": check["check_sha256"],
                            "package_section_index": section_index,
                        }
                        existing = merged_by_key.get(key)
                        if existing is not None:
                            if span_provenance not in existing["continuation_provenance"]:
                                existing["continuation_provenance"].append(span_provenance)
                            continue
                        merged = {
                            "section_type": section["section_type"],
                            "exact_source_quotes": [quote],
                            "normalized_text_candidate": section.get("normalized_text_candidate"),
                            "zh_tw_rendering_candidate": section.get("zh_tw_rendering_candidate"),
                            "source_locators": [clean_locator],
                            "continuation_provenance": [span_provenance],
                        }
                        merged_by_key[key] = merged
                        merged_sections.append(merged)
                if not merged_sections:
                    disposition.update({
                        **continuation_base,
                        "terminal": False,
                        "terminal_disposition": "continuation_receipts_passed_but_no_nonplate_text",
                        "embedding_ready_candidate": False,
                    })
                else:
                    receipt_chain_sha256 = sha256_text(canonical_json([
                        {"package_id": item["package_id"], "receipt_sha256": item["receipt_sha256"]}
                        for item in continuation_provenance
                    ]))
                    check_chain_sha256 = sha256_text(canonical_json([
                        {"package_id": item["package_id"], "validation_check_sha256": item["validation_check_sha256"]}
                        for item in continuation_provenance
                    ]))
                    disposition.update({
                        **continuation_base,
                        "terminal": True,
                        "terminal_disposition": "continuation_structure_validated",
                        "embedding_ready_candidate": True,
                        "continuation_receipt_chain_sha256": receipt_chain_sha256,
                        "continuation_validation_chain_sha256": check_chain_sha256,
                        "excluded_plate_section_count": excluded_plate_count,
                    })
                    candidate = {
                        "schema_version": "1.0",
                        "entry_id": entry["entry_id"],
                        "owner_shard": entry["owner_shard"],
                        "source_id": entry["source_id"],
                        "volume": entry["volume"],
                        "book_taxon_candidate": entry["book_taxon_candidate"],
                        "maker_receipt_sha256": receipt_chain_sha256,
                        "validation_check_sha256": check_chain_sha256,
                        "continuation_receipts": continuation_provenance,
                        "display_name": None,
                        "name_resolution": {"status": "unresolved", "sources": []},
                        "review_status": "machine_extracted",
                        "sections": merged_sections,
                        "excluded_plate_section_count": excluded_plate_count,
                        "layout_or_plate_claims_approved": False,
                        "canonical_write_allowed": False,
                        "embedding_call_performed": False,
                    }
                    candidate["candidate_sha256"] = sha256_text(canonical_json(candidate))
                    embedding_candidates.append(candidate)
        elif entry["disposition"] == "already_approved_overlap":
            disposition.update({
                "terminal": True,
                "terminal_disposition": "already_approved_overlap_tracked_no_duplicate_maker",
                "embedding_ready_candidate": False,
                "approved_overlap_pages": entry["approved_overlap_pages"],
                "approved_record_refs": approved_record_refs(entry),
            })
        elif entry["disposition"] == "hold_page_quality":
            packages = recovery_packages_by_parent[entry["entry_id"]]
            package = packages[0]
            path = root / "structure/recovery-maker-receipts" / f"{package['package_id'].replace(':', '__')}.json"
            if not path.is_file():
                disposition.update({
                    "terminal": False,
                    "terminal_disposition": "page_quality_recovery_awaiting_receipt",
                    "embedding_ready_candidate": False,
                    "content_recovery_package_ids": [package["package_id"]],
                    "requires_visual_review": False,
                    "next_allowed_action": "run the local source-exact recovery package",
                })
            else:
                receipt = read_json(path)
                receipt_errors, receipt_locators, receipt_warnings = validate_continuation_receipt(receipt, package, pages)
                check = {
                    "schema_version": "1.0", "package_id": package["package_id"],
                    "parent_entry_id": entry["entry_id"], "recovered_entry_id": package["recovered_entry_id"],
                    "owner_shard": entry["owner_shard"], "package_sha256": package["package_sha256"],
                    "receipt_path": str(path.relative_to(root)), "receipt_sha256": receipt.get("receipt_sha256"),
                    "status": "pass" if not receipt_errors else "needs_review", "errors": receipt_errors,
                    "warnings": receipt_warnings, "section_source_locators": receipt_locators, "checked_at": now(),
                }
                check["check_sha256"] = sha256_text(canonical_json(check))
                recovery_checks.append(check)
                candidate = recovery_candidate_from_pass(package, receipt, check) if not receipt_errors else None
                if candidate is None:
                    disposition.update({
                        "terminal": False,
                        "terminal_disposition": "page_quality_recovery_needs_review",
                        "embedding_ready_candidate": False,
                        "content_recovery_package_ids": [package["package_id"]],
                        "recovery_errors": receipt_errors or ["no_nonplate_text_candidate"],
                    })
                else:
                    disposition.update({
                        "terminal": True,
                        "terminal_disposition": "page_quality_structure_recovered",
                        "embedding_ready_candidate": True,
                        "content_recovery_package_ids": [package["package_id"]],
                        "recovered_entry_ids": [package["recovered_entry_id"]],
                        "recovery_receipt_sha256": receipt["receipt_sha256"],
                        "recovery_validation_check_sha256": check["check_sha256"],
                        "ocr_exclusions": package["ocr_exclusions"],
                    })
                    embedding_candidates.append(candidate)
        else:
            packages = sorted(recovery_packages_by_parent[entry["entry_id"]], key=lambda item: item["sequence"])
            results = []
            for package in packages:
                path = root / "structure/recovery-maker-receipts" / f"{package['package_id'].replace(':', '__')}.json"
                if not path.is_file():
                    results.append((package, None, None))
                    continue
                receipt = read_json(path)
                receipt_errors, receipt_locators, receipt_warnings = validate_continuation_receipt(receipt, package, pages)
                check = {
                    "schema_version": "1.0", "package_id": package["package_id"],
                    "parent_entry_id": entry["entry_id"], "recovered_entry_id": package["recovered_entry_id"],
                    "owner_shard": entry["owner_shard"], "package_sha256": package["package_sha256"],
                    "receipt_path": str(path.relative_to(root)), "receipt_sha256": receipt.get("receipt_sha256"),
                    "status": "pass" if not receipt_errors else "needs_review", "errors": receipt_errors,
                    "warnings": receipt_warnings, "section_source_locators": receipt_locators, "checked_at": now(),
                }
                check["check_sha256"] = sha256_text(canonical_json(check))
                recovery_checks.append(check)
                results.append((package, receipt, check))
            passing = [item for item in results if item[2] is not None and item[2]["status"] == "pass"]
            candidates = [
                recovery_candidate_from_pass(package, receipt, check)
                for package, receipt, check in passing
            ]
            if len(passing) != len(packages) or any(candidate is None for candidate in candidates):
                failed = [
                    package["package_id"] for package, receipt, check in results
                    if receipt is not None and check is not None and check["status"] != "pass"
                ]
                disposition.update({
                    "terminal": False,
                    "terminal_disposition": (
                        "terminal_boundary_recovery_needs_review" if failed
                        else "terminal_boundary_recovery_awaiting_receipts"
                    ),
                    "embedding_ready_candidate": False,
                    "content_recovery_package_ids": [package["package_id"] for package in packages],
                    "recovery_needs_review_package_ids": failed,
                    "recovered_entry_ids": [package["recovered_entry_id"] for package in packages],
                })
            else:
                disposition.update({
                    "terminal": True,
                    "terminal_disposition": "terminal_body_boundaries_recovered",
                    "embedding_ready_candidate": True,
                    "content_recovery_package_ids": [package["package_id"] for package in packages],
                    "recovered_entry_ids": [package["recovered_entry_id"] for package in packages],
                    "recovery_receipt_sha256s": [receipt["receipt_sha256"] for _, receipt, _ in passing],
                    "recovery_validation_check_sha256s": [check["check_sha256"] for _, _, check in passing],
                    "terminal_boundaries": [package["terminal_boundary"] for package in packages],
                })
                embedding_candidates.extend(candidate for candidate in candidates if candidate is not None)
        dispositions.append(disposition)

    disposition_by_entry = {}
    for disposition in dispositions:
        disposition["disposition_sha256"] = sha256_text(canonical_json(disposition))
        disposition_by_entry[disposition["entry_id"]] = disposition
    for candidate in embedding_candidates:
        candidate.pop("candidate_sha256", None)
        disposition_entry_id = candidate.get("source_parent_entry_id", candidate["entry_id"])
        candidate["source_disposition_sha256"] = disposition_by_entry[disposition_entry_id]["disposition_sha256"]
        candidate["candidate_sha256"] = sha256_text(canonical_json(candidate))

    disposition_counts = Counter(item["terminal_disposition"] for item in dispositions)
    terminal_count = sum(bool(item["terminal"]) for item in dispositions)
    awaiting = [item["entry_id"] for item in dispositions if not item["terminal"]]
    summary = {
        "schema_version": "1.0",
        "pipeline_id": manifest["pipeline_id"],
        "source_receipt_sha256": source_receipt_sha256,
        "checked_at": now(),
        "maker_batch_status": batch_status.get("status"),
        "detected_entries": len(entries),
        "terminal_entries": terminal_count,
        "nonterminal_entries": len(entries) - terminal_count,
        "maker_receipts_checked": len(checks),
        "maker_receipts_passed": sum(item["status"].startswith("pass") for item in checks),
        "maker_receipts_needs_review": sum(not item["status"].startswith("pass") for item in checks),
        "needs_review_entry_ids": [item["entry_id"] for item in checks if not item["status"].startswith("pass")],
        "continuation_parent_entries": sum(item["input_disposition"] == "hold_span_over_limit" for item in dispositions),
        "continuation_packages": len(continuations),
        "continuation_receipts_checked": len(continuation_checks),
        "continuation_receipts_passed": sum(item["status"] == "pass" for item in continuation_checks),
        "continuation_receipts_needs_review": sum(item["status"] != "pass" for item in continuation_checks),
        "continuation_needs_review_package_ids": [
            item["package_id"] for item in continuation_checks if item["status"] != "pass"
        ],
        "continuation_parent_entries_validated": sum(
            item["terminal_disposition"] == "continuation_structure_validated" for item in dispositions
        ),
        "approved_overlap_entries_tracked": sum(item["input_disposition"] == "already_approved_overlap" for item in dispositions),
        "page_quality_holds": sum(item["input_disposition"] == "hold_page_quality" for item in dispositions),
        "terminal_no_next_heading_holds": sum(item["input_disposition"] == "hold_terminal_no_next_heading" for item in dispositions),
        "content_recovery_packages": len(recovery_packages),
        "content_recovery_receipts_checked": len(recovery_checks),
        "content_recovery_receipts_passed": sum(item["status"] == "pass" for item in recovery_checks),
        "content_recovery_receipts_needs_review": sum(item["status"] != "pass" for item in recovery_checks),
        "content_recovery_needs_review_package_ids": [
            item["package_id"] for item in recovery_checks if item["status"] != "pass"
        ],
        "content_hold_parents_recovered": sum(
            item["terminal_disposition"] in {"page_quality_structure_recovered", "terminal_body_boundaries_recovered"}
            for item in dispositions
        ),
        "unresolved_content_holds": sum(
            item["input_disposition"] in {"hold_page_quality", "hold_terminal_no_next_heading"}
            and not item["terminal"]
            for item in dispositions
        ),
        "embedding_ready_text_candidates": len(embedding_candidates),
        "changed_strategy_repairs": len(repairs),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "awaiting_entry_ids": awaiting,
        "complete": terminal_count == len(entries) == 265 and maker_complete,
        "safety": {
            "taiwan_names_invented": False,
            "layout_or_plate_claims_approved": False,
            "canonical_writes": False,
            "embedding_calls": False,
            "external_api_calls": False,
        },
    }
    summary["summary_sha256"] = sha256_text(canonical_json(summary))

    write_jsonl(root / "checks/structure-validation.jsonl", checks)
    write_jsonl(root / "checks/continuation-validation.jsonl", continuation_checks)
    write_jsonl(root / "checks/content-recovery-validation.jsonl", recovery_checks)
    write_jsonl(root / "structure/deterministic-repairs.jsonl", repairs)
    write_json(root / "checks/integration-summary.json", summary)
    write_jsonl(root / "structure/continuation-work-packages.jsonl", continuations)
    write_jsonl(root / "structure/content-recovery-work-packages.jsonl", recovery_packages)
    write_jsonl(root / "integration/entry-dispositions.jsonl", dispositions)
    embedding_manifest = {
        "schema_version": "1.0",
        "pipeline_id": manifest["pipeline_id"],
        "source_receipt_sha256": source_receipt_sha256,
        "source_pdf_hashes": dict(sorted(SOURCE_PDF_HASHES.items())),
        "generated_at": now(),
        "status": "complete" if summary["complete"] else "incomplete_awaiting_local_maker",
        "candidate_count": len(embedding_candidates),
        "candidates": embedding_candidates,
        "excluded_entry_count": len(entries) - len(embedding_candidates),
        "canonical_write_allowed": False,
        "embedding_calls_performed": False,
        "vector_space_id": None,
        "name_resolution_policy": "unresolved until separately checked against Taiwan public authorities",
        "plate_policy": "plate sections excluded; no layout or image claim is approved",
    }
    embedding_manifest["manifest_sha256"] = sha256_text(canonical_json(embedding_manifest))
    write_json(root / "integration/embedding-ready-candidate-manifest.json", embedding_manifest)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
