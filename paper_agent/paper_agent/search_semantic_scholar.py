"""Semantic Scholar Graph API による検索 (harvest 用)。

正規化 work 辞書のリストを返す。ネットワーク不通/requests 不在でも [] を返す。
公開 API のみ。スクレイピング・paywall回避はしない。
"""
from __future__ import annotations

import os
from typing import Optional

from .utils import get_logger

logger = get_logger()

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,externalIds,openAccessPdf,url,venue,authors"


def search_normalized(query: str, *, limit: int = 50,
                      mailto: Optional[str] = None) -> list[dict]:
    try:
        import requests  # type: ignore
    except ImportError:
        return []
    headers = {}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    params = {"query": query, "limit": min(max(limit, 1), 100), "fields": FIELDS}
    try:
        resp = requests.get(S2_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Semantic Scholar 検索失敗: %s", exc)
        return []

    out: list[dict] = []
    for p in data.get("data", [])[:limit]:
        oa = (p.get("openAccessPdf") or {})
        out.append({
            "title": p.get("title") or "",
            "authors": "; ".join(a.get("name", "") for a in (p.get("authors") or [])),
            "year": p.get("year"),
            "doi": (p.get("externalIds") or {}).get("DOI"),
            "abstract": p.get("abstract") or "",
            "source_name": "SemanticScholar",
            "source_url": p.get("url"),
            "pdf_url": oa.get("url"),
            "oa_url": oa.get("url") or "",
            "open_access_flag": bool(oa.get("url")),
            "document_type_guess": "journal_article" if p.get("venue") else "unknown",
            "landing_page_url": p.get("url") or "",
        })
    logger.info("SemanticScholar: %d 件取得 (query=%r)", len(out), query)
    return out
