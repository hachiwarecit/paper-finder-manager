"""PDF / TXT からのテキスト抽出とセクション解析。

提供する機能:
- extract_text(path)       : PDF/TXT -> 生テキスト + ページ数
- guess_title(text)        : 先頭付近からタイトル推定
- detect_abstract(text)    : Abstract 部分の抽出
- split_sections(text)     : セクション見出しで分割
- select_analysis_sections : 分析対象章のみ残す (Methods/Results/References 等は除外)

PDF 抽出は PyMuPDF(fitz) を優先し、無ければ pdfplumber にフォールバックする。
どちらも無い/失敗した場合は空文字を返し、理由をログに残す (全体処理は止めない)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .utils import get_logger

logger = get_logger()

# 分析対象として残す章 (見出しに含まれうる語)
KEEP_SECTIONS = [
    "abstract", "introduction", "background", "literature review",
    "theoretical", "discussion", "conclusion", "conclusions",
    "summary", "implication", "implications", "limitation",
    "limitations", "future research",
]

# 除外する章
DROP_SECTIONS = [
    "method", "methodology", "methods", "survey", "questionnaire",
    "measurement", "measures", "data analysis", "data collection",
    "result", "results", "finding", "findings", "references",
    "bibliography", "appendix", "acknowledgement", "acknowledgment",
    "acknowledgements", "author bio", "about the author",
]

# 見出し行を判定する正規表現:
#   "1. Introduction" / "2.1 Literature Review" / "INTRODUCTION" / "Discussion" など
_HEADING_RE = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*)[.)]?\s+)?([A-Z][A-Za-z][A-Za-z \-/&]{2,60})\s*$"
)


@dataclass
class ExtractionResult:
    text: str = ""
    page_count: int = 0
    error: str = ""
    method: str = ""


@dataclass
class Section:
    heading: str
    body: str
    keep: bool = False
    is_results_discussion_mix: bool = False


@dataclass
class StructuredText:
    raw_text: str = ""
    page_count: int = 0
    title_guess: str = ""
    abstract: str = ""
    sections: list[Section] = field(default_factory=list)
    needs_review: bool = False  # Results と Discussion が混在している等


# ---------------------------------------------------------------------------
# 抽出
# ---------------------------------------------------------------------------
def extract_text(path: str | Path) -> ExtractionResult:
    """PDF または TXT からテキストを抽出する。"""
    p = Path(path)
    if not p.is_file():
        return ExtractionResult(error=f"file not found: {p}")

    suffix = p.suffix.lower()
    if suffix in (".txt", ".text", ".md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            return ExtractionResult(text=text, page_count=1, method="txt")
        except OSError as exc:
            return ExtractionResult(error=f"txt read error: {exc}")

    if suffix != ".pdf":
        return ExtractionResult(error=f"unsupported file type: {suffix}")

    # 1) PyMuPDF
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(p))
        parts = [page.get_text("text") for page in doc]
        page_count = doc.page_count
        doc.close()
        return ExtractionResult(text="\n".join(parts), page_count=page_count, method="pymupdf")
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("PyMuPDF 抽出失敗 %s: %s", p.name, exc)

    # 2) pdfplumber
    try:
        import pdfplumber  # type: ignore

        parts = []
        with pdfplumber.open(str(p)) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
            page_count = len(pdf.pages)
        return ExtractionResult(text="\n".join(parts), page_count=page_count, method="pdfplumber")
    except ImportError:
        return ExtractionResult(error="no PDF backend (install PyMuPDF or pdfplumber)")
    except Exception as exc:  # noqa: BLE001
        return ExtractionResult(error=f"pdfplumber failed: {exc}")


# ---------------------------------------------------------------------------
# タイトル推定
# ---------------------------------------------------------------------------
def guess_title(text: str) -> str:
    """本文先頭付近から、最も長く意味のありそうな行をタイトルとして推定する。"""
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines()[:40]]
    candidates = []
    for ln in lines:
        if len(ln) < 12 or len(ln) > 200:
            continue
        low = ln.lower()
        if low.startswith(("abstract", "keywords", "doi", "http", "issn", "vol")):
            continue
        # 数字や記号だらけの行は除外
        letters = sum(c.isalpha() for c in ln)
        if letters < len(ln) * 0.6:
            continue
        candidates.append(ln)
    if not candidates:
        return ""
    # 上の方にある、ある程度長い行を優先
    candidates.sort(key=lambda s: (-len(s)))
    return candidates[0]


# ---------------------------------------------------------------------------
# Abstract 検出
# ---------------------------------------------------------------------------
def detect_abstract(text: str) -> str:
    """Abstract 見出しから次の見出し/空行群までを抽出する。"""
    if not text:
        return ""
    m = re.search(r"(?im)^\s*abstract\s*[:.]?\s*$", text)
    if not m:
        m = re.search(r"(?i)\babstract\b[:.\-]?\s", text)
    if not m:
        return ""
    start = m.end()
    rest = text[start:]
    # "Keywords" / "Introduction" / "1." までで切る
    end = re.search(r"(?im)^\s*(keywords?|introduction|1\.\s|jel\b)", rest)
    chunk = rest[: end.start()] if end else rest[:2500]
    return chunk.strip()


# ---------------------------------------------------------------------------
# セクション分割
# ---------------------------------------------------------------------------
def _classify_heading(heading: str) -> tuple[bool, bool]:
    """(keep, is_results_discussion_mix) を返す。"""
    h = heading.lower().strip()
    has_keep = any(k in h for k in KEEP_SECTIONS)
    has_drop = any(d in h for d in DROP_SECTIONS)
    # Results と Discussion が同じ見出しに混在
    mix = ("discussion" in h) and ("result" in h or "finding" in h)
    if mix:
        return True, True
    if has_keep and not has_drop:
        return True, False
    if has_drop:
        return False, False
    # どちらでもない見出しは、いったん残す (情報を失わないため)
    return True, False


def split_sections(text: str) -> list[Section]:
    """見出しらしき行でテキストをセクションに分割する。"""
    if not text:
        return []
    lines = text.splitlines()
    sections: list[Section] = []
    current_heading = "Front Matter"
    buf: list[str] = []

    def flush():
        if buf:
            keep, mix = _classify_heading(current_heading)
            sections.append(
                Section(
                    heading=current_heading,
                    body="\n".join(buf).strip(),
                    keep=keep,
                    is_results_discussion_mix=mix,
                )
            )

    for ln in lines:
        stripped = ln.strip()
        is_heading = False
        if 0 < len(stripped) <= 70:
            mt = _HEADING_RE.match(stripped)
            low = stripped.lower().rstrip(":. ")
            if mt and (
                any(k in low for k in KEEP_SECTIONS)
                or any(d in low for d in DROP_SECTIONS)
                or stripped.isupper()
            ):
                is_heading = True
        if is_heading:
            flush()
            buf = []
            current_heading = stripped
        else:
            buf.append(ln)
    flush()
    return sections


def select_analysis_sections(text: str) -> StructuredText:
    """抽出テキストを構造化し、分析対象章のみ keep=True にする。"""
    st = StructuredText(raw_text=text or "")
    st.title_guess = guess_title(text or "")
    st.abstract = detect_abstract(text or "")
    st.sections = split_sections(text or "")
    st.needs_review = any(s.is_results_discussion_mix for s in st.sections)
    return st


def analysis_text(text: str) -> str:
    """KH Coder へ回す前段として、分析対象章だけを連結したテキストを返す。"""
    st = select_analysis_sections(text)
    parts = []
    for s in st.sections:
        if s.keep and s.body:
            parts.append(s.body)
    if not parts:
        # 章分割に失敗したら全文を返す (情報を失わない)
        return text or ""
    return "\n\n".join(parts)
