"""KH Coder 用テキストクリーニング。

- ヘッダー/フッター・ページ番号の除去
- 参考文献以降の除去
- DOI / URL の除去 (任意)
- 崩れた表記号の軽い整形
- 改行整形・段落保持
- UTF-8 で保存

出力ファイル名例:
  TH-5-04_2026_Goerlich_Navigating-Generational-Diversity_cleaned.txt
"""
from __future__ import annotations

import re
from pathlib import Path

from .extractor import analysis_text
from .models import PaperRecord
from .utils import DATA_DIR, get_logger, title_case_slug

logger = get_logger()

_URL_RE = re.compile(r"https?://\S+")
_DOI_RE = re.compile(r"\bdoi:\s*\S+|\b10\.\d{4,9}/\S+", re.IGNORECASE)
_PAGE_NUM_RE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")
_REFERENCES_RE = re.compile(r"(?im)^\s*(references|bibliography|reference list)\s*$")
# 表組みなどで崩れた記号の連続
_BROKEN_SYMBOLS_RE = re.compile(r"[│┃┆┊╎▌▐■□▪▫●○◦·•∙]+")
_REPEAT_PUNCT_RE = re.compile(r"([.\-_=*~])\1{3,}")
_MULTISPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def clean_text(
    text: str,
    *,
    drop_references: bool = True,
    drop_urls: bool = True,
    drop_doi: bool = True,
) -> str:
    """生テキストを KH Coder 用に整形する。"""
    if not text:
        return ""

    # 1) References 以降を落とす
    if drop_references:
        m = _REFERENCES_RE.search(text)
        if m:
            text = text[: m.start()]

    lines = text.splitlines()
    cleaned_lines: list[str] = []
    for ln in lines:
        s = ln.rstrip()
        # ページ番号だけの行
        if _PAGE_NUM_RE.match(s):
            continue
        # 崩れた表記号
        s = _BROKEN_SYMBOLS_RE.sub(" ", s)
        s = _REPEAT_PUNCT_RE.sub(r"\1", s)
        if drop_urls:
            s = _URL_RE.sub("", s)
        if drop_doi:
            s = _DOI_RE.sub("", s)
        s = _MULTISPACE_RE.sub(" ", s).strip()
        cleaned_lines.append(s)

    text = "\n".join(cleaned_lines)

    # 段落を保持しつつ余分な空行を圧縮
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    # 文の途中で改行されている箇所を緩く連結
    text = _join_wrapped_lines(text)
    return text.strip() + "\n"


def _join_wrapped_lines(text: str) -> str:
    """段落内で折り返された行を 1 行に繋ぐ。空行は段落区切りとして残す。"""
    paragraphs = re.split(r"\n\s*\n", text)
    out = []
    for para in paragraphs:
        joined = re.sub(r"\s*\n\s*", " ", para).strip()
        joined = _MULTISPACE_RE.sub(" ", joined)
        if joined:
            out.append(joined)
    return "\n\n".join(out)


def cleaned_filename(rec: PaperRecord) -> str:
    """cleaned テキストのファイル名を生成する。"""
    pid = (rec.paper_id or "PAPER").replace("/", "-")
    year = str(rec.year) if rec.year else "nd"
    # 先頭著者の姓
    first_author = "Anon"
    if rec.authors:
        first = rec.authors.split(";")[0].strip()
        if "," in first:
            first = first.split(",")[0]
        else:
            first = first.split()[-1] if first.split() else first
        first = re.sub(r"[^A-Za-z]", "", first) or "Anon"
    title_slug = title_case_slug(rec.title or "Untitled", max_len=50)
    return f"{pid}_{year}_{first_author if not rec.authors else first}_{title_slug}_cleaned.txt"


def clean_to_file(rec: PaperRecord, raw_text: str, *, sections_only: bool = True) -> Path:
    """分析対象テキストをクリーニングし 08_cleaned に保存。保存先 Path を返す。"""
    source = analysis_text(raw_text) if sections_only else raw_text
    cleaned = clean_text(source)
    out_dir = DATA_DIR / "08_cleaned"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / cleaned_filename(rec)
    out_path.write_text(cleaned, encoding="utf-8")
    logger.info("cleaned 保存: %s (%d 文字)", out_path.name, len(cleaned))
    return out_path
