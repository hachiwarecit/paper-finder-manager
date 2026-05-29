"""共通ユーティリティ: パス・設定読み込み・正規化・候補スキーマ。

各スクリプトから import して使う。プロジェクトの単一の真実の源として
ディレクトリ構成・CSV列・カテゴリ・国の定義をここに集約する。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

# --- パス -------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PDF_DIR = ROOT / "pdfs"
REPORTS_DIR = ROOT / "reports"
RULES_DIR = ROOT / "rules"

CANDIDATES_CSV = DATA_DIR / "candidates.csv"
CANDIDATES_JSON = DATA_DIR / "candidates.json"
SEARCH_QUERIES_YML = DATA_DIR / "search_queries.yml"

CATEGORY_KEYWORDS_YML = RULES_DIR / "category_keywords.yml"
COUNTRY_KEYWORDS_YML = RULES_DIR / "country_keywords.yml"
INCLUSION_RULES_YML = RULES_DIR / "inclusion_rules.yml"

# --- 定数 -------------------------------------------------------------------
COUNTRIES: List[str] = ["Thailand", "Vietnam", "Japan"]

# カテゴリ名 -> slot 番号 (1-6)。CLAUDE.md の順序に一致させる。
CATEGORIES: List[str] = [
    "Stereotypes / Ageism",
    "Work Values / Work Ethic",
    "Knowledge Transfer / Mentoring",
    "Communication / Psychological Safety",
    "Change Adaptation / Technology Adoption",
    "Status Quo Bias / Resistance to Change",
]
CATEGORY_INDEX: Dict[str, int] = {name: i + 1 for i, name in enumerate(CATEGORIES)}

# 各国×カテゴリで管理する本数 (1本目・2本目)
PAPERS_PER_SLOT = 2

# candidates.csv の列順 (CLAUDE.md と一致)
CSV_COLUMNS: List[str] = [
    "id",
    "country",
    "category",
    "slot",
    "title",
    "authors",
    "year",
    "journal_source",
    "doi",
    "url",
    "pdf_url",
    "oa_status",
    "target_country_region",
    "research_context",
    "abstract",
    "related_category",
    "reason_for_inclusion",
    "screening_status",
    "local_pdf_path",
    "notes",
    "duplicate_of",
    "created_at",
    "updated_at",
]

SCREENING_STATUSES = [
    "candidate",
    "strong_candidate",
    "needs_review",
    "adopted",
    "rejected",
    "exclude_country_mismatch",
    "exclude_no_workplace_context",
    "exclude_no_abstract_or_pdf",
    "manual_download_needed",
    "duplicate",
]

# 採用済みとみなすステータス (missing_slots 判定に使う)
ADOPTED_STATUSES = {"adopted", "strong_candidate"}


# --- 設定読み込み -----------------------------------------------------------
def load_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_category_keywords() -> Dict[str, Dict[str, Any]]:
    return load_yaml(CATEGORY_KEYWORDS_YML)


def load_country_keywords() -> Dict[str, List[str]]:
    return load_yaml(COUNTRY_KEYWORDS_YML)


def load_inclusion_rules() -> Dict[str, Any]:
    return load_yaml(INCLUSION_RULES_YML)


def load_search_queries() -> Dict[str, Dict[str, List[str]]]:
    return load_yaml(SEARCH_QUERIES_YML)


# --- 文字列ユーティリティ ---------------------------------------------------
def slot_name(country: str, paper_no: int, category: str) -> str:
    """例: Thailand_2-6 (2本目・カテゴリ6)。"""
    return f"{country}_{paper_no}-{CATEGORY_INDEX[category]}"


_NON_WORD = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_words: int = 6) -> str:
    """PDFファイル名用。スペース・記号・日本語を排除し短い英数字スラグにする。"""
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    words = [w for w in _NON_WORD.split(text) if w]
    slug = "_".join(words[:max_words])
    return slug[:60] or "untitled"


_PUNCT = re.compile(r"[^a-z0-9\s]+")
_WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """重複判定用の正規化タイトル。小文字・記号除去・空白圧縮。"""
    t = unicodedata.normalize("NFKD", title or "")
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_id(candidate: Dict[str, Any]) -> str:
    """候補の決定論的ID。再実行時に同一論文をマージできるようにする。

    DOI があれば DOI ベース、無ければ正規化タイトルのハッシュ。
    """
    import hashlib

    doi = (candidate.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    norm = normalize_title(candidate.get("title", ""))
    if norm:
        return "ttl:" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]
    return "raw:" + hashlib.sha1(
        repr(sorted(candidate.items())).encode("utf-8")
    ).hexdigest()[:16]
