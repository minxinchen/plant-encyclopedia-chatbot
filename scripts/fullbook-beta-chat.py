#!/usr/bin/env python3
"""Full-book beta retrieval and strictly evidence-bound bilingual answering.

The retriever is local (SQLite FTS5 plus Gemini embeddings). Generation is
optional and is constrained to the retrieved Köhler text. No web grounding or
external plant facts are enabled.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
DEFAULT_DB = LAB / "data/index/staging/plant-embeddings-fullbook-beta.sqlite"
DEFAULT_ENV = LAB.parents[1] / "secrets/plant-encyclopedia.env.local"
DEFAULT_QWEN_BASE_URL = "http://127.0.0.1:18080/v1"
DEFAULT_QWEN_MODEL = str(
    LAB.parents[1] / "services/qwen35-mlx/models/Qwen3.5-35B-A3B-6bit"
)
SIMPLIFIED_MARKERS = set("这书么药剂后发为里叶树个应当简体")
NON_KOHLER_DRUGS = (
    "阿斯匹靈", "阿司匹林", "aspirin", "metformin", "二甲雙胍", "二甲双胍",
    "acetaminophen", "paracetamol", "普拿疼", "ibuprofen", "布洛芬",
    "amoxicillin", "阿莫西林", "抗生素", "antibiotic",
)
OUT_OF_SCOPE_PATTERNS = (
    "星座", "天秤座", "牡羊座", "白羊座", "金牛座", "雙子座", "双子座", "巨蟹座",
    "獅子座", "狮子座", "處女座", "处女座", "天蠍座", "天蝎座", "射手座",
    "摩羯座", "水瓶座", "雙魚座", "双鱼座", "zodiac", "horoscope", "libra",
)
TAIWAN_NAME_SCOPES = {
    "taiwan_taxonomic_public", "taiwan_government_public",
    "taiwan_academic_public", "taiwan_public_fallback",
    # Compatibility for the eight approved baseline rows.
    "taiwan_public_name", "approved_taiwan_public",
}
MULTI_PLANT_PATTERNS = (
    "哪些植物", "什麼植物", "什么植物", "有哪些植物", "哪些藥用植物", "哪些药用植物",
    "哪些製劑", "哪些制剂", "which plants", "what plants", "which preparations",
    "plants are mentioned", "plants does the book",
)


def load_env_value(path: Path, key: str) -> str:
    if os.environ.get(key, "").strip():
        return os.environ[key].strip()
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def locale(question: str) -> str:
    if re.search(r"[\u3400-\u9fff]", question):
        return "zh-TW"
    return "en"


def lexical_query(question: str) -> str:
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}", question)
    for run in re.findall(r"[\u3400-\u9fff]{2,}", question):
        if len(run) <= 6:
            tokens.append(run)
        else:
            # unicode61 does not segment an unspaced Chinese question into
            # plant-name tokens. Longest-first n-grams recover exact Taiwan
            # names without introducing a language-specific dictionary.
            for width in (4, 3, 2):
                tokens.extend(run[index:index + width] for index in range(len(run) - width + 1))
    deduplicated = list(dict.fromkeys(tokens))[:32]
    safe = [token.replace('"', '""') for token in deduplicated]
    return " OR ".join(f'"{token}"' for token in safe)


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right)) / (
        math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    )


def embed(api_key: str, model: str, dimensions: int, text: str) -> list[float]:
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent",
        data=json.dumps({
            "model": f"models/{model}",
            "content": {"parts": [{"text": text}]},
            "outputDimensionality": dimensions,
        }).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        values = json.loads(response.read()).get("embedding", {}).get("values")
    if not isinstance(values, list) or len(values) != dimensions:
        raise RuntimeError("query embedding dimension mismatch")
    return [float(value) for value in values]


def retrieve(db_path: Path, question: str, api_key: str, top_k: int) -> list[dict[str, Any]]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    meta = dict(db.execute("SELECT key,value FROM embedding_meta"))
    allowed = tuple(meta.get("active_review_statuses", "approved").split(","))
    placeholders = ",".join("?" for _ in allowed)
    lex_scores: dict[str, float] = {}
    fts = lexical_query(question)
    has_fts = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedding_chunks_fts'"
    ).fetchone() is not None
    if fts and has_fts:
        for rank, row in enumerate(db.execute(
            "SELECT chunk_id,bm25(embedding_chunks_fts) score FROM embedding_chunks_fts "
            "WHERE embedding_chunks_fts MATCH ? ORDER BY score LIMIT 80", (fts,),
        )):
            # FTS5 bm25() is commonly negative and lower is better. Rank is
            # stable across score-scale changes and keeps exact lexical hits
            # meaningfully ordered before the semantic merge.
            lex_scores[row["chunk_id"]] = 1.0 / (1.0 + rank)
    query_text = f"task: question answering | query: {question}"
    query_vector = embed(api_key, meta["model"], int(meta["dimensions"]), query_text)
    candidates = []
    has_name_metadata = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='record_name_metadata'"
    ).fetchone() is not None
    source_sql = (
        "SELECT c.*,n.display_name_source_scope FROM embedding_chunks c "
        "LEFT JOIN record_name_metadata n ON n.record_id=c.record_id "
        if has_name_metadata else
        "SELECT c.*,'taiwan_public_name' display_name_source_scope FROM embedding_chunks c "
    )
    for row in db.execute(
        source_sql + f"WHERE c.profile_id=? AND c.review_status IN ({placeholders})",
        (meta["active_chunk_profile"], *allowed),
    ):
        semantic = cosine(query_vector, json.loads(row["embedding_json"]))
        lexical = lex_scores.get(row["chunk_id"], 0.0)
        candidates.append({
            "chunk_id": row["chunk_id"], "parent_chunk_id": row["parent_chunk_id"],
            "source_id": row["source_id"], "volume": row["volume"],
            "pdf_page": row["pdf_page"], "record_id": row["record_id"],
            "scientific_name": row["scientific_name"], "display_name": row["display_name"],
            "display_name_source_scope": row["display_name_source_scope"],
            "source_text": row["source_text"], "text_sha256": row["text_sha256"],
            "review_status": row["review_status"], "semantic_score": semantic,
            "lexical_score": lexical, "hybrid_score": semantic + 0.08 * lexical,
        })
    candidates.sort(key=lambda item: (-item["hybrid_score"], item["chunk_id"]))
    collapsed: dict[str, dict[str, Any]] = {}
    for item in candidates:
        collapsed.setdefault(item["parent_chunk_id"], item)
    hits = list(collapsed.values())[:top_k]
    db.close()
    return hits


def is_multi_plant_question(question: str) -> bool:
    folded = question.casefold()
    return any(pattern in folded for pattern in MULTI_PLANT_PATTERNS)


def diversify_by_record(evidence: list[dict[str, Any]], limit: int = 12,
                        per_record: int = 2) -> list[dict[str, Any]]:
    """Keep broad retrieval from being monopolized by one long plant entry."""
    counts: dict[str, int] = {}
    diversified: list[dict[str, Any]] = []
    for item in evidence:
        record_id = item["record_id"]
        if counts.get(record_id, 0) >= per_record:
            continue
        diversified.append(item)
        counts[record_id] = counts.get(record_id, 0) + 1
        if len(diversified) >= limit:
            break
    return diversified


def generate(api_key: str, question: str, response_locale: str,
             evidence: list[dict[str, Any]], model: str) -> str:
    excerpts = "\n\n".join(
        f"[E{index}] {item['source_id']} PDF p.{item['pdf_page']} | "
        f"{item['display_name'] or 'Chinese display name unresolved'} | "
        f"name scope: {item.get('display_name_source_scope') or 'unclassified'} | "
        f"{item['scientific_name']}\n"
        f"{item['source_text']}"
        for index, item in enumerate(evidence, 1)
    )
    language = "Traditional Chinese used in Taiwan" if response_locale == "zh-TW" else "English"
    prompt = f"""You are a constrained translator and summarizer for Köhler's botanical encyclopedia.
Answer in {language}. Use ONLY the evidence excerpts below. Never use your own botanical,
medical, astrological, or web knowledge. Preserve scientific names and Chinese display names.
At the first plant mention, include the evidence header's Chinese display name together with
the scientific name, even when the response language is English. If its name scope is
non_taiwan_traditional_fallback, explicitly label it as a non-Taiwan Traditional-Chinese fallback,
never as a Taiwan public name.
Copy every Latin drug, preparation, and substance name exactly; never invent a Chinese or
English gloss for names such as Extractum Hyoscyami. A Chinese-name-to-Latin-name pairing is
allowed only when that exact pairing appears in an evidence header.
Every factual sentence must end with one or more citations like [E1]. Medical-topic questions are
allowed, including questions phrased around the user's symptoms, but the answer must remain a
faithful historical Köhler summary. If the excerpts do not directly support an answer, output
exactly INSUFFICIENT_BOOK_EVIDENCE. Do not add modern diagnosis, dosage, efficacy, or safety claims.
Do not claim that a historic book use is medically safe.

Question: {question}

Evidence:
{excerpts}
"""
    if model == "local-qwen":
        return qwen_chat(prompt, 800)
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 800,
                                 "thinkingConfig": {"thinkingBudget": 0}},
        }).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read())
    candidates = payload.get("candidates", [])
    if not candidates:
        raise RuntimeError("generation returned no candidate")
    return "".join(
        part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])
    ).strip()


def generate_multi_selection(api_key: str, question: str,
                             evidence: list[dict[str, Any]], model: str) -> str:
    """Ask the model only to select exact supporting spans, not compose facts."""
    excerpts = "\n\n".join(
        f"[E{index}] record_id={item['record_id']} | {item['scientific_name']} | "
        f"{item['source_id']} PDF p.{item['pdf_page']}\n{item['source_text']}"
        for index, item in enumerate(evidence, 1)
    )
    prompt = f"""You are an evidence selector for Köhler's botanical encyclopedia.
The question asks for multiple plants or preparations. Use ONLY the excerpts below.
Evaluate EVERY evidence excerpt independently and return ALL qualifying records, not only the
single best match. Include a plant or its named preparation only when the excerpt itself
explicitly states its relationship to the question's subject. A list of drugs, preparations,
ingredients, or neighboring text is NOT proof of that relationship. A preparation may qualify
when the excerpt directly says that preparation has the requested relationship. Do not infer
relevance from retrieval rank or general knowledge.

Return strict JSON only. Include exactly one evaluation for every E number, in order:
{{"evaluations":[{{"evidence_id":1,"qualifies":true,"support_quote":"one exact contiguous quote copied from E1"}},{{"evidence_id":2,"qualifies":false,"support_quote":""}}]}}
For each qualifying item, support_quote must be copied exactly, apart from whitespace, and must
make the requested relationship intelligible. Never omit an E number and never combine excerpts.

Question: {question}

Evidence:
{excerpts}
"""
    if model == "local-qwen":
        return qwen_chat(prompt, 1800, json_mode=True)
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0, "maxOutputTokens": 1800,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read())
    candidates = payload.get("candidates", [])
    if not candidates:
        raise RuntimeError("multi-plant selection returned no candidate")
    return "".join(
        part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])
    ).strip()


def qwen_chat(prompt: str, max_tokens: int, json_mode: bool = False) -> str:
    """Call the local OpenAI-compatible Qwen API without sending evidence off-device."""
    base_url = os.environ.get("QWEN_API_BASE", DEFAULT_QWEN_BASE_URL).rstrip("/")
    model = os.environ.get("QWEN_API_MODEL", DEFAULT_QWEN_MODEL)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.loads(response.read())
    choices = body.get("choices", [])
    if not choices:
        raise RuntimeError("local Qwen returned no choice")
    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("local Qwen returned empty content")
    content = content.strip()
    if json_mode:
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
        if fenced:
            content = fenced.group(1).strip()
    return content


def citation_indices(text: str) -> set[int]:
    indices: set[int] = set()
    for marker in re.findall(r"\[E[\d,\sE]+\]", text):
        indices.update(int(value) for value in re.findall(r"\d+", marker))
    return indices


def citation_gate(text: str, evidence_count: int) -> tuple[bool, list[str]]:
    cited = citation_indices(text)
    invalid = sorted(index for index in cited if index < 1 or index > evidence_count)
    marker = r"\[E[\d,\sE]+\]"
    normalized = re.sub(rf"([。！？.!?])\s*({marker})", r" \2\1", text)
    policy_only = (
        "不是現代醫療", "不構成醫療", "非醫療建議", "諮詢合格醫療", "咨询合格医疗",
        "not modern medical", "not medical advice", "consult a qualified healthcare",
    )
    uncited: list[str] = []
    for line in normalized.splitlines():
        item = line.strip()
        if not item or item.rstrip().endswith((":", "：")):
            continue
        if any(phrase in item.casefold() for phrase in policy_only):
            continue
        # Remove complete factual clauses that end in an evidence marker. This
        # deliberately avoids splitting on periods inside names such as
        # "A. G. Nagle" or abbreviations such as "St. Vincent".
        residual = re.sub(rf".*?{marker}[。！？.!?]?(?:\s+|$)", "", item)
        if re.search(r"[A-Za-z\u3400-\u9fff]", residual):
            uncited.append(residual)
    return bool(cited) and not invalid and not uncited, uncited


def normalized_quote(text: str) -> str:
    # PDF text layers often split one printed word as ``brauch-\nbar``. Treat
    # only an intra-letter line-wrap hyphen as layout noise; keep real hyphens.
    dehyphenated = re.sub(r"(?<=[A-Za-zÀ-ÖØ-öø-ÿ])-\s+(?=[A-Za-zÀ-ÖØ-öø-ÿ])", "", text)
    return re.sub(r"\s+", " ", dehyphenated).strip()


def validate_multi_selection(raw: str, evidence: list[dict[str, Any]],
                             max_records: int = 6) -> list[dict[str, Any]]:
    """Fail closed unless every selected quote exists in a cited same-record excerpt."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if items is None and isinstance(payload, dict) and isinstance(payload.get("evaluations"), list):
        evaluations = payload["evaluations"]
        evaluation_ids = [
            item.get("evidence_id") for item in evaluations if isinstance(item, dict)
        ]
        if evaluation_ids != list(range(1, len(evidence) + 1)):
            return []
        items = [
            {
                "evidence_ids": [item.get("evidence_id")],
                "support_quote": item.get("support_quote"),
            }
            for item in evaluations
            if isinstance(item, dict) and item.get("qualifies") is True
        ]
    if not isinstance(items, list):
        return []
    verified: list[dict[str, Any]] = []
    seen_records: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        ids = item.get("evidence_ids")
        quote = item.get("support_quote")
        if not isinstance(ids, list) or not ids or not isinstance(quote, str):
            continue
        if not all(isinstance(index, int) and 1 <= index <= len(evidence) for index in ids):
            continue
        cited = [evidence[index - 1] for index in ids]
        record_ids = {entry["record_id"] for entry in cited}
        if len(record_ids) != 1:
            continue
        record_id = cited[0]["record_id"]
        if record_id in seen_records:
            continue
        normalized = normalized_quote(quote)
        if len(normalized) < 20 or not any(
            normalized in normalized_quote(entry["source_text"]) for entry in cited
        ):
            continue
        verified.append({
            "record_id": record_id,
            "evidence_ids": list(dict.fromkeys(ids)),
            "support_quote": normalized,
        })
        seen_records.add(record_id)
        if len(verified) >= max_records:
            break
    return verified


def evidence_identity(item: dict[str, Any], response_locale: str) -> str:
    scientific = item.get("scientific_name") or "Scientific name unresolved"
    display = item.get("display_name")
    scope = item.get("display_name_source_scope")
    if not display:
        return scientific
    if response_locale == "zh-TW":
        label = "非臺灣繁中備援名" if scope == "non_taiwan_traditional_fallback" else "臺灣公開名"
        return f"{display}（{scientific}；{label}）"
    label = (
        "non-Taiwan Traditional-Chinese fallback"
        if scope == "non_taiwan_traditional_fallback" else "Taiwan public name"
    )
    return f"{scientific} ({label}: {display})"


def format_multi_answer(verified: list[dict[str, Any]], evidence: list[dict[str, Any]],
                        response_locale: str) -> str:
    lines: list[str] = []
    for item in verified:
        primary = evidence[item["evidence_ids"][0] - 1]
        citations = "[" + ", ".join(f"E{index}" for index in item["evidence_ids"]) + "]"
        lines.append(f"- {evidence_identity(primary, response_locale)}：「{item['support_quote']}」{citations}")
    if response_locale == "zh-TW":
        return (
            "以下只列出原文直接支持問題關係的條目；引文保留原書語言：\n"
            + "\n".join(lines)
            + "\n以上是歷史文獻記載，不是現代醫療、用量或安全建議。"
        )
    return (
        "Only entries whose quoted text directly supports the requested relationship are listed; "
        "quotes remain in the book's language:\n" + "\n".join(lines)
        + "\nThese are historical book statements, not modern medical, dosage, or safety advice."
    )


def evidence_fallback(response_locale: str) -> str:
    if response_locale == "zh-TW":
        return "檢索到候選頁面，但沒有條目通過原文直接關係與逐字引文驗證，因此不彙整成答案。"
    return (
        "Candidate pages were retrieved, but no entry passed the direct-relationship and exact-quote "
        "checks, so they are not presented as an answer."
    )


def identity_prefix(response_locale: str, evidence: list[dict[str, Any]], text: str) -> str:
    """Deterministically expose the evidence header's name pair and source scope."""
    if not evidence:
        return ""
    primary = evidence[0]
    scientific = primary.get("scientific_name") or ""
    display = primary.get("display_name") or ""
    if not scientific or not display:
        return ""
    scope = primary.get("display_name_source_scope")
    fallback_label_present = (
        "非臺灣繁中備援名" in text
        if response_locale == "zh-TW"
        else "non-Taiwan" in text
    )
    if scientific in text and display in text and (
        scope != "non_taiwan_traditional_fallback" or fallback_label_present
    ):
        return ""
    if response_locale == "zh-TW":
        label = "非臺灣繁中備援名" if scope == "non_taiwan_traditional_fallback" else "中文顯示名"
        return f"{scientific}（{label}：{display}）[E1]。"
    label = (
        "non-Taiwan Traditional-Chinese fallback"
        if scope == "non_taiwan_traditional_fallback" else "Taiwan public name"
    )
    return f"{scientific} ({label}: {display}) [E1]. "


def entity_pair_gate(text: str, evidence: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    allowed = {
        (item["display_name"].casefold(), item["scientific_name"].casefold())
        for item in evidence if item.get("display_name") and item.get("scientific_name")
    }
    unauthorized: list[str] = []

    def compatible(actual: str, expected: str) -> bool:
        actual_tokens = re.findall(r"[a-z][a-z.-]*", actual.casefold())
        expected_tokens = re.findall(r"[a-z][a-z.-]*", expected.casefold())
        return len(actual_tokens) >= 2 and len(expected_tokens) >= 2 and actual_tokens[:2] == expected_tokens[:2]

    for match in re.finditer(
        r"([\u3400-\u9fff]{1,16})[（(]([A-Z][A-Za-z.-]*(?:\s+[A-Za-z.-]+){0,5})[）)]", text
    ):
        chinese = match.group(1).casefold()
        latin = match.group(2).casefold().strip()
        if not any(chinese == expected and compatible(latin, scientific) for expected, scientific in allowed):
            unauthorized.append(match.group(0))
    return not unauthorized, unauthorized


def display_name_gate(text: str, evidence: list[dict[str, Any]],
                      known_display_names: list[str]) -> tuple[bool, list[str]]:
    allowed = {item["display_name"] for item in evidence if item.get("display_name")}
    unauthorized = sorted({
        name for name in known_display_names
        if name and name in text and name not in allowed
    }, key=lambda value: (-len(value), value))
    return not unauthorized, unauthorized


def index_display_names(db_path: Path) -> list[str]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    names = [
        row[0] for row in db.execute(
            "SELECT DISTINCT display_name FROM embedding_chunks "
            "WHERE display_name IS NOT NULL AND display_name != ''"
        )
    ]
    db.close()
    return names


def answer(question: str, db: Path, env_file: Path, top_k: int, retrieval_only: bool,
           response_locale_override: str | None = None) -> dict[str, Any]:
    response_locale = response_locale_override or locale(question)
    result: dict[str, Any] = {
        "schema_version": "1.0", "request_id": str(uuid.uuid4()), "question": question,
        "response_locale": response_locale, "answer_status": "", "answer": "", "evidence": [],
        "external_generation_calls": 0, "external_embedding_calls": 0,
        "local_generation_calls": 0, "generation_provider": "none", "incremental_usd": 0,
    }
    folded = question.casefold()
    if any(pattern in folded for pattern in OUT_OF_SCOPE_PATTERNS):
        result["answer_status"] = "refused_outside_book_scope"
        result["answer"] = (
            "這種關聯不屬於本書可查證的植物內容，因此不回答。"
            if response_locale == "zh-TW" else
            "This relationship is not verifiable from the book, so I cannot answer it."
        )
        return result
    if any(name in folded for name in NON_KOHLER_DRUGS):
        result["answer_status"] = "refused_non_kohler_drug"
        result["answer"] = (
            "這不是本書的植物條目範圍，因此不回答。"
            if response_locale == "zh-TW" else
            "This is outside the plant entries in the book, so I cannot answer it."
        )
        return result
    api_key = load_env_value(env_file, "GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not loaded")
    broad = is_multi_plant_question(question)
    generation_provider = os.environ.get("PLANT_CHAT_GENERATION_PROVIDER", "gemini").strip().lower()
    if generation_provider not in {"gemini", "qwen"}:
        raise RuntimeError("unsupported generation provider")
    generation_model = "local-qwen" if generation_provider == "qwen" else "gemini-2.5-flash-lite"
    result["generation_provider"] = generation_provider
    retrieval_limit = max(top_k * 3, 18) if broad else top_k
    hits = retrieve(db, question, api_key, retrieval_limit)
    if broad:
        hits = diversify_by_record(hits, limit=12, per_record=2)
    result["external_embedding_calls"] = 1
    result["evidence"] = [{
        key: item[key] for key in (
            "source_id", "volume", "pdf_page", "record_id", "scientific_name",
            "display_name", "display_name_source_scope", "text_sha256", "review_status", "semantic_score",
            "lexical_score", "hybrid_score",
        )
    } | {"source_excerpt": item["source_text"]} for item in hits]
    result["display_names"] = [{
        "record_id": item["record_id"],
        "display_name": item.get("display_name"),
        "scientific_name": item["scientific_name"],
        "source_scope": item.get("display_name_source_scope"),
        "is_taiwan_public": item.get("display_name_source_scope") in TAIWAN_NAME_SCOPES,
    } for item in hits if item.get("display_name")]
    if retrieval_only:
        result["answer_status"] = "retrieval_only"
        return result
    if broad:
        raw_selection = generate_multi_selection(api_key, question, hits, generation_model)
        result["local_generation_calls" if generation_provider == "qwen" else "external_generation_calls"] = 1
        verified = validate_multi_selection(raw_selection, hits)
        result["answer_mode"] = "verified_multi_plant"
        result["verified_items"] = verified
        if verified:
            result["answer_status"] = "answerable_from_book"
            result["answer"] = format_multi_answer(verified, hits, response_locale)
        else:
            result["answer_status"] = "evidence_fallback"
            result["answer"] = evidence_fallback(response_locale)
        return result
    text = generate(api_key, question, response_locale, hits, generation_model)
    result["local_generation_calls" if generation_provider == "qwen" else "external_generation_calls"] = 1
    if text == "INSUFFICIENT_BOOK_EVIDENCE":
        result["answer_status"] = "not_in_book_or_insufficient"
        result["answer"] = (
            "目前找不到足以回答的書中證據。" if response_locale == "zh-TW"
            else "The book evidence retrieved is insufficient to answer this question."
        )
    else:
        entities_valid, unauthorized_pairs = entity_pair_gate(text, hits)
        display_names_valid, unauthorized_names = display_name_gate(
            text, hits, index_display_names(db)
        )
        citations_valid, uncited_sentences = citation_gate(text, len(hits))
        if not entities_valid:
            result["answer_status"] = "generation_failed_entity_pair_gate"
            result["answer"] = (
                "生成內容加入了書證未授權的名稱對應，因此不顯示。"
                if response_locale == "zh-TW" else
                "The generated response introduced an unsupported name pairing and is withheld."
            )
            result["entity_pair_gate_rejection_count"] = len(unauthorized_pairs)
        elif not display_names_valid:
            result["answer_status"] = "generation_failed_display_name_gate"
            result["answer"] = (
                "生成內容加入了本次書證未提供的臺灣植物名稱，因此不顯示。"
                if response_locale == "zh-TW" else
                "The generated response introduced a Taiwan plant name absent from the retrieved evidence and is withheld."
            )
            result["display_name_gate_rejection_count"] = len(unauthorized_names)
        elif not citations_valid:
            result["answer_status"] = "generation_failed_citation_gate"
            result["answer"] = (
                "生成內容未通過逐句書證引用檢查，因此不顯示。" if response_locale == "zh-TW"
                else "The generated response failed the book-citation gate and is withheld."
            )
            result["citation_gate_uncited_sentence_count"] = len(uncited_sentences)
        else:
            result["answer_status"] = "answerable_from_book"
            text = identity_prefix(response_locale, hits, text) + text
            if response_locale == "zh-TW":
                result["answer"] = (
                    "以下僅整理這本歷史文獻的記載：" + text
                    + " 以上不是現代醫療或安全建議。"
                )
            else:
                result["answer"] = (
                    "The following only summarizes this historical book: " + text
                    + " This is not modern medical or safety advice."
                )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--retrieval-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        answer(args.question, args.database, args.env_file, args.top_k, args.retrieval_only),
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
