"""Semantic Scholar Graph API による検索 (MVP では補助。後から本採用)。

OpenAlex と同じ正規化候補 dict を返す。API キーは任意 (環境変数
SEMANTIC_SCHOLAR_API_KEY)。スクレイピングは行わず公式 API のみ。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,authors,venue,externalIds,openAccessPdf,isOpenAccess"
API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")


def _normalize(paper: Dict[str, Any]) -> Dict[str, Any]:
    authors = "; ".join(a.get("name", "") for a in paper.get("authors", []) or [])
    ext = paper.get("externalIds") or {}
    oa_pdf = paper.get("openAccessPdf") or {}
    return {
        "title": paper.get("title") or "",
        "authors": authors,
        "year": paper.get("year") or "",
        "journal_source": paper.get("venue") or "",
        "doi": ext.get("DOI", "") or "",
        "url": f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}",
        "pdf_url": oa_pdf.get("url", "") or "",
        "oa_status": "open" if paper.get("isOpenAccess") else "",
        "abstract": paper.get("abstract") or "",
        "source_api": "semantic_scholar",
    }


def search(query: str, limit: int = 20,
           session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    sess = session or requests.Session()
    headers = {"x-api-key": API_KEY} if API_KEY else {}
    params = {"query": query, "limit": min(max(limit, 1), 100), "fields": FIELDS}
    try:
        resp = sess.get(S2_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[search_semantic_scholar] query='{query}' でエラー: {exc}")
        return []
    data = resp.json().get("data", []) or []
    return [_normalize(p) for p in data[:limit]]


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Semantic Scholar を検索")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    for c in search(args.query, limit=args.limit):
        print(json.dumps(c, ensure_ascii=False)[:300])
