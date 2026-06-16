"""DOAJ (Directory of Open Access Journals) API による検索 (harvest 用)。

正規化 work 辞書のリストを返す。DOAJ は OA 専門なので OA PDF/landing が得やすい。
ネットワーク不通/requests 不在でも [] を返す。
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from .utils import get_logger

logger = get_logger()

DOAJ_URL = "https://doaj.org/api/search/articles/{query}"


def search_normalized(query: str, *, limit: int = 50,
                      mailto: Optional[str] = None) -> list[dict]:
    try:
        import requests  # type: ignore
    except ImportError:
        return []
    url = DOAJ_URL.format(query=quote(query))
    params = {"pageSize": min(max(limit, 1), 100)}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("DOAJ 検索失敗: %s", exc)
        return []

    out: list[dict] = []
    for item in data.get("results", [])[:limit]:
        bj = item.get("bibjson", {}) or {}
        # DOI
        doi = None
        for ident in bj.get("identifier", []):
            if ident.get("type", "").lower() == "doi":
                doi = ident.get("id")
        # fulltext link
        pdf_url = None
        landing = None
        for link in bj.get("link", []):
            if link.get("type") == "fulltext":
                landing = link.get("url")
                if (link.get("content_type") or "").lower() in ("pdf", "application/pdf"):
                    pdf_url = link.get("url")
        out.append({
            "title": bj.get("title") or "",
            "authors": "; ".join(a.get("name", "") for a in bj.get("author", [])),
            "year": int(bj["year"]) if str(bj.get("year", "")).isdigit() else None,
            "doi": doi,
            "abstract": bj.get("abstract") or "",
            "source_name": "DOAJ",
            "source_url": landing,
            "pdf_url": pdf_url or landing,   # DOAJ は OA なので landing も取得可能
            "oa_url": pdf_url or landing or "",
            "open_access_flag": True,        # DOAJ は全て OA
            "document_type_guess": "journal_article",
            "landing_page_url": landing or "",
        })
    logger.info("DOAJ: %d 件取得 (query=%r)", len(out), query)
    return out
