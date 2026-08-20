#!/usr/bin/env python3
"""Build source-aligned entry-boundary evidence for long Köhler spans.

The four source PDF filenames contain their Internet Archive identifiers.  This
tool downloads the matching IA metadata and DjVu XML, verifies the advertised
size/SHA-1, aligns it one-to-one with the frozen local PDF pages, and emits a
compact staging overlay.  It never changes frozen inputs, canonical records,
chunks, embeddings, indexes, Taiwan names, or layout/image approvals.

author: Codex (GPT-5)
date: 2026-08-20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo


LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"
TZ = ZoneInfo("Asia/Taipei")
IA_ID = re.compile(r"(mobot\d+)", re.IGNORECASE)
BINOMIAL_AT_START = re.compile(
    r"^\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüßÀ-ÖØ-öø-ÿ]{2,})"
    r"\s+([A-Za-zÄÖÜäöüßÀ-ÖØ-öø-ÿ][A-Za-zÄÖÜäöüßÀ-ÖØ-öø-ÿ-]{2,})\b"
)
HEADING_STOPWORDS = {
    "Abbildung", "Anatomisches", "Anhang", "Anwendung", "Bestandtheile",
    "Bestandteile", "Blüthezeit", "Beschreibung", "Familie", "Goldene",
    "Köhler", "Litteratur", "Name", "Tafelbeschreibung", "Verbreitung",
    "Vorkommen",
}
SECOND_TOKEN_STOPWORDS = {
    "der", "des", "die", "ein", "eine", "für", "mit", "nach", "und", "von",
}
GNFINDER_API = "https://finder.globalnames.org/api/v1/find"
GNVERIFIER_API = "https://verifier.globalnames.org/api/v1/verifications/"


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def write_json(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    atomic_write(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def fetch_bytes(url: str, attempts: int = 5) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "kohler-boundary-evidence/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 15))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last_error}")


def fetch_json(url: str) -> dict:
    return json.loads(fetch_bytes(url))


def post_multipart(url: str, fields: dict[str, str]) -> dict:
    boundary = f"----kohler-{hashlib.sha256(canonical_json(fields).encode()).hexdigest()[:24]}"
    parts = []
    for key, value in fields.items():
        parts.extend([
            f"--{boundary}\r\n",
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n',
            f"{value}\r\n",
        ])
    parts.append(f"--{boundary}--\r\n")
    request = urllib.request.Request(
        url,
        data="".join(parts).encode("utf-8"),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "kohler-boundary-evidence/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def ensure_asset(cache_dir: Path, url: str, size: int, sha1: str, offline: bool) -> Path:
    path = cache_dir / url.rsplit("/", 1)[-1]
    if path.is_file() and path.stat().st_size == size and sha1_file(path) == sha1:
        return path
    if offline:
        raise SystemExit(f"verified cache unavailable in offline mode: {path}")
    payload = fetch_bytes(url)
    if len(payload) != size or hashlib.sha1(payload).hexdigest() != sha1:
        raise SystemExit(f"downloaded IA asset failed size/SHA-1 gate: {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)
    return path


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def canonical_taxon(value: str | None) -> str | None:
    if not value:
        return None
    match = BINOMIAL_AT_START.match(normalize_space(value))
    if not match:
        return None
    return f"{match.group(1).casefold()} {match.group(2).casefold()}"


def normalized_query(value: str) -> str:
    match = BINOMIAL_AT_START.match(normalize_space(value))
    if not match:
        return value
    return f"{match.group(1)} {match.group(2).lower()}"


def scientific_name_gate(
    candidate: str | None, cache: dict[str, dict], offline: bool
) -> dict | None:
    if not candidate:
        return None
    query = normalized_query(candidate)
    if query in cache:
        return cache[query]
    if offline:
        raise SystemExit(f"Global Names cache unavailable in offline mode: {query}")

    finder = post_multipart(GNFINDER_API, {"text": query, "verification": "false"})
    verifier = fetch_json(GNVERIFIER_API + urllib.parse.quote(query, safe=""))
    finder_names = [
        {
            "name": item.get("name"),
            "cardinality": item.get("cardinality"),
            "start": item.get("start"),
            "end": item.get("end"),
            "odds_log10": item.get("oddsLog10"),
        }
        for item in finder.get("names", [])
    ]
    finder_exact = any(
        item.get("cardinality", 0) >= 2
        and item.get("start") == 0
        and canonical_taxon(item.get("name")) == canonical_taxon(query)
        for item in finder_names
    )
    verification = (verifier.get("names") or [{}])[0]
    best = verification.get("bestResult") or {}
    verifier_exact = (
        verification.get("cardinality", 0) >= 2
        and verification.get("matchType") == "Exact"
        and best.get("matchType") == "Exact"
        and best.get("matchedCardinality", 0) >= 2
    )
    result = {
        "query": query,
        "finder_url": GNFINDER_API,
        "finder_version": finder.get("metadata", {}).get("gnfinderVersion"),
        "finder_exact_binomial_at_start": finder_exact,
        "finder_names": finder_names,
        "verifier_url": GNVERIFIER_API + urllib.parse.quote(query, safe=""),
        "verifier_match_type": verification.get("matchType"),
        "verifier_cardinality": verification.get("cardinality"),
        "verifier_best_match_type": best.get("matchType"),
        "verifier_matched_name": best.get("matchedCanonicalSimple"),
        "verifier_matched_cardinality": best.get("matchedCardinality"),
        "exact_species_gate": finder_exact and verifier_exact,
    }
    cache[query] = result
    return result


def scientific_name_score(result: dict | None) -> int:
    if not result:
        return 0
    if result.get("exact_species_gate"):
        return 3
    if result.get("verifier_match_type") == "Exact" and result.get("verifier_cardinality", 0) >= 2:
        return 2
    if result.get("verifier_match_type") == "Fuzzy" and result.get("verifier_cardinality", 0) >= 2:
        return 1
    return 0


def candidate_from_lines(lines: list[dict]) -> dict | None:
    first_lines = [line for line in lines if line["text"]][:12]
    context = "\n".join(line["text"] for line in lines[:24])
    structural = sorted({
        signal for signal, pattern in {
            "familie": r"\bFamilie\b",
            "synonym": r"\bSyn\.?\s*[:.]?",
            "gattung": r"\bGattung\b",
            "beschreibung": r"\bBeschreibung\b",
        }.items() if re.search(pattern, context, re.IGNORECASE)
    })
    for order, line in enumerate(first_lines):
        match = BINOMIAL_AT_START.match(line["text"])
        if not match:
            continue
        genus, species = match.groups()
        if genus in HEADING_STOPWORDS or species.casefold() in SECOND_TOKEN_STOPWORDS:
            continue
        return {
            "taxon_candidate": f"{genus} {species}",
            "heading_line": line["text"],
            "line_order": order,
            "word_confidence_median": line.get("word_confidence_median"),
            "line_bbox": line.get("bbox"),
            "structural_signals": structural,
        }
    return None


def local_lines(text: str) -> list[dict]:
    return [{"text": normalize_space(line)} for line in text.splitlines() if normalize_space(line)]


def parse_coords(value: str) -> tuple[int, int, int, int] | None:
    try:
        values = tuple(int(item) for item in value.split(","))
    except (TypeError, ValueError):
        return None
    return values if len(values) == 4 else None


def parse_djvu_pages(path: Path) -> list[dict]:
    pages: list[dict] = []
    for _event, element in ET.iterparse(path, events=("end",)):
        if element.tag != "OBJECT":
            continue
        lines = []
        for line_node in element.iter("LINE"):
            words = []
            confidences = []
            boxes = []
            for word in line_node.findall("WORD"):
                token = normalize_space(word.text or "")
                if not token:
                    continue
                words.append(token)
                try:
                    confidences.append(int(word.attrib.get("x-confidence", "")))
                except ValueError:
                    pass
                box = parse_coords(word.attrib.get("coords", ""))
                if box:
                    boxes.append(box)
            if not words:
                continue
            bbox = None
            if boxes:
                bbox = [
                    min(box[0] for box in boxes), max(box[1] for box in boxes),
                    max(box[2] for box in boxes), min(box[3] for box in boxes),
                ]
            lines.append({
                "text": " ".join(words),
                "bbox": bbox,
                "word_confidence_median": median(confidences) if confidences else None,
            })
        page_number = len(pages) + 1
        pages.append({
            "pdf_page": page_number,
            "width": int(element.attrib.get("width", "0") or 0),
            "height": int(element.attrib.get("height", "0") or 0),
            "lines": lines,
        })
        element.clear()
    return pages


def parse_bhl_names(path: Path) -> dict[int, list[dict]]:
    pages: dict[int, list[dict]] = {}
    seen: dict[int, set[tuple[str, str | None]]] = {}
    for _event, element in ET.iterparse(path, events=("end",)):
        if element.tag != "name":
            continue
        match = re.search(r"_(\d+)$", element.attrib.get("map", ""))
        if not match:
            element.clear()
            continue
        page = int(match.group(1))
        found = normalize_space(element.attrib.get("found", ""))
        confirmed = normalize_space(element.attrib.get("confirmed", "")) or None
        if found:
            key = (found, confirmed)
            seen.setdefault(page, set())
            if key not in seen[page]:
                pages.setdefault(page, []).append({
                    "found": found,
                    "confirmed": confirmed,
                    "bhl_page_url": element.attrib.get("bhlurl"),
                })
                seen[page].add(key)
        element.clear()
    return pages


def bhl_name_gate(candidate: str | None, scientific: dict | None, names: list[dict]) -> dict:
    candidate_taxon = canonical_taxon(candidate)
    verifier_taxon = canonical_taxon((scientific or {}).get("verifier_matched_name"))
    matches = []
    for item in names:
        found_taxon = canonical_taxon(item.get("found"))
        confirmed_taxon = canonical_taxon(item.get("confirmed"))
        if candidate_taxon and candidate_taxon in {found_taxon, confirmed_taxon}:
            matches.append(item)
        elif verifier_taxon and verifier_taxon in {found_taxon, confirmed_taxon}:
            matches.append(item)
    return {
        "page_name_count": len(names),
        "candidate_or_verifier_match": bool(matches),
        "matches": matches[:12],
    }


def infer_bhl_page_offset(
    source_id: str,
    long_entries: list[dict],
    frozen_pages: dict[tuple[str, int], dict],
    ia_pages: list[dict],
    bhl_names: dict[int, list[dict]],
) -> tuple[int, dict[int, int]]:
    scores: dict[int, int] = {}
    source_entries = [item for item in long_entries if item["source_id"] == source_id]
    for offset in range(-2, 3):
        score = 0
        for entry in source_entries:
            for page in entry["pdf_pages"]:
                local = candidate_from_lines(local_lines(frozen_pages[(source_id, page)]["text"]))
                external = candidate_from_lines(ia_pages[page - 1]["lines"])
                candidate = (external or local or {}).get("taxon_candidate")
                candidate_taxon = canonical_taxon(candidate)
                if not candidate_taxon:
                    continue
                mapped_page = page + offset
                current_match = any(
                    candidate_taxon in {
                        canonical_taxon(name.get("found")),
                        canonical_taxon(name.get("confirmed")),
                    }
                    for name in bhl_names.get(mapped_page, [])
                )
                previous_match = any(
                    candidate_taxon in {
                        canonical_taxon(name.get("found")),
                        canonical_taxon(name.get("confirmed")),
                    }
                    for name in bhl_names.get(mapped_page - 1, [])
                )
                if current_match:
                    score += 2 if not previous_match else 1
        scores[offset] = score
    best = max(scores.values())
    winners = [offset for offset, score in scores.items() if score == best]
    if len(winners) != 1 or best == 0:
        raise SystemExit(f"BHL page-name alignment is ambiguous for {source_id}: {scores}")
    return winners[0], scores


def load_scope(root: Path) -> tuple[dict[tuple[str, int], dict], list[dict]]:
    pages: dict[tuple[str, int], dict] = {}
    long_entries = []
    for shard in sorted((root / "shards").glob("S*")):
        for page in read_jsonl(shard / "inputs/pages.jsonl"):
            pages[(page["source_id"], page["pdf_page"])] = page
        for entry in read_jsonl(shard / "inputs/entries.jsonl"):
            if entry["disposition"] == "hold_span_over_limit":
                long_entries.append(entry)
    return pages, sorted(long_entries, key=lambda item: (item["volume"], item["start_pdf_page"]))


def ia_identity(source: dict) -> str:
    match = IA_ID.search(Path(source["path"]).name)
    if not match:
        raise SystemExit(f"source path lacks an Internet Archive identifier: {source['source_id']}")
    return match.group(1).lower()


def asset_contract(metadata: dict, suffix: str) -> dict:
    matches = [item for item in metadata.get("files", []) if item.get("name", "").endswith(suffix)]
    if len(matches) != 1:
        raise SystemExit(f"IA metadata does not contain exactly one {suffix} asset")
    asset = matches[0]
    if not asset.get("size") or not asset.get("sha1"):
        raise SystemExit(f"IA asset lacks size/SHA-1: {asset.get('name')}")
    return {"name": asset["name"], "size": int(asset["size"]), "sha1": asset["sha1"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--source-manifest", type=Path, default=LAB / "data/source-manifest.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_ROOT / "boundary-evidence-v1")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    source_manifest = read_json(args.source_manifest)
    sources = {item["source_id"]: item for item in source_manifest["files"]}
    frozen_pages, long_entries = load_scope(args.root)
    if len(long_entries) != 18:
        raise SystemExit(f"expected 18 frozen over-limit parents, found {len(long_entries)}")

    cache_dir = args.output / "cache"
    global_names_cache_path = cache_dir / "global-names-cache.json"
    global_names_cache = (
        read_json(global_names_cache_path) if global_names_cache_path.is_file() else {}
    )
    assets_manifest = []
    ia_pages_by_source: dict[str, list[dict]] = {}
    bhl_names_by_source: dict[str, dict[int, list[dict]]] = {}
    for source_id, source in sorted(sources.items(), key=lambda item: item[1]["volume"]):
        identifier = ia_identity(source)
        metadata_url = f"https://archive.org/metadata/{identifier}"
        metadata_cache = cache_dir / f"{identifier}_metadata.json"
        if args.offline:
            if not metadata_cache.is_file():
                raise SystemExit(f"metadata cache unavailable in offline mode: {metadata_cache}")
            metadata = read_json(metadata_cache)
        else:
            metadata = fetch_json(metadata_url)
            write_json(metadata_cache, metadata)
        if metadata.get("metadata", {}).get("identifier") != identifier:
            raise SystemExit(f"IA identifier mismatch: {source_id}")
        djvu = asset_contract(metadata, "_djvu.xml")
        bhl_names = asset_contract(metadata, "_names.xml")
        djvu_url = f"https://archive.org/download/{identifier}/{djvu['name']}"
        bhl_names_url = f"https://archive.org/download/{identifier}/{bhl_names['name']}"
        djvu_path = ensure_asset(cache_dir, djvu_url, djvu["size"], djvu["sha1"], args.offline)
        bhl_names_path = ensure_asset(
            cache_dir, bhl_names_url, bhl_names["size"], bhl_names["sha1"], args.offline
        )
        parsed_pages = parse_djvu_pages(djvu_path)
        if len(parsed_pages) != source["pages"]:
            raise SystemExit(
                f"IA/PDF page-count mismatch for {source_id}: IA={len(parsed_pages)} local={source['pages']}"
            )
        ia_pages_by_source[source_id] = parsed_pages
        bhl_names_by_source[source_id] = parse_bhl_names(bhl_names_path)
        assets_manifest.append({
            "source_id": source_id,
            "volume": source["volume"],
            "ia_identifier": identifier,
            "metadata_url": metadata_url,
            "djvu_url": djvu_url,
            "djvu_size": djvu["size"],
            "djvu_sha1": djvu["sha1"],
            "bhl_names_url": bhl_names_url,
            "bhl_names_size": bhl_names["size"],
            "bhl_names_sha1": bhl_names["sha1"],
            "aligned_page_count": len(parsed_pages),
        })

    bhl_page_offsets = {}
    for source_id in sorted(sources):
        offset, scores = infer_bhl_page_offset(
            source_id,
            long_entries,
            frozen_pages,
            ia_pages_by_source[source_id],
            bhl_names_by_source[source_id],
        )
        bhl_page_offsets[source_id] = offset
        asset = next(item for item in assets_manifest if item["source_id"] == source_id)
        asset["bhl_names_page_offset"] = offset
        asset["bhl_names_alignment_scores"] = {str(key): value for key, value in scores.items()}

    evidence = []
    for entry in long_entries:
        for pdf_page in entry["pdf_pages"]:
            local_page = frozen_pages[(entry["source_id"], pdf_page)]
            ia_page = ia_pages_by_source[entry["source_id"]][pdf_page - 1]
            local = candidate_from_lines(local_lines(local_page["text"]))
            external = candidate_from_lines(ia_page["lines"])
            local_taxon = canonical_taxon(local["taxon_candidate"] if local else None)
            external_taxon = canonical_taxon(external["taxon_candidate"] if external else None)
            agreement = bool(local_taxon and external_taxon and local_taxon == external_taxon)
            local_scientific = scientific_name_gate(
                local["taxon_candidate"] if local else None,
                global_names_cache,
                args.offline,
            )
            external_scientific = scientific_name_gate(
                external["taxon_candidate"] if external else None,
                global_names_cache,
                args.offline,
            )
            if scientific_name_score(local_scientific) > scientific_name_score(external_scientific):
                preferred, scientific_name, preferred_source = local, local_scientific, "local"
                other_scientific = external_scientific
            else:
                preferred, scientific_name, preferred_source = external or local, external_scientific or local_scientific, "internet_archive" if external else "local"
                other_scientific = local_scientific
            bhl_map_page = pdf_page + bhl_page_offsets[entry["source_id"]]
            bhl_page_names = bhl_names_by_source[entry["source_id"]].get(bhl_map_page, [])
            bhl_name = bhl_name_gate(
                preferred["taxon_candidate"] if preferred else None,
                scientific_name,
                bhl_page_names,
            )
            bhl_name["bhl_map_page"] = bhl_map_page
            bhl_name["pdf_to_bhl_map_offset"] = bhl_page_offsets[entry["source_id"]]
            is_parent_start = pdf_page == entry["start_pdf_page"]
            structural = bool(
                (local or {}).get("structural_signals") or (external or {}).get("structural_signals")
            )
            source_disagreement_resolved = bool(
                local_taxon
                and external_taxon
                and local_taxon != external_taxon
                and scientific_name
                and scientific_name.get("exact_species_gate")
                and not (other_scientific or {}).get("exact_species_gate")
                and min(
                    (local or {}).get("line_order", 99),
                    (external or {}).get("line_order", 99),
                ) <= 1
            )
            exact_confirmed = bool(
                scientific_name
                and scientific_name["exact_species_gate"]
                and (
                    agreement
                    or bhl_name["candidate_or_verifier_match"]
                    or source_disagreement_resolved
                )
            )
            fuzzy_bhl_bridge = bool(
                scientific_name
                and scientific_name.get("verifier_match_type") == "Fuzzy"
                and scientific_name.get("verifier_matched_cardinality", 0) >= 2
                and bhl_name["candidate_or_verifier_match"]
                and (preferred or {}).get("line_order", 99) <= 1
            )
            if (
                structural
                and (exact_confirmed or fuzzy_bhl_bridge)
            ):
                decision = "confirmed_parent_heading" if is_parent_start else "confirmed_hidden_heading"
            elif local_taxon or external_taxon:
                decision = "candidate_needs_secondary_evidence"
            else:
                decision = "no_page_heading_detected"
            row = {
                "schema_version": "1.0",
                "parent_entry_id": entry["entry_id"],
                "owner_shard": entry["owner_shard"],
                "source_id": entry["source_id"],
                "volume": entry["volume"],
                "pdf_page": pdf_page,
                "is_frozen_parent_start": is_parent_start,
                "frozen_parent_taxon": entry["book_taxon_candidate"],
                "local_page_text_sha256": local_page["text_sha256"],
                "local_heading": local,
                "ia_heading": external,
                "boundary_taxon_candidate": preferred["taxon_candidate"] if preferred else None,
                "canonical_taxon_agreement": agreement,
                "preferred_heading_source": preferred_source,
                "candidate_selection": {
                    "local_scientific_score": scientific_name_score(local_scientific),
                    "ia_scientific_score": scientific_name_score(external_scientific),
                    "source_disagreement_resolved_by_exact_name_gate": source_disagreement_resolved,
                },
                "scientific_name_evidence": scientific_name,
                "bhl_page_name_evidence": bhl_name,
                "boundary_gate": {
                    "structural_signal": structural,
                    "exact_confirmed": exact_confirmed,
                    "fuzzy_bhl_bridge": fuzzy_bhl_bridge,
                    "source_disagreement_resolved": source_disagreement_resolved,
                },
                "decision": decision,
                "requires_visual_review": decision == "candidate_needs_secondary_evidence",
                "canonical_write_allowed": False,
                "taiwan_name_resolution_allowed": False,
                "layout_or_image_claims_approved": False,
            }
            row["evidence_sha256"] = sha256_text(canonical_json(row))
            evidence.append(row)

    write_json(global_names_cache_path, global_names_cache)

    decision_counts = Counter(item["decision"] for item in evidence)
    hidden = [item for item in evidence if item["decision"] == "confirmed_hidden_heading"]
    secondary = [item for item in evidence if item["decision"] == "candidate_needs_secondary_evidence"]
    output_manifest = {
        "schema_version": "1.0",
        "pipeline_id": "kohler-boundary-evidence-v1",
        "generated_at": now(),
        "scope": {
            "source_volumes": len(sources),
            "frozen_pdf_pages": sum(item["pages"] for item in sources.values()),
            "over_limit_parents": len(long_entries),
            "evaluated_pages": len(evidence),
        },
        "assets": assets_manifest,
        "decision_counts": dict(sorted(decision_counts.items())),
        "confirmed_hidden_heading_count": len(hidden),
        "secondary_review_count": len(secondary),
        "confirmed_hidden_headings": [
            {
                "parent_entry_id": item["parent_entry_id"],
                "source_id": item["source_id"],
                "pdf_page": item["pdf_page"],
                "taxon_candidate": item["boundary_taxon_candidate"],
                "evidence_sha256": item["evidence_sha256"],
            }
            for item in hidden
        ],
        "safety": {
            "canonical_writes": False,
            "embedding_calls": False,
            "taiwan_names_resolved": False,
            "layout_or_image_claims_approved": False,
            "external_text_used_as_book_fact": False,
            "global_names_used_only_for_boundary_validation": True,
        },
    }
    output_manifest["manifest_sha256"] = sha256_text(canonical_json(output_manifest))
    write_jsonl(args.output / "boundary-evidence.jsonl", evidence)
    write_json(args.output / "manifest.json", output_manifest)
    print(json.dumps(output_manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
