"""論文段階の共通処理 (cli と autopilot/agents が再利用)。

- get_text_for(rec)        : レコードの抽出テキストを取得
- apply_dedupe(rec, ...)   : 1件の重複判定を適用 (dedupe-all のコア)
- apply_screen(rec, db)    : 1件の採否判定を適用 (screen-all のコア)
- dedupe_all_papers(db)    : 全件の重複判定
- screen_all_papers(db)    : 全件の採否判定
"""
from __future__ import annotations

from pathlib import Path

from .db import PaperDB
from .duplicate_checker import best_match
from .extractor import extract_text, guess_title
from .models import DuplicateType, PaperRecord, ScreeningStatus
from .screener import (
    assess_country,
    assess_generations,
    detected_generation_groups,
    enrich_record,
    screen,
)
from .utils import detect_language, get_logger, normalize_title

logger = get_logger()


def get_text_for(rec: PaperRecord) -> str:
    """レコードの抽出テキストを返す。text パスが無ければ PDF から抽出する。"""
    if rec.local_text_path and Path(rec.local_text_path).is_file():
        try:
            return Path(rec.local_text_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if rec.local_pdf_path and Path(rec.local_pdf_path).is_file():
        return extract_text(rec.local_pdf_path).text
    return ""


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------
def apply_dedupe(rec: PaperRecord, others: list[PaperRecord], db: PaperDB) -> str:
    enrich_record(rec, get_text_for(rec))
    result = best_match(rec, others)
    if result.duplicate_type == DuplicateType.not_duplicate:
        db.upsert(rec)
        return "not_duplicate"

    matched = db.get(result.matched_paper_id) if result.matched_paper_id else None
    group = (matched.duplicate_group_id if matched and matched.duplicate_group_id
             else result.matched_paper_id)
    rec.duplicate_group_id = group
    rec.duplicate_of = result.matched_paper_id
    rec.duplicate_confidence = result.confidence

    if result.duplicate_type in (DuplicateType.exact_duplicate, DuplicateType.probable_duplicate):
        rec.screening_status = ScreeningStatus.duplicate
        rec.rejection_reason = result.explanation
    elif result.duplicate_type == DuplicateType.same_dataset_possible:
        rec.screening_status = ScreeningStatus.needs_review
        rec.same_dataset_warning = True
        rec.notes = (rec.notes + " | " if rec.notes else "") + result.explanation
    db.upsert(rec)
    return result.duplicate_type.value


def dedupe_all_papers(db: PaperDB) -> dict:
    records = db.all()
    processed: list[PaperRecord] = []
    counts = {"exact_duplicate": 0, "probable_duplicate": 0,
              "same_dataset_possible": 0, "not_duplicate": 0}
    for rec in records:
        try:
            outcome = apply_dedupe(rec, processed, db)
            counts[outcome] = counts.get(outcome, 0) + 1
        except Exception as exc:  # noqa: BLE001
            logger.error("dedupe 失敗 %s: %s", rec.paper_id, exc)
        processed.append(db.get(rec.paper_id) or rec)
    return counts


# ---------------------------------------------------------------------------
# screen
# ---------------------------------------------------------------------------
def apply_screen(rec: PaperRecord, db: PaperDB) -> str:
    text = get_text_for(rec)
    result = screen(rec, text)

    text_low = text.lower()
    _, detected_country, _ = assess_country(rec, text_low)
    if detected_country in ("thailand", "vietnam"):
        rec.target_country = detected_country.capitalize()
    _gen_fit, n_gen, _, _ = assess_generations(text_low)
    rec.generation_groups = "; ".join(detected_generation_groups(text_low))
    rec.number_of_generations = n_gen
    rec.document_type = result.document_type
    rec.original_language = detect_language(text)
    rec.full_text_available = result.fulltext_fit
    rec.workplace_fit = result.workplace_fit
    if result.category_fit != "unknown":
        rec.category = result.category_fit
    if result.translation_required:
        rec.analysis_language = "en"

    dedupe_locked = rec.screening_status == ScreeningStatus.duplicate or rec.same_dataset_warning
    if not dedupe_locked:
        rec.screening_status = result.decision
        rec.rejection_reason = "" if result.decision == ScreeningStatus.accepted else result.primary_reason
    else:
        note = f"[screen] decision={result.decision.value}; cat={result.category_fit}"
        rec.notes = (rec.notes + " | " if rec.notes else "") + note

    if result.warnings:
        rec.notes = (rec.notes + " | " if rec.notes else "") + " ; ".join(result.warnings)

    db.upsert(rec)
    return rec.screening_status.value


def screen_all_papers(db: PaperDB) -> dict:
    counts: dict[str, int] = {}
    for rec in db.all():
        try:
            outcome = apply_screen(rec, db)
            counts[outcome] = counts.get(outcome, 0) + 1
        except Exception as exc:  # noqa: BLE001
            logger.error("screen 失敗 %s: %s", rec.paper_id, exc)
    return counts
