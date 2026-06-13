"""Crossref REST API による検索 (harvest 用、Priority B)。

正規化 work 辞書のリストを返す。ネットワーク不通/requests 不在でも [] を返す。
公開 API のみを使用し、スクレイピング・paywall 回避は行わない。
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .utils import get_logger

logger = get_logger()

CROSSREF_URL = "https://api.crossref.org/works"
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_abstract(raw: Optional[str]) -> str:
    if not raw:
        return ""
    # Crossref の abstract は JATS XML タグを含むことがある
    return _TAG_RE.sub(" ", raw).replace("&amp;", "&").strip()


def search_normalized(query: str, *, limit: int = 50,
                      mailto: Optional[str] = None) -> list[dict]:
    """Crossref を検索し、正規化 work 辞書のリストを返す。"""
    try:
        import requests  # type: ignore
    except ImportError:
        logger.warning("requests 不在のため Crossref 検索をスキップ。")
        return []

    mailto = mailto or os.environ.get("CONTACT_EMAIL") or os.environ.get("OPENALEX_MAILTO")
    params = {"query": query, "rows": min(max(limit, 1), 100)}
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": f"paper_agent/1.0 (mailto:{mailto or 'unknown'})"}
    try:
        resp = requests.get(CROSSREF_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Crossref 検索失敗: %s", exc)
        return []

    out: list[dict] = []
    for item in data.get("message", {}).get("items", [])[:limit]:
        title_list = item.get("title") or []
        title = title_list[0] if title_list else ""
        authors = "; ".join(
            f"{a.get('family', '')} {a.get('given', '')}".strip()
            for a in item.get("author", [])
        )
        # 出版年
        year = None
        for key in ("published-print", "published-online", "issued", "created"):
            parts = (item.get(key) or {}).get("date-parts") or []
            if parts and parts[0] and parts[0][0]:
                year = parts[0][0]
                break
        # 文書タイプ推定
        ctype = item.get("type", "")
        dtype = {
            "journal-article": "journal_article",
            "proceedings-article": "conference_paper",
            "dissertation": "thesis",
            "report": "report",
        }.get(ctype, "unknown")

        pdf_url = None
        for link in item.get("link", []):
            if link.get("content-type") == "application/pdf":
                pdf_url = link.get("URL")
                break

        out.append({
            "title": title,
            "authors": authors,
            "year": year,
            "doi": item.get("DOI"),
            "abstract": _clean_abstract(item.get("abstract")),
            "source_name": "Crossref",
            "source_url": item.get("URL"),
            "pdf_url": pdf_url,
            "open_access_flag": bool(pdf_url),
            "document_type_guess": dtype,
            "license": "; ".join(l.get("URL", "") for l in item.get("license", [])) or "unknown",
        })
    logger.info("Crossref: %d 件取得 (query=%r)", len(out), query)
    return out
