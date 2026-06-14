"""Excel / CSV 管理台帳の出力。

出力先: data/10_exports/paper_inventory.xlsx
シート:
  1. All_Papers
  2. Accepted
  3. Supplementary
  4. Rejected
  5. Duplicates
  6. Needs_Review
  7. Category_Counts
  8. Country_Category_Crosstab
"""
from __future__ import annotations

from pathlib import Path

from .db import PaperDB
from .models import PaperRecord
from .utils import DATA_DIR, get_logger

logger = get_logger()

# Excel に出す列
# 人間が編集して import-metadata で取り込める列は EDITABLE_COLUMNS を参照。
EXPORT_COLUMNS = [
    "paper_id", "country", "category", "target_country", "organization_context",
    "title", "authors", "year", "doi", "source_url", "pdf_url", "local_path",
    "original_language", "analysis_language", "generation_groups",
    "number_of_generations", "sample_size", "method", "document_type",
    "screening_status", "rejection_reason", "duplicate_of",
    "duplicate_confidence", "same_dataset_warning", "notes",
]


def _record_to_export_row(rec: PaperRecord) -> dict:
    local_path = rec.local_text_path or rec.local_pdf_path or ""
    return {
        "paper_id": rec.paper_id,
        "country": rec.country,
        "category": rec.category,
        "target_country": rec.target_country,
        "organization_context": rec.organization_context,
        "number_of_generations": rec.number_of_generations,
        "title": rec.title,
        "authors": rec.authors,
        "year": rec.year if rec.year is not None else "",
        "doi": rec.doi or "",
        "source_url": rec.source_url or "",
        "pdf_url": rec.pdf_url or "",
        "local_path": local_path,
        "original_language": rec.original_language,
        "analysis_language": rec.analysis_language,
        "generation_groups": rec.generation_groups,
        "sample_size": rec.sample_size if rec.sample_size is not None else "",
        "method": rec.research_method,
        "document_type": rec.document_type.value if hasattr(rec.document_type, "value") else str(rec.document_type),
        "screening_status": rec.screening_status.value if hasattr(rec.screening_status, "value") else str(rec.screening_status),
        "rejection_reason": rec.rejection_reason,
        "duplicate_of": rec.duplicate_of or "",
        "duplicate_confidence": rec.duplicate_confidence if rec.duplicate_confidence is not None else "",
        "same_dataset_warning": "YES" if rec.same_dataset_warning else "",
        "notes": rec.notes,
    }


# Candidates シートの列 (人間が編集できる。candidate_status を編集 → import-metadata)
CANDIDATE_COLUMNS = [
    "candidate_id", "candidate_status", "candidate_score", "duplicate_status",
    "duplicate_of", "same_dataset_warning", "title", "authors", "year", "doi",
    "query_country", "category", "target_country", "target_country_source",
    "target_country_evidence", "generation_keywords", "workplace_keywords",
    "category_keywords", "document_type_guess", "open_access_flag", "pdf_url",
    "source_name", "source_url", "legality_note", "auto_approve_reason",
    "auto_approve_blockers", "download_status", "download_error", "attempted_url",
    "downloaded_path", "download_timestamp", "notes",
]


def _candidate_to_row(c) -> dict:
    return {
        "candidate_id": c.candidate_id,
        "candidate_status": c.candidate_status.value if hasattr(c.candidate_status, "value") else str(c.candidate_status),
        "candidate_score": c.candidate_score,
        "duplicate_status": c.duplicate_status.value if hasattr(c.duplicate_status, "value") else str(c.duplicate_status),
        "duplicate_of": c.duplicate_of or "",
        "same_dataset_warning": "YES" if c.same_dataset_warning else "",
        "title": c.title,
        "authors": c.authors,
        "year": c.year if c.year is not None else "",
        "doi": c.doi or "",
        "query_country": c.query_country,
        "category": c.category,
        "target_country": c.target_country,
        "target_country_source": c.target_country_source,
        "target_country_evidence": c.target_country_evidence,
        "generation_keywords": c.generation_keywords,
        "workplace_keywords": c.workplace_keywords,
        "category_keywords": c.category_keywords,
        "document_type_guess": c.document_type_guess,
        "open_access_flag": "YES" if c.open_access_flag else "",
        "pdf_url": c.pdf_url or "",
        "source_name": c.source_name,
        "source_url": c.source_url or "",
        "legality_note": c.legality_note,
        "auto_approve_reason": c.auto_approve_reason,
        "auto_approve_blockers": c.auto_approve_blockers,
        "download_status": c.download_status,
        "download_error": c.download_error,
        "attempted_url": c.attempted_url,
        "downloaded_path": c.downloaded_path,
        "download_timestamp": c.download_timestamp,
        "notes": c.notes,
    }


def export(fmt: str = "xlsx", db: PaperDB | None = None) -> Path:
    """台帳を出力し、出力ファイル Path を返す。fmt は xlsx / csv。"""
    own_db = db is None
    db = db or PaperDB()
    try:
        records = db.all()
        candidates = db.all_candidates()
    finally:
        if own_db:
            db.close()

    rows = [_record_to_export_row(r) for r in records]
    cand_rows = [_candidate_to_row(c) for c in candidates]
    out_dir = DATA_DIR / "10_exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        _export_candidates_csv(cand_rows, out_dir)
        return _export_csv(rows, out_dir)
    return _export_xlsx(rows, records, cand_rows, candidates, out_dir)


def _export_candidates_csv(cand_rows: list[dict], out_dir: Path) -> Path:
    import csv

    out_path = out_dir / "candidates.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
        for r in cand_rows:
            writer.writerow(r)
    return out_path


def _export_csv(rows: list[dict], out_dir: Path) -> Path:
    import csv

    out_path = out_dir / "paper_inventory.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    logger.info("CSV 出力: %s (%d 行)", out_path, len(rows))
    return out_path


def _status(rows: list[dict], status: str) -> list[dict]:
    return [r for r in rows if r["screening_status"] == status]


def _export_xlsx(rows: list[dict], records: list[PaperRecord],
                 cand_rows: list[dict], candidates: list, out_dir: Path) -> Path:
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        logger.warning("pandas 不在のため CSV にフォールバックします。")
        _export_candidates_csv(cand_rows, out_dir)
        return _export_csv(rows, out_dir)

    from .analysis import is_analysis_n

    out_path = out_dir / "paper_inventory.xlsx"
    all_df = pd.DataFrame(rows, columns=EXPORT_COLUMNS)

    # カテゴリ集計
    if rows:
        cat_counts = (
            all_df.groupby("category").size().reset_index(name="count").sort_values("count", ascending=False)
        )
        crosstab = pd.crosstab(all_df["country"], all_df["category"])
    else:
        cat_counts = pd.DataFrame(columns=["category", "count"])
        crosstab = pd.DataFrame()

    # Analysis_N: N 条件を満たす論文だけ
    n_rows = [r for r, rec in zip(rows, records) if is_analysis_n(rec)]
    analysis_n_df = pd.DataFrame(n_rows, columns=EXPORT_COLUMNS)

    # 候補シート
    cand_df = pd.DataFrame(cand_rows, columns=CANDIDATE_COLUMNS)
    cand_dups = [r for r in cand_rows if r["duplicate_status"] in ("exact_duplicate", "probable_duplicate")]
    cand_review = [r for r in cand_rows if r["candidate_status"] == "needs_review"]

    sheets = {
        "All_Papers": all_df,
        "Accepted": pd.DataFrame(_status(rows, "accepted"), columns=EXPORT_COLUMNS),
        "Supplementary": pd.DataFrame(_status(rows, "supplementary"), columns=EXPORT_COLUMNS),
        "Rejected": pd.DataFrame(_status(rows, "rejected"), columns=EXPORT_COLUMNS),
        "Duplicates": pd.DataFrame(_status(rows, "duplicate"), columns=EXPORT_COLUMNS),
        "Needs_Review": pd.DataFrame(_status(rows, "needs_review"), columns=EXPORT_COLUMNS),
        "Category_Counts": cat_counts,
        "Country_Category_Crosstab": crosstab.reset_index() if not crosstab.empty else crosstab,
        "Analysis_N": analysis_n_df,
        "Candidates": cand_df,
        "Candidate_Duplicates": pd.DataFrame(cand_dups, columns=CANDIDATE_COLUMNS),
        "Candidate_Needs_Review": pd.DataFrame(cand_review, columns=CANDIDATE_COLUMNS),
    }

    try:
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for name, df in sheets.items():
                df.to_excel(writer, sheet_name=name, index=(name == "Country_Category_Crosstab" and crosstab.empty))
        logger.info("Excel 出力: %s (papers=%d, candidates=%d, analysis_N=%d)",
                    out_path, len(rows), len(cand_rows), len(n_rows))
        return out_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("Excel 出力失敗 (%s)。CSV にフォールバック。", exc)
        _export_candidates_csv(cand_rows, out_dir)
        return _export_csv(rows, out_dir)
