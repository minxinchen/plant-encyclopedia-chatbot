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
SIMPLIFIED_MARKERS = set("这书么药剂后发为里叶树个应当简体")
MEDICAL_PATTERNS = (
    "我便秘", "我流血", "我應該", "我应该", "可以吃", "能吃", "可以用", "能用",
    "劑量", "剂量", "用量", "一天吃", "should i take", "can i take", "can i use",
    "what dose", "dosage", "treat my", "i am bleeding",
)
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
Every factual sentence must end with one or more citations like [E1]. If the excerpts do not
directly support an answer, output exactly INSUFFICIENT_BOOK_EVIDENCE. Do not give personal
treatment, dosage, or safety advice. Do not claim that a historic book use is medically safe.

Question: {question}

Evidence:
{excerpts}
"""
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


def citation_gate(text: str, evidence_count: int) -> tuple[bool, list[str]]:
    cited = {int(index) for index in re.findall(r"\[E(\d+)\]", text)}
    invalid = sorted(index for index in cited if index < 1 or index > evidence_count)
    normalized = re.sub(r"([。！？.!?])\s*(\[E\d+\])", r" \2\1", text)
    sentences: list[str] = []
    for line in normalized.splitlines():
        sentences.extend(
            item.strip() for item in re.findall(
                r".+?(?:[。！？!?]|\.(?=\s|$)|$)", line
            ) if re.search(r"[A-Za-z\u3400-\u9fff]", item)
        )
    policy_only = (
        "不是現代醫療", "不構成醫療", "非醫療建議", "諮詢合格醫療", "咨询合格医疗",
        "not modern medical", "not medical advice", "consult a qualified healthcare",
    )
    uncited = [
        item for item in sentences
        if not re.search(r"\[E\d+\]", item)
        and not item.rstrip().endswith((":", "："))
        and not any(phrase in item.casefold() for phrase in policy_only)
    ]
    return bool(cited) and not invalid and not uncited, uncited


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
        "external_generation_calls": 0, "external_embedding_calls": 0, "incremental_usd": 0,
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
    if any(pattern in folded for pattern in MEDICAL_PATTERNS):
        result["answer_status"] = "refused_personal_medical_advice"
        result["answer"] = (
            "這個系統只整理書中歷史記載，不能提供個人治療、用量或安全建議。"
            if response_locale == "zh-TW" else
            "This system only summarizes historical book content and cannot provide personal treatment, dosage, or safety advice."
        )
        return result
    api_key = load_env_value(env_file, "GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not loaded")
    hits = retrieve(db, question, api_key, top_k)
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
    text = generate(api_key, question, response_locale, hits, "gemini-2.5-flash-lite")
    result["external_generation_calls"] = 1
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
