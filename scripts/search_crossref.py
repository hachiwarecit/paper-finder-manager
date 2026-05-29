"""Crossref REST API による検索 (MVP では補助。後から本採用)。

OpenAlex と同じ正規化候補 dict を返すので、run_search_pipeline から
差し替え・併用できる。スクレイピングは行わず公式 API のみ。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

CROSSREF_URL = "https://api.crossref.org/works"
DEFAULT_MAILTO = os.environ.get("CROSSREF_MAILTO", "research@example.com")


def _authors(item: Dict[str, Any]) -> str:
    names = []
    for a in item.get("author", []) or []:
        name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
        if name:
            names.append(name)
    return "; ".join(names)


def _year(item: Dict[str, Any]) -> Any:
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (item.get(key) or {}).get("date-parts") or [[]]
        if parts and parts[0]:
            return parts[0][0]
    return ""


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    title_list = item.get("title") or [""]
    container = item.get("container-title") or [""]
    return {
        "title": title_list[0] if title_list else "",
        "authors": _authors(item),
        "year": _year(item),
        "journal_source": container[0] if container else "",
        "doi": item.get("DOI", ""),
        "url": item.get("URL", ""),
        "pdf_url": "",  # Crossref は OA PDF を直接持たない -> Unpaywall で補う
        "oa_status": "",
        "abstract": _strip_jats(item.get("abstract", "")),
        "source_api": "crossref",
    }


def _strip_jats(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def search(query: str, limit: int = 20, mailto: str = DEFAULT_MAILTO,
           session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    sess = session or requests.Session()
    params = {"query": query, "rows": min(max(limit, 1), 100),
              "mailto": mailto}
    try:
        resp = sess.get(CROSSREF_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[search_crossref] query='{query}' でエラー: {exc}")
        return []
    items = resp.json().get("message", {}).get("items", [])
    return [_normalize(i) for i in items[:limit]]


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Crossref を検索")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    for c in search(args.query, limit=args.limit):
        print(json.dumps(c, ensure_ascii=False)[:300])
