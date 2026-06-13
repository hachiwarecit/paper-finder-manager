"""人間が編集した Excel / CSV からメタデータを取り込み、PaperRecord を更新する。

ワークフロー:
    1. export --format xlsx で paper_inventory.xlsx を出力
    2. 人間が Excel を開いて authors / sample_size / doi 等を補完
    3. import-metadata で編集内容を SQLite に反映
    4. dedupe-all / screen-all / report で再判定

方針:
    - 更新対象は EDITABLE_COLUMNS のみ。screening_status などの判定結果列は触らない。
    - **空欄（空文字 / NaN / None）は既存値を上書きしない**（補完であって消去ではない）。
    - paper_id をキーに既存レコードを探す。存在しない paper_id はスキップ（警告）。
    - 不正な値（数値列に文字、document_type に未知の値）はスキップして警告。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .db import PaperDB
from .models import DocumentType, PaperRecord
from .utils import get_logger, normalize_title

logger = get_logger()

# 取り込み対象列 -> PaperRecord フィールド名
# Excel 列名 "method" は research_method に対応（export 側の列名にあわせる）。
EDITABLE_COLUMNS: dict[str, str] = {
    "title": "title",
    "authors": "authors",
    "year": "year",
    "doi": "doi",
    "country": "country",
    "category": "category",
    "target_country": "target_country",
    "organization_context": "organization_context",
    "generation_groups": "generation_groups",
    "number_of_generations": "number_of_generations",
    "sample_size": "sample_size",
    "method": "research_method",
    "research_method": "research_method",
    "document_type": "document_type",
    "original_language": "original_language",
    "analysis_language": "analysis_language",
    "notes": "notes",
}

_INT_FIELDS = {"year", "number_of_generations", "sample_size"}


@dataclass
class ImportStats:
    rows_read: int = 0
    records_updated: int = 0
    fields_updated: int = 0
    skipped_missing_id: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_blank(value) -> bool:
    """空欄判定: None / 空文字 / 空白のみ / NaN を空とみなす。"""
    if value is None:
        return True
    # pandas/float NaN (NaN != NaN)
    if isinstance(value, float) and value != value:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _read_rows(path: str | Path) -> list[dict]:
    """xlsx / csv を読み、ヘッダ付きの dict 行リストを返す。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {p}")

    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx(p)
    if suffix in (".csv", ".txt"):
        return _read_csv(p)
    raise ValueError(f"対応していない形式です: {suffix} (xlsx / csv)")


def _read_xlsx(p: Path) -> list[dict]:
    import openpyxl  # type: ignore

    wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    # All_Papers シートを優先。無ければ先頭シート。
    ws = wb["All_Papers"] if "All_Papers" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = list(next(rows_iter))
    except StopIteration:
        return []
    header = [str(h).strip() if h is not None else "" for h in header]
    out = []
    for row in rows_iter:
        out.append({header[i]: row[i] for i in range(min(len(header), len(row)))})
    wb.close()
    return out


def _read_csv(p: Path) -> list[dict]:
    import csv

    # Excel が書く UTF-8 BOM (utf-8-sig) にも対応
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


def _coerce(field_name: str, value, stats: ImportStats, paper_id: str):
    """フィールド型に合わせて値を変換。失敗時は (None, False)。成功時 (val, True)。"""
    if field_name in _INT_FIELDS:
        try:
            return int(float(str(value).strip())), True
        except (TypeError, ValueError):
            stats.warnings.append(f"{paper_id}: {field_name} の値 '{value}' は数値でないため無視。")
            return None, False
    if field_name == "document_type":
        v = str(value).strip()
        valid = {d.value for d in DocumentType}
        if v not in valid:
            stats.warnings.append(
                f"{paper_id}: document_type '{v}' は不正。許可値: {sorted(valid)}。無視。"
            )
            return None, False
        return DocumentType(v), True
    return str(value).strip(), True


def import_metadata(path: str | Path, db: PaperDB | None = None) -> ImportStats:
    """編集済み Excel/CSV を読み込み、空欄以外の編集列で既存レコードを更新する。"""
    stats = ImportStats()
    rows = _read_rows(path)
    stats.rows_read = len(rows)

    own = db is None
    db = db or PaperDB()
    try:
        for row in rows:
            pid = row.get("paper_id")
            if _is_blank(pid):
                continue
            pid = str(pid).strip()
            rec = db.get(pid)
            if rec is None:
                stats.skipped_missing_id.append(pid)
                stats.warnings.append(f"{pid}: DB に存在しない paper_id のためスキップ。")
                continue

            changed = False
            title_changed = False
            for col, value in row.items():
                if col not in EDITABLE_COLUMNS:
                    continue
                if _is_blank(value):
                    continue  # 空欄は既存値を保持
                field_name = EDITABLE_COLUMNS[col]
                coerced, ok = _coerce(field_name, value, stats, pid)
                if not ok:
                    continue
                current = getattr(rec, field_name)
                # enum 同士/値同士の比較
                cur_cmp = current.value if hasattr(current, "value") else current
                new_cmp = coerced.value if hasattr(coerced, "value") else coerced
                if str(cur_cmp) == str(new_cmp):
                    continue  # 変化なし
                setattr(rec, field_name, coerced)
                stats.fields_updated += 1
                changed = True
                if field_name == "title":
                    title_changed = True

            if title_changed:
                rec.normalized_title = normalize_title(rec.title)

            if changed:
                db.upsert(rec)
                stats.records_updated += 1
    finally:
        if own:
            db.close()

    logger.info(
        "import-metadata: %d 行読込, %d レコード更新, %d フィールド更新",
        stats.rows_read, stats.records_updated, stats.fields_updated,
    )
    return stats
