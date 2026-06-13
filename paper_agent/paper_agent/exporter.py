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
EXPORT_COLUMNS = [
    "paper_id", "country", "category", "title", "authors", "year", "doi",
    "source_url", "pdf_url", "local_path", "original_language",
    "analysis_language", "generation_groups", "sample_size", "method",
    "document_type", "screening_status", "rejection_reason", "duplicate_of",
    "duplicate_confidence", "same_dataset_warning", "notes",
]


def _record_to_export_row(rec: PaperRecord) -> dict:
    local_path = rec.local_text_path or rec.local_pdf_path or ""
    return {
        "paper_id": rec.paper_id,
        "country": rec.country,
        "category": rec.category,
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


def export(fmt: str = "xlsx", db: PaperDB | None = None) -> Path:
    """台帳を出力し、出力ファイル Path を返す。fmt は xlsx / csv。"""
    own_db = db is None
    db = db or PaperDB()
    try:
        records = db.all()
    finally:
        if own_db:
            db.close()

    rows = [_record_to_export_row(r) for r in records]
    out_dir = DATA_DIR / "10_exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        return _export_csv(rows, out_dir)
    return _export_xlsx(rows, records, out_dir)


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


def _export_xlsx(rows: list[dict], records: list[PaperRecord], out_dir: Path) -> Path:
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        logger.warning("pandas 不在のため CSV にフォールバックします。")
        return _export_csv(rows, out_dir)

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

    sheets = {
        "All_Papers": all_df,
        "Accepted": pd.DataFrame(_status(rows, "accepted"), columns=EXPORT_COLUMNS),
        "Supplementary": pd.DataFrame(_status(rows, "supplementary"), columns=EXPORT_COLUMNS),
        "Rejected": pd.DataFrame(_status(rows, "rejected"), columns=EXPORT_COLUMNS),
        "Duplicates": pd.DataFrame(_status(rows, "duplicate"), columns=EXPORT_COLUMNS),
        "Needs_Review": pd.DataFrame(_status(rows, "needs_review"), columns=EXPORT_COLUMNS),
        "Category_Counts": cat_counts,
        "Country_Category_Crosstab": crosstab.reset_index() if not crosstab.empty else crosstab,
    }

    try:
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for name, df in sheets.items():
                df.to_excel(writer, sheet_name=name, index=(name == "Country_Category_Crosstab" and crosstab.empty))
        logger.info("Excel 出力: %s (%d 行)", out_path, len(rows))
        return out_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("Excel 出力失敗 (%s)。CSV にフォールバック。", exc)
        return _export_csv(rows, out_dir)
