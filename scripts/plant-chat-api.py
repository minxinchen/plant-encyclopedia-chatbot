#!/usr/bin/env python3
"""Portable zero-cost HTTP API for the approved Köhler evidence subset.

author: Codex (GPT-5)
date: 2026-08-11
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LAB = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "1.0"
GENERATION_INSTRUCTION = (
    "Use only answer_sentences and evidence returned by this API. Do not add plant facts. "
    "Preserve every source_id and pdf_page citation. Keep the returned response_locale and "
    "Taiwan display name. Refuse or defer when answer_status is not answerable or name_metadata."
)

NAME_STATUS_ORDER = {
    "taiwan_catalogue_preferred": 0,
    "taiwan_public_name": 1,
    "taiwan_alias": 2,
    "non_taiwan_traditional_authoritative": 3,
    "human_reviewed_traditional_translation": 4,
    "simplified_chinese_fallback": 5,
}

SIMPLIFIED_MARKERS = set("这书么药剂后发为里叶树个应当简体")
ENGLISH_RE = re.compile(r"[A-Za-z]{2,}")
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def detect_input_language(question: str, requested: str | None = None) -> tuple[str, str]:
    if requested in {"en", "zh-TW"}:
        return requested, requested
    if requested == "zh-Hans":
        return "zh-Hans", "zh-TW"
    if CHINESE_RE.search(question):
        detected = "zh-Hans" if any(char in SIMPLIFIED_MARKERS for char in question) else "zh-Hant"
        return detected, "zh-TW"
    if ENGLISH_RE.search(question):
        return "en", "en"
    return "other", "zh-TW"


def citation(source_id: str, volume: int, pdf_page: int, record_id: str, section_type: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "volume": volume,
        "pdf_page": pdf_page,
        "record_id": record_id,
        "section_type": section_type,
        "citation_label": f"{source_id}, PDF p. {pdf_page}",
    }


class PlantChatEngine:
    """Policy-first deterministic chat engine over explicitly approved records."""

    def __init__(self, lab: Path = LAB) -> None:
        self.lab = lab
        self.policy = load_json(lab / "config/chat-policy.json")
        self.record = load_json(lab / "data/records/cibotium-barometz.json")
        self.podophyllum_record = load_json(lab / "data/records/podophyllum-peltatum.json")
        self.strychnos_record = load_json(lab / "data/records/strychnos-nux-vomica.json")
        self.carica_record = load_json(lab / "data/records/carica-papaya.json")
        self.atropa_record = load_json(lab / "data/records/atropa-belladonna.json")
        self.piper_record = load_json(lab / "data/records/piper-nigrum.json")
        self.polygala_record = load_json(lab / "data/records/polygala-senega.json")
        self.laminaria_record = load_json(lab / "data/records/laminaria-hyperborea.json")
        self.records = [
            self.record, self.podophyllum_record, self.strychnos_record,
            self.carica_record, self.atropa_record, self.piper_record, self.polygala_record,
            self.laminaria_record,
        ]
        self.names = load_json(lab / "data/sample-name-resolution.json")
        self.sections = {item["section_type"]: item for item in self.record["sections"]}
        self.podophyllum_sections = {
            item["section_type"]: item for item in self.podophyllum_record["sections"]
        }
        self.strychnos_sections = {
            item["section_type"]: item for item in self.strychnos_record["sections"]
        }
        self.carica_sections = {
            item["section_type"]: item for item in self.carica_record["sections"]
        }
        self.atropa_sections = {
            item["section_type"]: item for item in self.atropa_record["sections"]
        }
        self.piper_sections = {
            item["section_type"]: item for item in self.piper_record["sections"]
        }
        self.polygala_sections = {
            item["section_type"]: item for item in self.polygala_record["sections"]
        }
        self.laminaria_sections = {
            item["section_type"]: item for item in self.laminaria_record["sections"]
        }
        self.display_name = self.record["display_name"]
        self.scientific_name = self.record["name_resolution"]["accepted_scientific_name"]
        self.cibotium_terms = (
            "金狗毛蕨", "金毛狗", "cibotium", "barometz", "penawar", "djambi",
        )
        self.podophyllum_terms = (
            "盾葉鬼臼", "盾叶鬼臼", "podophyllum", "peltatum", "may-apple", "may apple",
        )
        self.strychnos_terms = (
            "馬錢", "马钱", "馬錢子", "马钱子", "strychnos", "nux-vomica", "nux vomica", "strychnine", "士的寧",
        )
        self.carica_terms = (
            "番木瓜", "carica papaya", "carica", "papaya", "papayotin", "papain", "木瓜蛋白酶",
        )
        self.carica_alias_terms = ("木瓜", "萬壽果", "乳瓜")
        self.atropa_terms = (
            "顛茄", "颠茄", "atropa", "belladonna", "atropin", "atropine", "顛茄鹼", "颠茄碱",
        )
        self.piper_terms = (
            "胡椒", "piper nigrum", "black pepper", "piperin", "piperine", "胡椒鹼", "胡椒碱",
        )
        self.polygala_terms = (
            "美遠志", "美远志", "polygala senega", "senega", "senegin", "saponin", "皂苷", "皂甙",
        )
        self.polygala_alias_terms = ("遠志", "远志")
        self.laminaria_terms = (
            "極北海帶", "极北海带", "laminaria hyperborea", "laminaria cloustonii",
            "laminaria cloustoni", "cloustonii", "cloustoni",
        )
        self.cibotium_wrong_name_terms = ("cibotium taiwanense",)
        self.carica_wrong_name_terms = ("木瓜海棠", "chaenomeles sinensis", "chaenomeles")
        self.atropa_wrong_name_terms = ("datura stramonium", "曼陀羅", "曼陀罗")
        self.piper_wrong_name_terms = (
            "zanthoxylum bungeanum", "花椒", "capsicum annuum", "甜椒",
        )
        self.polygala_wrong_name_terms = ("polygala tenuifolia", "細葉遠志", "细叶远志")
        self.laminaria_wrong_name_terms = ("saccharina japonica", "日本海帶", "日本海带")
        self.non_kohler_drugs = (
            "阿斯匹靈", "阿司匹林", "aspirin", "metformin", "二甲雙胍", "二甲双胍",
        )

    @staticmethod
    def _contains(question: str, terms: tuple[str, ...]) -> bool:
        folded = question.casefold()
        return any(term.casefold() in folded for term in terms)

    @staticmethod
    def _is_medical_advice(question: str) -> bool:
        folded = question.casefold()
        direct_patterns = (
            "我流血", "我便秘", "我要怎麼", "我应该", "我應該", "一天吃", "用量", "劑量",
            "how should i", "should i take", "can i take", "can i use", "what dose", "dosage", "treat my", "i am bleeding",
            "可以吃", "能吃", "可以用", "能用",
        )
        return any(pattern in folded for pattern in direct_patterns)

    @staticmethod
    def _looks_like_drug_question(question: str) -> bool:
        folded = question.casefold()
        return any(term in folded for term in ("dose", "dosage", "drug", "medicine", "藥物", "藥品", "劑量", "用量"))

    def _name_query_record(self, question: str) -> str | None:
        folded = question.casefold()
        name_terms = {
            "Cibotium barometz": self.cibotium_terms,
            "Saponaria officinalis": ("saponaria officinalis", "皂質草", "肥皂草"),
            "Podophyllum peltatum": self.podophyllum_terms,
            "Strychnos nux-vomica": self.strychnos_terms,
            "Carica papaya": self.carica_terms + self.carica_alias_terms,
            "Atropa belladonna": self.atropa_terms,
            "Piper nigrum": self.piper_terms,
            "Polygala senega": self.polygala_terms + self.polygala_alias_terms,
            "Laminaria hyperborea": self.laminaria_terms,
        }
        asks_for_name = any(term in folded for term in ("name", "called", "名稱", "名字", "叫什麼", "顯示"))
        if not asks_for_name:
            return None
        for query_name, terms in name_terms.items():
            if any(term.casefold() in folded for term in terms):
                return query_name
        return None

    @staticmethod
    def _display_names(record: dict[str, Any]) -> list[dict[str, Any]]:
        sources = record["name_resolution"]["sources"]
        return [{
            "display_name": record["display_name"],
            "scientific_name": record["name_resolution"]["accepted_scientific_name"],
            "status": record["name_resolution"]["status"],
            "authority": sources[0]["authority"],
            "url": sources[0]["url"],
        }]

    def _base(self, question: str, requested_language: str | None) -> dict[str, Any]:
        detected, locale = detect_input_language(question, requested_language)
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": str(uuid.uuid4()),
            "question": question,
            "detected_language": detected,
            "response_locale": locale,
            "answer_status": "not_yet_processed",
            "answer": "",
            "answer_sentences": [],
            "evidence": [],
            "display_names": [],
            "generation_instruction": GENERATION_INSTRUCTION,
            "policy_id": self.policy["policy_id"],
            "coverage": {
                "approved_fact_records": [record["record_id"] for record in self.records],
                "full_book_processed": False,
            },
            "capabilities": {
                "text_evidence": True,
                "image_reasoning": False,
            },
            "external_model_calls": 0,
            "incremental_usd": 0,
        }

    @staticmethod
    def _sentence(text: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
        return {"text": text, "citations": citations}

    def _finish(self, response: dict[str, Any], status: str, sentences: list[dict[str, Any]],
                evidence: list[dict[str, Any]] | None = None,
                display_names: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        response["answer_status"] = status
        response["answer_sentences"] = sentences
        response["answer"] = " ".join(item["text"] for item in sentences)
        response["evidence"] = evidence or []
        response["display_names"] = display_names or []
        return response

    def _template(self, response: dict[str, Any], template_name: str, status: str) -> dict[str, Any]:
        text = self.policy["response_templates"][template_name]
        return self._finish(response, status, [self._sentence(text, [])])

    def _answer_name_metadata(self, response: dict[str, Any], query_name: str) -> dict[str, Any]:
        record = next(item for item in self.names["records"] if item["query_scientific_name"] == query_name)
        source = record["sources"][0]
        if query_name == "Cibotium barometz":
            sentence = (
                "臺灣公開資料的優先顯示名稱是金狗毛蕨（Cibotium barometz (L.) J.Sm.），並有臺灣物種紀錄。"
                if response["response_locale"] == "zh-TW" else
                "Taiwan public sources use 金狗毛蕨 for Cibotium barometz (L.) J.Sm. and record the species in Taiwan."
            )
        elif query_name == "Saponaria officinalis":
            sentence = (
                "臺灣公開資料的優先顯示名稱是皂質草（Saponaria officinalis L.）；肥皂草只作為別名。"
                if response["response_locale"] == "zh-TW" else
                "The preferred Taiwan display name is 皂質草 (Saponaria officinalis L.); 肥皂草 is retained only as an alias."
            )
        elif query_name == "Podophyllum peltatum":
            occurrence = "；Tai2 同時標示為 Non-Taiwanese，這個中文名不代表台灣有分布紀錄"
            sentence = (
                f"臺灣公開資料使用盾葉鬼臼（Podophyllum peltatum L.）{occurrence}。"
                if response["response_locale"] == "zh-TW" else
                "A Taiwan public source uses 盾葉鬼臼 for Podophyllum peltatum L.; Tai2 also marks it Non-Taiwanese, so the Chinese display name is not evidence of occurrence in Taiwan."
            )
        elif query_name == "Strychnos nux-vomica":
            sentence = (
                "臺灣官方資料使用植物名「馬錢」（Strychnos nux-vomica L.），其乾燥成熟種子的藥材名是「馬錢子」；資料另記載臺灣有栽培，這不等於臺灣原生分布。"
                if response["response_locale"] == "zh-TW" else
                "Taiwan official sources use 馬錢 for the plant Strychnos nux-vomica L.; 馬錢子 is the materia-medica name for its dried mature seed. The source records cultivation in Taiwan, not native distribution."
            )
        elif query_name == "Carica papaya":
            sentence = (
                "臺灣公開資料使用「番木瓜」（Carica papaya L.）作明確植物名；「木瓜」保留為臺灣別名。臺灣資料標示本種為歸化，這不等於本書的原生分布結論。"
                if response["response_locale"] == "zh-TW" else
                "Taiwan public sources use 番木瓜 for Carica papaya L.; 木瓜 is retained as a Taiwan alias. Taiwan sources mark it naturalized, which is separate from the book's distribution account."
            )
        elif query_name == "Atropa belladonna":
            sentence = (
                "臺灣衛生福利部公開資料使用「顛茄」（Atropa belladonna L.）；該來源只用於名稱對照，本批未核對臺灣 occurrence。"
                if response["response_locale"] == "zh-TW" else
                "A Taiwan Ministry of Health and Welfare source uses 顛茄 for Atropa belladonna L.; it is used only for name resolution, and Taiwan occurrence was not checked in this batch."
            )
        elif query_name == "Piper nigrum":
            sentence = (
                "臺灣公開資料使用「胡椒」（Piper nigrum L.）；Tai2 標示 Non-Taiwanese，農業部另記載高雄六龜有少量種植，因此只視為臺灣栽培紀錄，不視為臺灣原生。"
                if response["response_locale"] == "zh-TW" else
                "Taiwan public sources use 胡椒 for Piper nigrum L. Tai2 marks it Non-Taiwanese, while a Ministry of Agriculture source records limited cultivation in Liugui, Kaohsiung; this is cultivation, not native occurrence."
            )
        elif query_name == "Polygala senega":
            sentence = (
                "臺灣衛福部資料將 Senega 稱為「美遠志」，另有資料把 Polygala senega 對應為「遠志」；本系統以美遠志作主顯示名、遠志作別名。這些資料只支援名稱／成分用語，本批未找到臺灣 occurrence 紀錄。"
                if response["response_locale"] == "zh-TW" else
                "Taiwan Ministry of Health and Welfare sources use 美遠志 for Senega and also map Polygala senega to 遠志. This system uses 美遠志 as the primary display name and 遠志 as an alias; those sources do not establish occurrence in Taiwan."
            )
        else:
            sentence = (
                "臺灣農業部體系公開資料將 Laminaria hyperborea 稱為「極北海帶」；WoRMS 將書中的 Laminaria cloustonii 接受為此學名。這些來源只用於名稱與分類對照，本批未找到臺灣 occurrence 紀錄。"
                if response["response_locale"] == "zh-TW" else
                "A Taiwan Ministry of Agriculture source uses 極北海帶 for Laminaria hyperborea, and WoRMS accepts the book name Laminaria cloustonii under that scientific name. These sources support naming and taxonomy only; this batch found no Taiwan occurrence record."
            )
        display_names = [{
            "display_name": record["display_name_zh_tw"],
            "scientific_name": record["book_scientific_name"],
            "status": record["name_status"],
            "authority": source["authority"],
            "url": source["url"],
        }]
        return self._finish(response, "name_metadata", [self._sentence(sentence, [])], display_names=display_names)

    def _cibotium_intent(self, question: str) -> str | None:
        folded = question.casefold()
        # Remove taxon labels before intent detection so the 毛 in 金狗毛蕨
        # cannot falsely turn an unrelated fact question into a hair query.
        for label in ("金狗毛蕨", "金毛狗", "cibotium barometz", "cibotium"):
            folded = folded.replace(label, " ")
        if any(term in folded for term in ("止血", "haemostat", "hemostat", "mechanism", "機制")):
            return "historical_use"
        if any(term in folded for term in ("penawar", "djambi", "毛", "hair", "cell", "細胞", "alkali", "橙紅")):
            return "anatomy"
        if any(term in folded for term in ("分布", "產地", "where", "distribution", "formosa", "assam")):
            return "distribution"
        if any(term in folded for term in ("形態", "葉", "leaf", "frond", "莖", "stem", "describe")):
            return "description"
        return None

    def _answer_cibotium(self, response: dict[str, Any], intent: str | None) -> dict[str, Any]:
        locale = response["response_locale"]
        names = self._display_names(self.record)
        if intent == "historical_use":
            return self._template(
                response,
                "not_yet_processed_en" if locale == "en" else "not_yet_processed_zh_tw",
                "not_yet_processed",
            )
        if intent is None:
            text = (
                "The currently approved book evidence does not contain that information; the full book has not been completely reviewed."
                if locale == "en" else
                "現有已核准的書中證據沒有這項資訊；全書尚未處理完成，不能斷言本書完全未記載。"
            )
            return self._finish(response, "no_approved_evidence", [self._sentence(text, [])], display_names=names)

        page = 32 if intent == "anatomy" else 31 if intent == "distribution" else 30
        cite = citation("kohler-volume-4", 4, page, self.record["record_id"], intent)
        section = self.sections[intent]
        if locale == "en":
            rendered = {
                "anatomy": (
                    "For 金狗毛蕨 (Cibotium barometz (L.) J.Sm.), the book says that the Penawar Djambi variety "
                    "was supplied mainly by Cibotium Barometz. Its hairs are shiny golden yellow to yellow-brown, "
                    "3–7 cm long, fairly straight, and made of a single row of cells; individual cells measure "
                    "400–600 µm long by 20–45 µm wide."
                ),
                "distribution": (
                    "For 金狗毛蕨 (Cibotium barometz (L.) J.Sm.), the book describes a range from Assam and "
                    "southern China southward to Formosa and the Malay region."
                ),
                "description": (
                    "For 金狗毛蕨 (Cibotium barometz (L.) J.Sm.), the book describes a short, strong terrestrial "
                    "trunk, fronds over two metres long, and long golden scales or hairs densely covering the base of the stipe."
                ),
            }[intent]
        else:
            rendered = section["zh_tw_rendering"]
        evidence = [{
            **cite,
            "source_excerpt_original": section["original_text"],
            "quote_or_summary": rendered,
            "review_status": self.record["review_status"],
            "score_channels": {"approved_record_match": 1.0},
        }]
        return self._finish(response, "answerable", [self._sentence(rendered, [cite])], evidence, names)

    def _podophyllum_intent(self, question: str) -> str | None:
        folded = question.casefold()
        for label in ("盾葉鬼臼", "盾叶鬼臼", "podophyllum peltatum", "podophyllum"):
            folded = folded.replace(label, " ")
        if any(term in folded for term in ("便秘", "constipation", "verstopfung", "瀉", "泻", "purgative", "歷史藥用", "historical use")):
            return "historical_use"
        if any(term in folded for term in ("分布", "產地", "where", "distribution", "canada", "北美")):
            return "distribution"
        if any(term in folded for term in ("科", "分類", "taxonomy", "family", "berberidaceae")):
            return "taxonomy"
        return None

    def _answer_podophyllum(self, response: dict[str, Any], intent: str | None) -> dict[str, Any]:
        record = self.podophyllum_record
        locale = response["response_locale"]
        names = self._display_names(record)
        if intent is None:
            text = (
                "The currently approved book evidence does not contain that information; the full book has not been completely reviewed."
                if locale == "en" else
                "現有已核准的書中證據沒有這項資訊；全書尚未處理完成，不能斷言本書完全未記載。"
            )
            return self._finish(response, "no_approved_evidence", [self._sentence(text, [])], display_names=names)

        page = 192 if intent == "historical_use" else 191
        section = self.podophyllum_sections[intent]
        cite = citation("kohler-volume-1", 1, page, record["record_id"], intent)
        if locale == "en":
            rendered = {
                "historical_use": (
                    "For 盾葉鬼臼 (Podophyllum peltatum L.), the book historically describes the root as a purgative and emetic, "
                    "and says podophyllin was used for habitual constipation; the same passage warns that it could readily cause colic. "
                    "This is historical documentation, not modern medical or self-medication advice."
                ),
                "distribution": (
                    "For 盾葉鬼臼 (Podophyllum peltatum L.), the book records moist forests in the eastern United States and Canada."
                ),
                "taxonomy": (
                    "The book places 盾葉鬼臼 (Podophyllum peltatum L.) in Berberidaceae and the genus Podophyllum."
                ),
            }[intent]
        else:
            rendered = section["zh_tw_rendering"]
        evidence = [{
            **cite,
            "source_excerpt_original": section["original_text"],
            "quote_or_summary": rendered,
            "review_status": record["review_status"],
            "score_channels": {"approved_record_match": 1.0},
        }]
        return self._finish(response, "answerable", [self._sentence(rendered, [cite])], evidence, names)

    def _strychnos_intent(self, question: str) -> str | None:
        folded = question.casefold()
        for label in self.strychnos_terms:
            folded = folded.replace(label.casefold(), " ")
        if any(term in folded for term in ("毒", "士的寧", "toxic", "poison", "strychnine", "giftig")):
            return "constituents"
        if any(term in folded for term in ("分布", "產地", "where", "distribution", "india", "burma")):
            return "distribution"
        if any(term in folded for term in ("科", "分類", "taxonomy", "family", "loganiaceae")):
            return "taxonomy"
        return None

    def _answer_strychnos(self, response: dict[str, Any], intent: str | None) -> dict[str, Any]:
        record = self.strychnos_record
        locale = response["response_locale"]
        names = self._display_names(record)
        if intent is None:
            text = (
                "The currently approved book evidence does not contain that information; the full book has not been completely reviewed."
                if locale == "en" else
                "現有已核准的書中證據沒有這項資訊；全書尚未處理完成，不能斷言本書完全未記載。"
            )
            return self._finish(response, "no_approved_evidence", [self._sentence(text, [])], display_names=names)
        section = self.strychnos_sections[intent]
        page = 143 if intent == "constituents" else 142 if intent == "distribution" else 141
        cite = citation("kohler-volume-2", 2, page, record["record_id"], intent)
        if locale == "en":
            rendered = {
                "constituents": (
                    "For 馬錢 (Strychnos nux-vomica L.), the book explicitly says that strychnine is very poisonous. "
                    "This is a toxicity statement from the historical source, not medical or self-use advice."
                ),
                "distribution": (
                    "For 馬錢 (Strychnos nux-vomica L.), the book records East India, Indochina and northern Australia, mainly coastal areas, as well as Ceylon and inland Burma."
                ),
                "taxonomy": "The book places 馬錢 (Strychnos nux-vomica L.) in Loganiaceae and the genus Strychnos.",
            }[intent]
        else:
            rendered = section["zh_tw_rendering"]
        evidence = [{
            **cite,
            "source_excerpt_original": section["original_text"],
            "quote_or_summary": rendered,
            "review_status": record["review_status"],
            "score_channels": {"approved_record_match": 1.0},
        }]
        return self._finish(response, "answerable", [self._sentence(rendered, [cite])], evidence, names)

    def _carica_intent(self, question: str) -> str | None:
        folded = question.casefold()
        for label in self.carica_terms + self.carica_alias_terms:
            folded = folded.replace(label.casefold(), " ")
        if any(term in question.casefold() for term in ("papayotin", "papain", "木瓜蛋白酶", "乳汁", "latex")):
            return "constituents"
        if any(term in folded for term in ("全年", "花期", "flower", "fruit all year")):
            return "flowering"
        if any(term in folded for term in ("科", "分類", "taxonomy", "family", "papayaceae", "caricaceae")):
            return "taxonomy"
        return None

    def _answer_carica(self, response: dict[str, Any], intent: str | None) -> dict[str, Any]:
        record = self.carica_record
        locale = response["response_locale"]
        names = self._display_names(record)
        if intent is None:
            text = (
                "The currently approved book evidence does not contain that information; the full book has not been completely reviewed."
                if locale == "en" else
                "現有已核准的書中證據沒有這項資訊；全書尚未處理完成，不能斷言本書完全未記載。"
            )
            return self._finish(response, "no_approved_evidence", [self._sentence(text, [])], display_names=names)
        section = self.carica_sections[intent]
        page = 168 if intent == "constituents" else 167 if intent == "flowering" else 165
        cite = citation("kohler-volume-3", 3, page, record["record_id"], intent)
        if locale == "en":
            rendered = {
                "constituents": (
                    "For 番木瓜 (Carica papaya L.), the book lists papayotin as a constituent of the latex and says that Wurtz and Bouchut isolated this enzyme in 1870 and called it papain."
                ),
                "flowering": "The book says that 番木瓜 (Carica papaya L.) bears flowers and fruit throughout the year.",
                "taxonomy": "The book records 番木瓜 as Carica Papaya L. in the papaya family and genus Carica.",
            }[intent]
        else:
            rendered = section["zh_tw_rendering"]
        evidence = [{
            **cite,
            "source_excerpt_original": section["original_text"],
            "quote_or_summary": rendered,
            "review_status": record["review_status"],
            "score_channels": {"approved_record_match": 1.0},
        }]
        return self._finish(response, "answerable", [self._sentence(rendered, [cite])], evidence, names)

    def _atropa_intent(self, question: str) -> str | None:
        folded = question.casefold()
        original = folded
        for label in self.atropa_terms:
            folded = folded.replace(label.casefold(), " ")
        if any(term in original for term in ("atropin", "atropine", "顛茄鹼", "颠茄碱")) or any(
            term in folded for term in ("成分", "constituent", "alkaloid", "生物鹼", "生物碱")
        ):
            return "constituents"
        if any(term in folded for term in ("分布", "產地", "where", "distribution", "europe", "asia", "歐洲", "亞洲")):
            return "distribution"
        if any(term in folded for term in ("形態", "describe", "berry", "leaf", "leaves", "果實", "葉")):
            return "description"
        if any(term in folded for term in ("科", "分類", "taxonomy", "family", "solaneae")):
            return "taxonomy"
        if any(term in folded for term in ("歷史用途", "歷史藥用", "historical use", "historically used", "anwendung")):
            return "historical_use"
        return None

    def _answer_atropa(self, response: dict[str, Any], intent: str | None) -> dict[str, Any]:
        record = self.atropa_record
        locale = response["response_locale"]
        names = self._display_names(record)
        if intent is None:
            text = (
                "The currently approved book evidence does not contain that information; the full book has not been completely reviewed."
                if locale == "en" else
                "現有已核准的書中證據沒有這項資訊；全書尚未處理完成，不能斷言本書完全未記載。"
            )
            return self._finish(response, "no_approved_evidence", [self._sentence(text, [])], display_names=names)
        section = self.atropa_sections[intent]
        evidence_ref = record["book_evidence"][section["evidence_indexes"][0]]
        page = evidence_ref["pdf_page"]
        cite = citation("kohler-volume-1", 1, page, record["record_id"], intent)
        if locale == "en":
            rendered = {
                "constituents": (
                    "For 顛茄 (Atropa belladonna L.), the book records atropine in the roots, leaves and seeds, "
                    "and also names belladonnine, hyoscyamine, asparagine and atrosin. This historical constituent account is not advice for use."
                ),
                "distribution": (
                    "For 顛茄 (Atropa belladonna L.), the book records scattered occurrence in shady mountain forests of central and southern Europe and in western and central Asia; this is separate from Taiwan occurrence metadata."
                ),
                "description": (
                    "The book describes 顛茄 (Atropa belladonna L.) as a perennial herb up to two metres tall, with stalked leaves, solitary nodding flowers and nearly spherical glossy black berries."
                ),
                "taxonomy": "The book records 顛茄 as Atropa Belladonna L. in Solaneae and the genus Atropa.",
                "historical_use": (
                    "The book lists historical uses of belladonna preparations and atropine. This is historical documentation only, not modern treatment, dosage or self-use advice."
                ),
            }[intent]
        else:
            rendered = section["zh_tw_rendering"]
        evidence = [{
            **cite,
            "source_excerpt_original": section["original_text"],
            "quote_or_summary": rendered,
            "review_status": record["review_status"],
            "score_channels": {"approved_record_match": 1.0},
        }]
        return self._finish(response, "answerable", [self._sentence(rendered, [cite])], evidence, names)

    def _piper_intent(self, question: str) -> str | None:
        folded = question.casefold()
        original = folded
        for label in self.piper_terms:
            folded = folded.replace(label.casefold(), " ")
        if any(term in original for term in ("piperin", "piperine", "胡椒鹼", "胡椒碱")) or any(
            term in folded for term in ("成分", "constituent", "alkaloid", "生物鹼", "生物碱")
        ):
            return "constituents"
        if any(term in folded for term in ("分布", "原生地", "產地", "where", "distribution", "malabar")):
            return "distribution"
        if any(term in folded for term in ("花期", "開花", "flowering", "harvest", "採收")):
            return "flowering"
        if any(term in folded for term in ("形態", "describe", "leaf", "berry", "葉", "果實")):
            return "description"
        if any(term in folded for term in ("科", "分類", "taxonomy", "family", "piperaceae")):
            return "taxonomy"
        if any(term in folded for term in ("歷史用途", "歷史藥用", "historical use", "historically used", "anwendung")):
            return "historical_use"
        return None

    def _answer_piper(self, response: dict[str, Any], intent: str | None) -> dict[str, Any]:
        record = self.piper_record
        locale = response["response_locale"]
        names = self._display_names(record)
        if intent is None:
            text = (
                "The currently approved book evidence does not contain that information; the full book has not been completely reviewed."
                if locale == "en" else
                "現有已核准的書中證據沒有這項資訊；全書尚未處理完成，不能斷言本書完全未記載。"
            )
            return self._finish(response, "no_approved_evidence", [self._sentence(text, [])], display_names=names)
        section = self.piper_sections[intent]
        evidence_ref = record["book_evidence"][section["evidence_indexes"][0]]
        page = evidence_ref["pdf_page"]
        cite = citation("kohler-volume-2", 2, page, record["record_id"], intent)
        if locale == "en":
            rendered = {
                "constituents": (
                    "For 胡椒 (Piper nigrum L.), the book describes piperine and records piperidine and piperic acid among products formed under specified treatment. This historical chemistry account is not advice for use."
                ),
                "distribution": (
                    "The book considers the forests of the Malabar coast the probable native area of 胡椒 (Piper nigrum L.) and records cultivation in southern India, Ceylon, Sumatra, Java, Borneo, the Philippines and the West Indies."
                ),
                "flowering": "The book records flowering in southern India in May and June, with harvest beginning early the following year.",
                "description": (
                    "The book describes 胡椒 (Piper nigrum L.) as a woody shrub climbing by aerial roots, with alternate leathery leaves, flower spikes and nearly spherical one-seeded berries."
                ),
                "taxonomy": "The book records 胡椒 as Piper nigrum L. in Piperaceae and the genus Piper, with Piper trioicum Roxb. listed as a synonym.",
                "historical_use": (
                    "The book lists historical uses of pepper and piperine and also records poisoning effects from larger amounts. This is historical documentation, not modern treatment, dosage or self-use advice."
                ),
            }[intent]
        else:
            rendered = section["zh_tw_rendering"]
        evidence = [{
            **cite,
            "source_excerpt_original": section["original_text"],
            "quote_or_summary": rendered,
            "review_status": record["review_status"],
            "score_channels": {"approved_record_match": 1.0},
        }]
        return self._finish(response, "answerable", [self._sentence(rendered, [cite])], evidence, names)

    def _polygala_intent(self, question: str) -> str | None:
        folded = question.casefold()
        original = folded
        for label in self.polygala_terms + self.polygala_alias_terms:
            folded = folded.replace(label.casefold(), " ")
        if any(term in original for term in ("senegin", "saponin", "皂苷", "皂甙")) or any(
            term in folded for term in ("成分", "constituent", "compound")
        ):
            return "constituents"
        if any(term in folded for term in ("分布", "原生", "產地", "where", "distribution", "north america", "北美")):
            return "distribution"
        if any(term in folded for term in ("花期", "開花", "flowering", "may", "五月")):
            return "flowering"
        if any(term in folded for term in ("形態", "describe", "root", "leaf", "根", "葉", "花")):
            return "description"
        if any(term in folded for term in ("科", "分類", "taxonomy", "family", "polygalaceae")):
            return "taxonomy"
        if any(term in folded for term in ("歷史用途", "歷史藥用", "historical use", "historically used", "anwendung")):
            return "historical_use"
        return None

    def _answer_polygala(self, response: dict[str, Any], intent: str | None) -> dict[str, Any]:
        record = self.polygala_record
        locale = response["response_locale"]
        names = self._display_names(record)
        if intent is None:
            text = (
                "The currently approved book evidence does not contain that information; the full book has not been completely reviewed."
                if locale == "en" else
                "現有已核准的書中證據沒有這項資訊；全書尚未處理完成，不能斷言本書完全未記載。"
            )
            return self._finish(response, "no_approved_evidence", [self._sentence(text, [])], display_names=names)
        section = self.polygala_sections[intent]
        evidence_ref = record["book_evidence"][section["evidence_indexes"][0]]
        page = evidence_ref["pdf_page"]
        cite = citation("kohler-volume-2", 2, page, record["record_id"], intent)
        if locale == "en":
            rendered = {
                "constituents": (
                    "For 美遠志 (Polygala senega L.), the book records senegin in the root and links that historical name with polygalic acid and saponin; it also lists virginic acid, pectic acid, tannin, pigments, gum, protein, wax, fixed oil, resin, malic acid and sugar. This is a historical chemistry account, not advice for use."
                ),
                "distribution": (
                    "The book records 美遠志 (Polygala senega L.) as native to mountain forests of North America between the Great Lakes and Texas, but absent from the Rocky Mountains."
                ),
                "flowering": "The book records May as the flowering time of 美遠志 (Polygala senega L.).",
                "description": (
                    "The book describes 美遠志 (Polygala senega L.) as a perennial herb with a spindle-shaped root, several upright stems, alternate leaves, terminal racemes and small white, greenish-white or reddish flowers."
                ),
                "taxonomy": "The book records 美遠志 as Polygala Senega L. in Polygalaceae and the genus Polygala.",
                "historical_use": (
                    "The book lists historical uses of Senega root and its preparations and also records gastrointestinal irritation and unsuitability for prolonged use. This is historical documentation, not modern treatment, dosage or self-use advice."
                ),
            }[intent]
        else:
            rendered = section["zh_tw_rendering"]
        evidence = [{
            **cite,
            "source_excerpt_original": section["original_text"],
            "quote_or_summary": rendered,
            "review_status": record["review_status"],
            "score_channels": {"approved_record_match": 1.0},
        }]
        return self._finish(response, "answerable", [self._sentence(rendered, [cite])], evidence, names)

    def _laminaria_intent(self, question: str) -> str | None:
        folded = question.casefold()
        original = folded
        for label in self.laminaria_terms:
            folded = folded.replace(label.casefold(), " ")
        if any(term in original for term in ("碘", "iodine", "jod")):
            return "constituents"
        if any(term in folded for term in ("分布", "水深", "外海", "where", "distribution", "depth")):
            return "distribution"
        if any(term in original for term in ("flexicaulis",)) or any(
            term in folded for term in ("比較", "區分", "compare", "difference", "柄")
        ):
            return "other"
        if any(term in folded for term in ("構造", "解剖", "橫切", "anatomy", "cross-section", "mucilage")):
            return "anatomy"
        if any(term in folded for term in ("形態", "describe", "blade", "thallus", "葉狀體")):
            return "description"
        if any(term in folded for term in ("科", "分類", "taxonomy", "family", "laminarieae")):
            return "taxonomy"
        if any(term in folded for term in ("歷史用途", "歷史藥用", "historical use", "anwendung")):
            return "historical_use"
        return None

    def _answer_laminaria(self, response: dict[str, Any], intent: str | None) -> dict[str, Any]:
        record = self.laminaria_record
        locale = response["response_locale"]
        names = self._display_names(record)
        if intent is None:
            text = (
                "The currently approved book evidence does not contain that information; the full book has not been completely reviewed."
                if locale == "en" else
                "現有已核准的書中證據沒有這項資訊；全書尚未處理完成，不能斷言本書完全未記載。"
            )
            return self._finish(response, "no_approved_evidence", [self._sentence(text, [])], display_names=names)
        section = self.laminaria_sections[intent]
        evidence_ref = record["book_evidence"][section["evidence_indexes"][0]]
        page = evidence_ref["pdf_page"]
        cite = citation("kohler-volume-2", 2, page, record["record_id"], intent)
        if locale == "en":
            rendered = {
                "constituents": (
                    "For 極北海帶 (Laminaria hyperborea), the book records that kelps take up large quantities of iodine from seawater and were historically used in Scotland, Norway and northern France to produce iodine. This is historical documentation, not advice for use."
                ),
                "distribution": (
                    "The book records 極北海帶 (Laminaria hyperborea) in northern seas, especially along Great Britain and Scandinavia, and places it in deeper water farther offshore."
                ),
                "other": (
                    "The book contrasts the lighter, rigid upright stipe of L. Cloustoni with the darker, longer and more flexible stipe of L. flexicaulis. This is the book's historical taxonomic comparison."
                ),
                "anatomy": (
                    "The book describes a dark-brown cortex, a middle layer with a ring of large mucilage cavities, and a central medulla in water-soaked stipe cross-sections."
                ),
                "description": (
                    "The book describes a kelp with a long rigid stipe, root-like holdfast branches and a palmately divided blade-like thallus."
                ),
                "taxonomy": (
                    "The book records the taxon as Laminaria Cloustoni Edmonston; modern taxonomy accepts Laminaria cloustonii as Laminaria hyperborea."
                ),
                "historical_use": (
                    "The book records a historical surgical use for dried kelp stipes and their swelling in water. This is historical documentation, not modern treatment, device-use or self-use advice."
                ),
            }[intent]
        else:
            rendered = section["zh_tw_rendering"]
        evidence = [{
            **cite,
            "source_excerpt_original": section["original_text"],
            "quote_or_summary": rendered,
            "review_status": record["review_status"],
            "score_channels": {"approved_record_match": 1.0},
        }]
        return self._finish(response, "answerable", [self._sentence(rendered, [cite])], evidence, names)

    def respond(self, payload: dict[str, Any], retrieve_only: bool = False) -> dict[str, Any]:
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        question = question.strip()
        response = self._base(question, payload.get("language"))
        response["image_request_status"] = (
            "not_available_in_text_only_mvp" if payload.get("include_images") is True else "not_requested"
        )
        locale = response["response_locale"]

        # Scope is evaluated before medical-advice or retrieval logic.
        if self._contains(question, self.non_kohler_drugs):
            return self._template(response, "out_of_scope_en" if locale == "en" else "out_of_scope_zh_tw", "out_of_scope")
        if name_query := self._name_query_record(question):
            return self._answer_name_metadata(response, name_query)

        if self._contains(question, self.cibotium_wrong_name_terms + self.carica_wrong_name_terms + self.atropa_wrong_name_terms + self.piper_wrong_name_terms + self.polygala_wrong_name_terms + self.laminaria_wrong_name_terms):
            return self._template(
                response,
                "not_yet_processed_en" if locale == "en" else "not_yet_processed_zh_tw",
                "not_yet_processed",
            )

        known_cibotium = self._contains(question, self.cibotium_terms)
        known_podophyllum = self._contains(question, self.podophyllum_terms)
        known_strychnos = self._contains(question, self.strychnos_terms)
        known_carica = self._contains(question, self.carica_terms + self.carica_alias_terms)
        known_atropa = self._contains(question, self.atropa_terms)
        known_piper = self._contains(question, self.piper_terms)
        known_polygala = self._contains(question, self.polygala_terms + self.polygala_alias_terms)
        known_laminaria = self._contains(question, self.laminaria_terms)
        if self._is_medical_advice(question):
            if known_cibotium or known_podophyllum or known_strychnos or known_carica or known_atropa or known_piper or known_polygala or known_laminaria:
                return self._template(response, "medical_advice_en" if locale == "en" else "medical_advice_zh_tw", "refuse_medical_advice")
            if self._looks_like_drug_question(question):
                return self._template(response, "out_of_scope_en" if locale == "en" else "out_of_scope_zh_tw", "out_of_scope")

        if known_cibotium:
            result = self._answer_cibotium(response, self._cibotium_intent(question))
        elif known_podophyllum:
            result = self._answer_podophyllum(response, self._podophyllum_intent(question))
        elif known_strychnos:
            result = self._answer_strychnos(response, self._strychnos_intent(question))
        elif known_carica:
            result = self._answer_carica(response, self._carica_intent(question))
        elif known_atropa:
            result = self._answer_atropa(response, self._atropa_intent(question))
        elif known_piper:
            result = self._answer_piper(response, self._piper_intent(question))
        elif known_polygala:
            result = self._answer_polygala(response, self._polygala_intent(question))
        elif known_laminaria:
            result = self._answer_laminaria(response, self._laminaria_intent(question))
        else:
            result = self._template(
                response,
                "not_yet_processed_en" if locale == "en" else "not_yet_processed_zh_tw",
                "not_yet_processed",
            )

        if retrieve_only and result["answer_status"] == "answerable":
            result["answer"] = ""
            result["answer_sentences"] = []
        return result


class PlantChatHandler(BaseHTTPRequestHandler):
    engine = PlantChatEngine()
    server_version = "KohlerPlantChat/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)

    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {
                "status": "ok",
                "service": "kohler-plant-chat-api",
                "schema_version": SCHEMA_VERSION,
                "mode": "approved-evidence-only",
                "capabilities": {"text_evidence": True, "image_reasoning": False},
                "external_model_calls": 0,
            })
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/v1/chat", "/v1/retrieve"}:
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError("request body must be between 1 and 65536 bytes")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            result = self.engine.respond(payload, retrieve_only=self.path.endswith("/retrieve"))
            self._json(200, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid_request", "message": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--once", help="Answer one question as JSON without starting HTTP")
    parser.add_argument("--language", choices=["zh-TW", "zh-Hans", "en"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.once:
        print(json.dumps(PlantChatEngine().respond({"question": args.once, "language": args.language}), ensure_ascii=False))
        return
    server = ThreadingHTTPServer((args.host, args.port), PlantChatHandler)
    print(f"Köhler plant chat API listening on http://{args.host}:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
