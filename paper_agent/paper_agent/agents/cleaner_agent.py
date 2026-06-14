"""CleanerAgent: accepted 論文のみ KH Coder 用テキストを作成。

タイ語・ベトナム語本文は自動で N に入れず translation_required=True (screener が
needs_review にしている)。accepted には screening/duplicate JSON も書き出す。
"""
from __future__ import annotations

from ..artifacts import write_duplicate_json, write_screening_json
from ..cleaner import clean_to_file
from ..models import ScreeningStatus
from ..pipeline import get_text_for
from ..screener import screen
from .base import AgentResult, logger


class CleanerAgent:
    name = "CleanerAgent"

    def run(self, db) -> AgentResult:
        res = AgentResult(self.name)
        cleaned = 0
        skipped_lang = []
        for rec in db.all():
            if rec.screening_status != ScreeningStatus.accepted:
                continue
            # タイ語/ベトナム語は翻訳前提。N に入れず cleaned も作らない。
            if rec.original_language in ("th", "vi"):
                skipped_lang.append(rec.paper_id)
                continue
            text = get_text_for(rec)
            if not text:
                continue
            try:
                clean_to_file(rec, text)
                cleaned += 1
                # 判定根拠 JSON も保存
                sr = screen(rec, text)
                write_screening_json(rec, {
                    "paper_id": rec.paper_id,
                    "decision": sr.decision.value,
                    "confidence": sr.confidence,
                    "country_fit": sr.country_fit,
                    "workplace_fit": sr.workplace_fit,
                    "generation_fit": sr.generation_fit,
                    "fulltext_fit": sr.fulltext_fit,
                    "category_fit": sr.category_fit,
                    "reasons": sr.reasons,
                    "warnings": sr.warnings,
                })
                write_duplicate_json(rec, {
                    "paper_id": rec.paper_id,
                    "duplicate_of": rec.duplicate_of,
                    "duplicate_confidence": rec.duplicate_confidence,
                    "same_dataset_warning": rec.same_dataset_warning,
                })
            except Exception as exc:  # noqa: BLE001
                logger.error("cleaner 失敗 %s: %s", rec.paper_id, exc)
        res.info["cleaned"] = cleaned
        res.info["skipped_translation_required"] = skipped_lang
        res.add(f"cleaned.txt 生成 {cleaned} 件 / 翻訳要でスキップ {len(skipped_lang)} 件")
        return res
