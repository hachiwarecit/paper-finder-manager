"""共通ユーティリティ。

- パス・プロジェクトルートの解決
- ロギング設定
- タイトル正規化 / ファイル名スラッグ化
- ハッシュ計算 (ファイル SHA256 / テキスト SHA256)
- 著者文字列のパースと著者集合の重なり計算
- 言語判定 (langdetect があれば使用)
- config(YAML) 読み込み

すべて UTF-8 前提。日本語を含むパスでも動くようにしている。
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# パス
# ---------------------------------------------------------------------------
# このファイルは <project_root>/paper_agent/utils.py に置かれる。
# project_root = .../paper_agent (config/ data/ db/ を含むディレクトリ)
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
DB_DIR = PROJECT_ROOT / "db"
DB_PATH = DB_DIR / "papers.sqlite"
LOG_DIR = PROJECT_ROOT / "logs"

# data 配下のサブフォルダ
DATA_SUBDIRS = [
    "01_candidates",
    "02_downloaded",
    "03_screening",
    "04_accepted",
    "05_supplementary",
    "06_rejected",
    "07_duplicates",
    "08_cleaned",
    "09_translated",
    "10_exports",
]


# ---------------------------------------------------------------------------
# ロギング
# ---------------------------------------------------------------------------
_LOGGER_CONFIGURED = False


def get_logger(name: str = "paper_agent") -> logging.Logger:
    """コンソールとファイルの両方に出力するロガーを返す。"""
    global _LOGGER_CONFIGURED
    logger = logging.getLogger(name)
    if _LOGGER_CONFIGURED:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_DIR / "paper_agent.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        # ファイルに書けなくてもコンソールには出す
        pass

    _LOGGER_CONFIGURED = True
    return logger


def now_iso() -> str:
    """ISO8601 (UTC) の現在時刻文字列。"""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# タイトル正規化
# ---------------------------------------------------------------------------
# コロン・ハイフン・各種引用符などはスペースに置換して差を吸収する。
_PUNCT_TO_SPACE = re.compile(r"[:\-‐-―‘’“”\"'`,.;/()\[\]{}?!]+")
_NON_WORD = re.compile(r"[^0-9a-z฀-๿À-ỿ\s]")
_MULTISPACE = re.compile(r"\s+")


def normalize_title(title: str | None) -> str:
    """タイトルを比較用に正規化する。

    手順:
      1. Unicode 正規化 (NFKC)
      2. 小文字化
      3. コロン・ハイフン・引用符をスペースへ
      4. 残った記号を除去 (英数・タイ文字・ベトナム語(ラテン拡張)・空白は残す)
      5. 連続スペースを 1 個に圧縮
    Thai / Vietnam / workplace などの語はそのまま残る。
    """
    if not title:
        return ""
    t = unicodedata.normalize("NFKC", title)
    t = t.lower()
    t = _PUNCT_TO_SPACE.sub(" ", t)
    t = _NON_WORD.sub(" ", t)
    t = _MULTISPACE.sub(" ", t).strip()
    return t


def slugify(text: str, max_len: int = 60) -> str:
    """ファイル名用のスラッグ (ハイフン区切り、ASCII 寄り) を作る。"""
    if not text:
        return "untitled"
    t = unicodedata.normalize("NFKD", text)
    t = t.encode("ascii", "ignore").decode("ascii")  # 非ASCIIは落とす
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if not t:
        return "untitled"
    if len(t) > max_len:
        t = t[:max_len].rstrip("-")
    return t


def title_case_slug(text: str, max_len: int = 60) -> str:
    """cleaned ファイル名用に Title-Case-Hyphen 形式のスラッグを作る。

    例: "Navigating Generational Diversity" -> "Navigating-Generational-Diversity"
    """
    if not text:
        return "Untitled"
    t = unicodedata.normalize("NFKD", text)
    t = t.encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[A-Za-z0-9]+", t)
    if not words:
        return "Untitled"
    slug = "-".join(w.capitalize() for w in words)
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


# ---------------------------------------------------------------------------
# ハッシュ
# ---------------------------------------------------------------------------
def sha256_file(path: str | Path) -> str | None:
    """ファイルの SHA256。読めなければ None。"""
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def sha256_text(text: str | None) -> str | None:
    """テキストの SHA256。空なら None。

    比較を安定させるため、空白を 1 個に圧縮し小文字化した上でハッシュする。
    """
    if not text:
        return None
    norm = _MULTISPACE.sub(" ", text.lower()).strip()
    if not norm:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 著者
# ---------------------------------------------------------------------------
def parse_authors(authors: str | Iterable[str] | None) -> list[str]:
    """著者文字列/リストを正規化した姓ベースの集合に変換する。

    "Smith, J.; Tran, K." や ["John Smith", "Kim Tran"] を
    ["smith", "tran"] のような比較しやすい形にする。
    """
    if not authors:
        return []
    if isinstance(authors, str):
        # セミコロン / "and" / 改行 で分割
        parts = re.split(r"[;\n]|\band\b", authors)
    else:
        parts = list(authors)

    result: list[str] = []
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        p = unicodedata.normalize("NFKD", p).encode("ascii", "ignore").decode("ascii")
        p = p.lower()
        # "Smith, John" -> 姓は "smith"
        if "," in p:
            surname = p.split(",")[0]
        else:
            tokens = p.split()
            surname = tokens[-1] if tokens else p
        surname = re.sub(r"[^a-z]", "", surname)
        if surname:
            result.append(surname)
    # 重複除去 (順序維持)
    seen: set[str] = set()
    out: list[str] = []
    for s in result:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def author_overlap(a: str | Iterable[str] | None, b: str | Iterable[str] | None) -> float:
    """2つの著者集合の Jaccard 重なり (0..1)。"""
    sa = set(parse_authors(a))
    sb = set(parse_authors(b))
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# 言語判定
# ---------------------------------------------------------------------------
def detect_language(text: str | None) -> str:
    """テキストの言語コードを返す。判定不能なら 'unknown'。

    まずタイ文字・ベトナム語特有文字をヒューリスティックに見て、
    その後 langdetect があれば使う。
    """
    if not text or not text.strip():
        return "unknown"
    # タイ文字が一定割合あれば th
    thai_chars = len(re.findall(r"[฀-๿]", text))
    if thai_chars >= 20:
        return "th"
    # ベトナム語特有のラテン拡張文字
    viet_chars = len(re.findall(r"[ăâđêôơưĂÂĐÊÔƠƯạảấầẩẫậắằẳẵặẹẻẽếềểễệ]", text))
    if viet_chars >= 20:
        return "vi"
    try:
        from langdetect import detect  # type: ignore

        return detect(text[:4000])
    except Exception:
        # ASCII 中心なら英語とみなす
        ascii_ratio = sum(c.isascii() for c in text[:1000]) / max(1, min(1000, len(text)))
        return "en" if ascii_ratio > 0.9 else "unknown"


# ---------------------------------------------------------------------------
# config 読み込み
# ---------------------------------------------------------------------------
def load_yaml(path: str | Path) -> dict:
    """YAML を読み込む。PyYAML が無い/失敗時は空 dict。"""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        import yaml  # type: ignore

        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data or {}
    except Exception as exc:  # noqa: BLE001
        get_logger().warning("YAML 読み込み失敗 %s: %s", p, exc)
        return {}


def load_config(name: str) -> dict:
    """config/<name> を読み込む。"""
    return load_yaml(CONFIG_DIR / name)


def ensure_dirs() -> None:
    """data / db / logs のディレクトリを作成する。"""
    for sub in DATA_SUBDIRS:
        (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
