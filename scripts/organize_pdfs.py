"""download 済み PDF を研究用の分類ルールでファイル名整理する。

元の PDF は消さず、安全のため organized_pdfs/{country}/ に **コピー** する。
ファイル名形式:
    {country}_{category_number}-{sequence}_{short_title}.pdf
    例: Vietnam_4-1_employee_voice_power_distance.pdf

カテゴリ番号 (CLAUDE.md / common.CATEGORY_INDEX と一致):
    1 = Stereotypes / Ageism
    2 = Work Values / Work Ethic
    3 = Knowledge Transfer / Mentoring
    4 = Communication / Psychological Safety
    5 = Change Adaptation / Technology Adoption
    6 = Status Quo Bias / Resistance to Change

入力は data/candidates.csv か reports/candidate_table.csv。
列名が多少違っても落ちないよう存在確認しながら読む。

使い方:
    python scripts/organize_pdfs.py --country Vietnam --dry-run
    python scripts/organize_pdfs.py --country Vietnam
    python scripts/organize_pdfs.py --all-countries
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (
    CANDIDATES_CSV,
    CATEGORIES,
    CATEGORY_INDEX,
    COUNTRIES,
    ORGANIZED_PDF_DIR,
    REPORTS_DIR,
    ROOT,
)

# 整理対象から外す screening_status
SKIP_STATUSES = {"rejected", "duplicate", "exclude_country_mismatch"}

# sequence 採番の優先順位（小さいほど先＝若い番号）
_STATUS_PRIORITY = {
    "adopted": 0,
    "strong_candidate": 1,
    "candidate": 2,
}

RENAME_LOG = REPORTS_DIR / "rename_log.csv"
LOG_COLUMNS = [
    "country", "category", "category_number", "sequence",
    "title", "source_path", "new_path", "status", "note",
]

# 列名のゆらぎを吸収するためのエイリアス
_COLUMN_ALIASES = {
    "country": ["country", "Country", "target_country_region"],
    "category": ["category", "Category"],
    "title": ["title", "Title"],
    "local_pdf_path": ["local_pdf_path", "pdf_path", "local_path", "localPdfPath"],
    "screening_status": ["screening_status", "status", "screeningStatus"],
}


# --------------------------------------------------------------------------
# short_title 生成
# --------------------------------------------------------------------------
def short_title(title: str, max_len: int = 50) -> str:
    """title から短いファイル名片を作る。

    小文字化 / ASCII 化 / 英数字とアンダースコアのみ / 空白・記号は _ /
    連続 _ は1つ / 50文字以内 / 先頭末尾 _ は削除。
    """
    t = unicodedata.normalize("NFKD", title or "")
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "_", t)   # 英数字以外（空白・記号）を _ に
    t = re.sub(r"_+", "_", t).strip("_")
    if len(t) > max_len:
        t = t[:max_len].rstrip("_")
    return t or "untitled"


# --------------------------------------------------------------------------
# 入力読み込み
# --------------------------------------------------------------------------
def _resolve_input(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    # 既定: candidates.csv を優先、無ければ candidate_table.csv
    for cand in (CANDIDATES_CSV, REPORTS_DIR / "candidate_table.csv"):
        if cand.exists():
            return cand
    return None


def _pick(row: Dict[str, str], logical: str) -> str:
    """論理列名に対し、実在するエイリアス列から値を取る。無ければ空文字。"""
    for alias in _COLUMN_ALIASES.get(logical, [logical]):
        if alias in row and row[alias] not in (None, ""):
            return str(row[alias]).strip()
    return ""


def load_rows(input_path: Path) -> List[Dict[str, str]]:
    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


# --------------------------------------------------------------------------
# カテゴリ番号の解決（名前ゆらぎ・番号直書きに対応）
# --------------------------------------------------------------------------
def category_number(category: str) -> Optional[int]:
    if not category:
        return None
    if category in CATEGORY_INDEX:
        return CATEGORY_INDEX[category]
    # 先頭に数字が書かれている場合 ("4", "4 - Communication ...")
    m = re.match(r"\s*([1-6])\b", category)
    if m:
        return int(m.group(1))
    # 大文字小文字・前後空白を無視した一致
    low = category.strip().lower()
    for name, num in CATEGORY_INDEX.items():
        if name.lower() == low:
            return num
    return None


# --------------------------------------------------------------------------
# ファイル名の衝突回避
# --------------------------------------------------------------------------
def _unique_name(base_name: str, used: set, dest_dir: Path):
    """既存ファイル / 本実行で予約済みの名前と衝突しない名前を返す。

    戻り値: (final_name, adjusted)  adjusted=True なら _dupN を付けた。
    """
    if base_name.lower() not in used and not (dest_dir / base_name).exists():
        return base_name, False
    stem = base_name[:-4] if base_name.lower().endswith(".pdf") else base_name
    i = 1
    while True:
        cand = f"{stem}_dup{i}.pdf"
        if cand.lower() not in used and not (dest_dir / cand).exists():
            return cand, True
        i += 1


def _resolve_source(local_pdf_path: str) -> Path:
    """local_pdf_path（ROOT 相対 or 絶対）を実パスに解決する。"""
    p = Path(local_pdf_path)
    return p if p.is_absolute() else (ROOT / p)


# --------------------------------------------------------------------------
# メイン処理
# --------------------------------------------------------------------------
def organize(rows: List[Dict[str, str]], countries: List[str], *,
             dry_run: bool) -> List[Dict[str, Any]]:
    """対象国の PDF を整理し、ログ行のリストを返す。"""
    log: List[Dict[str, Any]] = []
    # 国ごとに「予約済みファイル名」と「カテゴリ別連番」を保持
    used_names: Dict[str, set] = {}
    seq_counter: Dict[tuple, int] = {}

    # 既存の organized ファイル名を予約に取り込み（上書き防止）
    for country in countries:
        d = ORGANIZED_PDF_DIR / country
        used_names[country] = (
            {p.name.lower() for p in d.glob("*.pdf")} if d.exists() else set()
        )

    # 優先順位付きで並べてから採番（adopted/strong を先に）
    def sort_key(r: Dict[str, str]):
        st = _pick(r, "screening_status")
        return (_STATUS_PRIORITY.get(st, 9), _pick(r, "title").lower())

    for country in countries:
        dest_dir = ORGANIZED_PDF_DIR / country
        country_rows = [r for r in rows if _pick(r, "country") == country]
        country_rows.sort(key=sort_key)

        for row in country_rows:
            title = _pick(row, "title")
            category = _pick(row, "category")
            status = _pick(row, "screening_status")
            local = _pick(row, "local_pdf_path")
            cat_num = category_number(category)

            entry = {
                "country": country,
                "category": category,
                "category_number": cat_num if cat_num else "",
                "sequence": "",
                "title": title,
                "source_path": local,
                "new_path": "",
                "status": "",
                "note": "",
            }

            # --- 除外判定 ---
            if status in SKIP_STATUSES:
                entry["status"] = "skipped_rejected"
                entry["note"] = f"screening_status={status}"
                log.append(entry)
                continue
            if not local:
                entry["status"] = "skipped_no_pdf"
                entry["note"] = "local_pdf_path が空"
                log.append(entry)
                continue
            src = _resolve_source(local)
            if not src.exists():
                entry["status"] = "skipped_missing_file"
                entry["note"] = f"ファイルが存在しない: {src}"
                log.append(entry)
                continue
            if cat_num is None:
                entry["status"] = "error"
                entry["note"] = f"カテゴリ番号を解決できない: '{category}'"
                log.append(entry)
                continue

            # --- 採番 & ファイル名生成 ---
            key = (country, cat_num)
            seq = seq_counter.get(key, 0) + 1
            seq_counter[key] = seq
            entry["sequence"] = seq

            base_name = f"{country}_{cat_num}-{seq}_{short_title(title)}.pdf"
            final_name, adjusted = _unique_name(base_name, used_names[country], dest_dir)
            used_names[country].add(final_name.lower())
            dest = dest_dir / final_name
            entry["new_path"] = str(dest)

            if adjusted:
                entry["note"] = f"同名回避: {base_name} -> {final_name}"

            # --- コピー or dry-run ---
            if dry_run:
                entry["status"] = "dry_run"
                log.append(entry)
                continue

            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)  # 元は残す（コピー）
                entry["status"] = "duplicate_filename_adjusted" if adjusted else "copied"
            except Exception as exc:  # noqa: BLE001 - ログに残して継続
                entry["status"] = "error"
                entry["note"] = (entry["note"] + "; " if entry["note"] else "") + str(exc)
            log.append(entry)

    return log


def write_log(log: List[Dict[str, Any]]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RENAME_LOG, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        writer.writeheader()
        for row in log:
            writer.writerow({k: row.get(k, "") for k in LOG_COLUMNS})


def _summary(log: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for r in log:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "0 件"


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="download 済み PDF をファイル名整理")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--country", choices=COUNTRIES, help="対象国")
    g.add_argument("--all-countries", action="store_true", help="3か国すべて")
    ap.add_argument("--dry-run", action="store_true",
                    help="コピーせず、実行内容のみログ出力")
    ap.add_argument("--input", default=None,
                    help="入力CSV（既定: data/candidates.csv か "
                         "reports/candidate_table.csv）")
    return ap.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    countries = COUNTRIES if args.all_countries else [args.country]

    input_path = _resolve_input(args.input)
    if input_path is None:
        print("入力CSVが見つかりません "
              "(data/candidates.csv または reports/candidate_table.csv)。")
        return 1
    print(f"入力: {input_path}")

    rows = load_rows(input_path)
    log = organize(rows, countries, dry_run=args.dry_run)
    write_log(log)

    mode = "DRY-RUN" if args.dry_run else "コピー整理"
    print(f"[{mode}] {len(log)} 件処理: {_summary(log)}")
    print(f"ログ: {RENAME_LOG}")
    if not args.dry_run:
        print(f"出力先: {ORGANIZED_PDF_DIR}/（元PDFは削除していません）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
