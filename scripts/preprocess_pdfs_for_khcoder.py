"""organized_pdfs の PDF を KH Coder 用に前処理する。

各 PDF からテキストを抽出し、卒業研究の前処理ルールに基づいて
References / Appendix / 統計表などを除去し、質的記述・インタビュー引用を残した
khcoder_cleaned.txt を生成する。

出力 (PDF ごと):
    khcoder_ready/{country}/{paper_id}/
        original.pdf          元PDFのコピー
        extracted_raw.txt     抽出した生テキスト
        khcoder_cleaned.txt   整形済みテキスト (KH Coder 投入用)
        cleaning_notes.txt    残した/削除したセクション・警告・語数
        processing_log.txt    処理ログ

まとめ: reports/khcoder_preprocess_log.csv

使い方:
    python scripts/preprocess_pdfs_for_khcoder.py --country Vietnam
    python scripts/preprocess_pdfs_for_khcoder.py --all-countries
    python scripts/preprocess_pdfs_for_khcoder.py --input organized_pdfs/Vietnam/Vietnam_1-1_xxx.pdf

注意:
 - 初期実装では OCR は必須にしない。抽出テキストが極端に少ない場合は
   "OCR required" と記録してスキップする。
 - 最終的な cleaned text の確認は人間が行う。
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    CATEGORIES,
    COUNTRIES,
    KHCODER_READY_DIR,
    ORGANIZED_PDF_DIR,
    REPORTS_DIR,
)

# テキストが極端に少ない場合は OCR 必須とみなす閾値
MIN_WORDS_FOR_PROCESSING = 100
SCANNED_CHARS_PER_PAGE = 60  # 1ページあたり文字数がこれ未満ならスキャン疑い

PREPROCESS_LOG_CSV = REPORTS_DIR / "khcoder_preprocess_log.csv"
LOG_COLUMNS = [
    "country", "pdf_file", "category_number", "category_name",
    "raw_text_path", "cleaned_text_path", "notes_path", "status",
    "word_count_raw", "word_count_cleaned", "warnings",
]

# --------------------------------------------------------------------------
# セクション見出しの分類
# --------------------------------------------------------------------------
KEEP_HEADINGS = [
    "abstract", "introduction", "literature review", "related work",
    "theoretical background", "conceptual background", "theoretical framework",
    "background", "findings", "results", "discussion", "conclusion",
    "conclusions", "concluding remarks", "summary", "policy recommendation",
    "policy recommendations", "policy implications", "implications",
    "limitations", "limitations and future research", "future research",
    "case description", "case study",
]
METHODS_HEADINGS = [
    "methods", "method", "methodology", "materials and methods",
    "research method", "research methodology", "research design",
    "data and methods", "data collection", "research setting", "sample",
]
DROP_HEADINGS = [
    "references", "reference", "bibliography", "appendix", "appendices",
    "acknowledgement", "acknowledgements", "acknowledgments", "funding",
    "author contributions", "authors contributions", "conflict of interest",
    "conflicts of interest", "declaration of competing interest",
    "competing interests", "ethics", "ethics statement", "ethical approval",
    "data availability", "data availability statement", "informed consent",
    "copyright", "table of contents", "contents", "supplementary material",
    "supplementary materials", "notes", "endnotes", "about the authors",
]

# 質的・心理的障壁に関する説明や引用は表/Appendix でも残す
KEEP_EXCEPTION_KW = [
    "first-order", "first order", "second-order", "second order",
    "aggregate dimension", "aggregate dimensions", "gioia",
    "resistance to change", "psychological safety", "knowledge transfer",
    "knowledge sharing", "technology adoption", "speaking up",
    "employee voice", "power distance", "reverse mentoring", "mentoring",
    "ageism", "stereotype", "work values", "work ethic",
    "barrier", "reluctan", "afraid", "fear of", "hesitat", "silence",
    "trust", "hierarchy", "authority", "respect", "face", "seniority",
]
# Methods 内で残してよい文脈語
METHODS_KEEP_KW = [
    "interview", "qualitative", "participant", "respondent", "informant",
    "case study", "semi-structured", "semistructured", "focus group",
    "grounded theory", "gioia", "thematic", "narrative", "ethnograph",
    "thailand", "thai", "vietnam", "vietnamese", "japan", "japanese",
    "organization", "organisation", "company", "firm", "employees",
    "employee", "workplace", "sector", "industry",
]


def _norm_heading(line: str) -> str:
    """見出し候補を正規化（先頭の番号/ローマ数字・記号を除去して小文字化）。"""
    t = line.strip()
    t = re.sub(r"^[\dIVXivx]+[\.\)]?\s*", "", t)   # "4." "IV)" など
    t = re.sub(r"[^a-zA-Z\s/&-]", "", t).strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def classify_heading(line: str) -> Optional[str]:
    """行が見出しなら 'keep'/'methods'/'drop' を返す。見出しでなければ None。"""
    raw = line.strip()
    if not raw or len(raw) > 70:
        return None
    words = raw.split()
    if len(words) > 8:
        return None
    # 見出しは末尾がピリオドでないことが多い（"e.g." 等の誤検出を避ける）
    if raw.endswith("."):
        return None
    h = _norm_heading(line)
    if not h:
        return None
    for kw in DROP_HEADINGS:
        if h == kw or h.startswith(kw + " ") or h.startswith(kw):
            return "drop"
    for kw in METHODS_HEADINGS:
        if h == kw or h.startswith(kw):
            return "methods"
    for kw in KEEP_HEADINGS:
        if h == kw or h.startswith(kw):
            return "keep"
    return None


# --------------------------------------------------------------------------
# 行レベルのノイズ判定
# --------------------------------------------------------------------------
_DOI_ONLY = re.compile(r"^\s*(doi:?\s*)?(https?://(dx\.)?doi\.org/)?10\.\d{4,9}/\S+\s*$", re.I)
_URL_ONLY = re.compile(r"^\s*(https?://|www\.)\S+\s*$", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PAGE_NUM = re.compile(r"^\s*(page\s+)?\d{1,4}\s*$", re.I)
_AFFIL = re.compile(
    r"^\s*(department|dept\.?|faculty|school|college|institute|"
    r"centre|center|university|laboratory)\b", re.I)
_PVALUE = re.compile(r"\bp\s*[<>=]\s*0?\.\d+", re.I)
_STAT_EQ = re.compile(r"\b([βbtrzfF]|chi|χ2?|se|sd|m)\s*=\s*-?\d", re.I)
_NUM = re.compile(r"-?\d+(\.\d+)?")


GIOIA_KW = [
    "first-order", "first order", "second-order", "second order",
    "aggregate dimension", "aggregate dimensions", "gioia",
]
# 参考文献エントリらしい行（Author, A. (2020). ... pp. 1-20 など）
_CITATION = re.compile(
    r"(\([12]\d{3}[a-z]?\).{0,80}\d+(\s*[-–]\s*\d+)?$)"      # (2020) ... 45-67
    r"|(^\s*[A-Z][A-Za-z'’-]+,\s+[A-Z]\.)",                  # Doe, J.
)


def _has_quote(line: str) -> bool:
    if len(line) > 25 and re.search(r"[“”‘’«»]", line):
        return True
    return line.count('"') >= 2 and len(line) > 25


def is_quote_or_exception(low: str, line: str) -> bool:
    """インタビュー引用・質的説明など、本文(keep/methods)で残したい行か。"""
    if _has_quote(line):
        return True
    return any(kw in low for kw in KEEP_EXCEPTION_KW)


def is_gioia_or_quote(low: str, line: str) -> bool:
    """drop セクション(References/Appendix等)でも残す価値のある行か。

    一般キーワードでは残さない（参考文献の誤検出を防ぐ）。実際の引用文か、
    Gioia 表に固有の語のみを対象とし、引用文献エントリは明示的に除外する。
    """
    if _CITATION.search(line):
        return False
    if _has_quote(line):
        return True
    return any(kw in low for kw in GIOIA_KW)


def is_methods_keep(low: str, line: str) -> bool:
    return is_quote_or_exception(low, line) or any(kw in low for kw in METHODS_KEEP_KW)


def junk_kind(line: str, low: str) -> Optional[str]:
    """ノイズ行なら種別を返す。'doi'|'url'|'email'|'page'|'affil'|'stat'|'table'。"""
    s = line.strip()
    if not s:
        return None
    if _DOI_ONLY.match(s):
        return "doi"
    if _URL_ONLY.match(s):
        return "url"
    if _PAGE_NUM.match(s):
        return "page"
    if _EMAIL.search(s) and len(s) < 120:
        return "email"
    if _AFFIL.match(s) and len(s) < 100 and not s.endswith((".", "?", "!")):
        return "affil"
    if _PVALUE.search(s) or _STAT_EQ.search(s) or "cronbach" in low \
            or "p-value" in low or "t-value" in low:
        return "stat"
    # 数字が多くテキストが少ない行 = 統計表/回帰表の行
    nums = _NUM.findall(s)
    digits = sum(c.isdigit() for c in s)
    nonspace = sum(not c.isspace() for c in s)
    if len(nums) >= 3 and nonspace and digits / nonspace > 0.35:
        return "table"
    return None


# --------------------------------------------------------------------------
# 正規化（記号・ハイフネーション・空白）
# --------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    text = text.replace("-LRB-", "(").replace("-RRB-", ")")
    text = text.replace("-LSB-", "[").replace("-RSB-", "]")
    text = text.replace("⏎", "")          # ⏎ を削除
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("­", "")           # soft hyphen
    # PDF 由来のハイフネーション: 行末 "exam-\nple" -> "example"
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)
    return text


def collapse_spaces(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# --------------------------------------------------------------------------
# クリーニング本体
# --------------------------------------------------------------------------
def clean_text(raw: str) -> Tuple[str, Dict[str, Any]]:
    """生テキストを整形し、(cleaned_text, info) を返す。"""
    info: Dict[str, Any] = {
        "kept_sections": [],
        "removed_sections": [],
        "warnings": set(),
    }
    text = normalize_text(raw)
    lines = text.split("\n")

    mode = "keep"           # keep | methods | drop
    blocks: List[List[str]] = []
    current: List[str] = []

    def flush():
        if current:
            blocks.append(current.copy())
            current.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue

        heading = classify_heading(stripped)
        if heading:
            flush()
            name = stripped
            if heading == "drop":
                mode = "drop"
                info["removed_sections"].append(name)
                low_h = _norm_heading(stripped)
                if low_h.startswith(("references", "reference", "bibliography")):
                    info["warnings"].add("references removed")
                continue
            if heading == "methods":
                mode = "methods"
                info["kept_sections"].append(name + " (minimal/context only)")
                continue
            mode = "keep"
            info["kept_sections"].append(name)
            continue

        low = stripped.lower()

        # ノイズ行
        kind = junk_kind(stripped, low)
        if kind and not is_quote_or_exception(low, stripped):
            if kind in ("stat", "table"):
                info["warnings"].add("tables removed")
            continue

        # モード別の取捨
        if mode == "drop":
            # References/Appendix では本物の引用・Gioia 引用のみ残す
            if is_gioia_or_quote(low, stripped):
                info["warnings"].add("qualitative quotes retained")
                current.append(stripped)
            continue
        if mode == "methods":
            if is_methods_keep(low, stripped):
                current.append(stripped)
            continue
        current.append(stripped)

    flush()

    # ブロックを段落に整形（折り返しを結合）
    paragraphs = []
    for block in blocks:
        para = collapse_spaces(" ".join(block))
        if para:
            paragraphs.append(para)
    cleaned = "\n\n".join(paragraphs)

    if not info["kept_sections"]:
        info["warnings"].add("no section headings detected; line-level cleaning only")
    return cleaned, info


# --------------------------------------------------------------------------
# PDF テキスト抽出（pymupdf -> pdfplumber -> pypdf）
# --------------------------------------------------------------------------
def extract_text(pdf_path: Path, log: List[str]) -> Tuple[str, int]:
    """(text, page_count) を返す。全抽出器が失敗したら ("", 0)。"""
    # 1. pymupdf
    try:
        import fitz  # type: ignore
        doc = fitz.open(pdf_path)
        pages = [doc[i].get_text("text") for i in range(doc.page_count)]
        n = doc.page_count
        doc.close()
        text = "\n".join(pages)
        if text.strip():
            log.append(f"extractor=pymupdf pages={n} chars={len(text)}")
            return text, n
        log.append("pymupdf: テキスト空、次の抽出器を試行")
    except Exception as exc:  # noqa: BLE001
        log.append(f"pymupdf 失敗: {exc}")

    # 2. pdfplumber
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(pdf_path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
            n = len(pdf.pages)
        text = "\n".join(pages)
        if text.strip():
            log.append(f"extractor=pdfplumber pages={n} chars={len(text)}")
            return text, n
        log.append("pdfplumber: テキスト空、次の抽出器を試行")
    except Exception as exc:  # noqa: BLE001
        log.append(f"pdfplumber 失敗: {exc}")

    # 3. pypdf
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        pages = [(pg.extract_text() or "") for pg in reader.pages]
        n = len(reader.pages)
        text = "\n".join(pages)
        log.append(f"extractor=pypdf pages={n} chars={len(text)}")
        return text, n
    except Exception as exc:  # noqa: BLE001
        log.append(f"pypdf 失敗: {exc}")

    return "", 0


# --------------------------------------------------------------------------
# ファイル名 -> 国・カテゴリ
# --------------------------------------------------------------------------
_NAME_RE = re.compile(r"^(?P<country>[A-Za-z]+)_(?P<cat>\d)-(?P<seq>\d+)_")


def parse_meta(pdf_path: Path) -> Dict[str, Any]:
    stem = pdf_path.stem
    country = pdf_path.parent.name
    cat_num: Optional[int] = None
    m = _NAME_RE.match(stem)
    if m:
        country = m.group("country") or country
        cat_num = int(m.group("cat"))
    cat_name = ""
    if cat_num and 1 <= cat_num <= len(CATEGORIES):
        cat_name = CATEGORIES[cat_num - 1]
    return {
        "paper_id": stem,
        "country": country,
        "category_number": cat_num,
        "category_name": cat_name,
    }


def word_count(text: str) -> int:
    return len(text.split())


# --------------------------------------------------------------------------
# 1 PDF の処理
# --------------------------------------------------------------------------
def process_pdf(pdf_path: Path) -> Dict[str, Any]:
    meta = parse_meta(pdf_path)
    out_dir = KHCODER_READY_DIR / meta["country"] / meta["paper_id"]
    log: List[str] = []
    log.append(f"処理日: {date.today().isoformat()}")
    log.append(f"入力PDF: {pdf_path}")
    log.append(f"paper_id={meta['paper_id']} country={meta['country']} "
               f"category_number={meta['category_number']} "
               f"category_name={meta['category_name']}")

    result: Dict[str, Any] = {
        "country": meta["country"],
        "pdf_file": pdf_path.name,
        "category_number": meta["category_number"] or "",
        "category_name": meta["category_name"],
        "raw_text_path": "",
        "cleaned_text_path": "",
        "notes_path": "",
        "status": "",
        "word_count_raw": 0,
        "word_count_cleaned": 0,
        "warnings": "",
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    # 元PDFのコピー（元は消さない）
    try:
        shutil.copy2(pdf_path, out_dir / "original.pdf")
    except Exception as exc:  # noqa: BLE001
        log.append(f"original.pdf コピー失敗: {exc}")

    raw, pages = extract_text(pdf_path, log)
    raw_path = out_dir / "extracted_raw.txt"
    raw_path.write_text(raw, encoding="utf-8")
    result["raw_text_path"] = str(raw_path)
    wc_raw = word_count(raw)
    result["word_count_raw"] = wc_raw
    log.append(f"word_count_raw={wc_raw} pages={pages}")

    warnings: set = set()
    avg_per_page = (len(raw) / pages) if pages else 0

    # --- テキスト不足 / OCR 判定 ---
    if not raw.strip():
        result["status"] = "skipped_no_text"
        warnings.add("text extraction may be imperfect")
        log.append("テキストを抽出できなかった -> skipped_no_text")
        _write_notes(out_dir, meta, [], [], warnings, wc_raw, 0)
        result["notes_path"] = str(out_dir / "cleaning_notes.txt")
        result["warnings"] = "; ".join(sorted(warnings))
        (out_dir / "processing_log.txt").write_text("\n".join(log), encoding="utf-8")
        return result

    if wc_raw < MIN_WORDS_FOR_PROCESSING:
        result["status"] = "ocr_required"
        warnings.add("OCR required")
        if pages and avg_per_page < SCANNED_CHARS_PER_PAGE:
            warnings.add("scanned PDF suspected")
        warnings.add("text extraction may be imperfect")
        log.append(f"抽出語数 {wc_raw} < {MIN_WORDS_FOR_PROCESSING} -> OCR required")
        _write_notes(out_dir, meta, [], [], warnings, wc_raw, 0)
        result["notes_path"] = str(out_dir / "cleaning_notes.txt")
        result["warnings"] = "; ".join(sorted(warnings))
        (out_dir / "processing_log.txt").write_text("\n".join(log), encoding="utf-8")
        return result

    # --- クリーニング ---
    try:
        cleaned, info = clean_text(raw)
        warnings |= info["warnings"]
        cleaned_path = out_dir / "khcoder_cleaned.txt"
        cleaned_path.write_text(cleaned, encoding="utf-8")
        wc_clean = word_count(cleaned)
        result["cleaned_text_path"] = str(cleaned_path)
        result["word_count_cleaned"] = wc_clean
        result["status"] = "processed"
        log.append(f"word_count_cleaned={wc_clean}")
        log.append(f"kept_sections={info['kept_sections']}")
        log.append(f"removed_sections={info['removed_sections']}")

        _write_notes(out_dir, meta, info["kept_sections"],
                     info["removed_sections"], warnings, wc_raw, wc_clean)
        result["notes_path"] = str(out_dir / "cleaning_notes.txt")
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        warnings.add("text extraction may be imperfect")
        log.append(f"クリーニング失敗: {exc}")

    result["warnings"] = "; ".join(sorted(warnings))
    (out_dir / "processing_log.txt").write_text("\n".join(log), encoding="utf-8")
    return result


def _write_notes(out_dir: Path, meta: Dict[str, Any], kept: List[str],
                 removed: List[str], warnings: set,
                 wc_raw: int, wc_clean: int) -> None:
    lines = [
        f"paper_id: {meta['paper_id']}",
        f"source_pdf: {meta['paper_id']}.pdf",
        f"country: {meta['country']}",
        f"category_number: {meta['category_number'] or ''}",
        f"category_name: {meta['category_name']}",
        f"processing_date: {date.today().isoformat()}",
        "",
        "kept_sections:",
    ]
    lines += [f"- {s}" for s in kept] or ["- (none detected)"]
    lines += ["", "removed_sections:"]
    lines += [f"- {s}" for s in removed] or ["- (none detected)"]
    lines += ["", "warnings:"]
    lines += [f"- {w}" for w in sorted(warnings)] or ["- (none)"]
    lines += [
        "",
        f"word_count_raw: {wc_raw}",
        f"word_count_cleaned: {wc_clean}",
    ]
    (out_dir / "cleaning_notes.txt").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------
# 対象 PDF の収集
# --------------------------------------------------------------------------
def collect_pdfs(countries: List[str], input_path: Optional[str]) -> List[Path]:
    if input_path:
        p = Path(input_path)
        return [p] if p.exists() and p.suffix.lower() == ".pdf" else []
    pdfs: List[Path] = []
    for c in countries:
        d = ORGANIZED_PDF_DIR / c
        if d.exists():
            pdfs.extend(sorted(d.glob("*.pdf")))
    return pdfs


def write_summary(results: List[Dict[str, Any]]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(PREPROCESS_LOG_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in LOG_COLUMNS})


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="organized_pdfs を KH Coder 用に前処理")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--country", choices=COUNTRIES, help="対象国")
    g.add_argument("--all-countries", action="store_true", help="3か国すべて")
    g.add_argument("--input", help="単一PDFのパス")
    return ap.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    countries = (COUNTRIES if args.all_countries
                 else [args.country] if args.country else [])

    pdfs = collect_pdfs(countries, args.input)
    if not pdfs:
        target = args.input or f"organized_pdfs/{countries}"
        print(f"対象PDFが見つかりません: {target}")
        return 1

    print(f"{len(pdfs)} 件のPDFを処理します")
    results = []
    for pdf in pdfs:
        r = process_pdf(pdf)
        results.append(r)
        print(f"  {r['status']:16} {pdf.name}"
              + (f"  ({r['warnings']})" if r["warnings"] else ""))

    write_summary(results)
    counts: Dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("結果: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"出力: {KHCODER_READY_DIR}/  まとめ: {PREPROCESS_LOG_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
