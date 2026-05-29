"""OpenAlex API による論文候補検索。

OpenAlex は無料・APIキー不要。礼儀として mailto を付けて polite pool を使う。
スクレイピングは行わず、公式 REST API のみを使う。

戻り値は「正規化済み候補 dict」のリスト。candidates.csv の列に近い形にする。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

OPENALEX_URL = "https://api.openalex.org/works"
# polite pool 用の連絡先 (環境変数 OPENALEX_MAILTO で上書き可)
DEFAULT_MAILTO = os.environ.get("OPENALEX_MAILTO", "research@example.com")


def _reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """OpenAlex の abstract_inverted_index を平文に復元する。"""
    if not inverted_index:
        return ""
    positions: List[tuple] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in positions)


def _authors(work: Dict[str, Any]) -> str:
    names = [
        a.get("author", {}).get("display_name", "")
        for a in work.get("authorships", [])
    ]
    return "; ".join(n for n in names if n)


def _normalize_work(work: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAlex の work を内部候補 dict に変換する。"""
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    primary = work.get("primary_location") or {}
    source = (primary.get("source") or {}).get("display_name", "")
    oa = work.get("open_access") or {}

    # OpenAlex が把握している OA PDF（best_oa_location）
    best_oa = work.get("best_oa_location") or {}
    pdf_url = best_oa.get("pdf_url") or ""
    landing = primary.get("landing_page_url") or work.get("id", "")

    return {
        "title": work.get("title") or work.get("display_name") or "",
        "authors": _authors(work),
        "year": work.get("publication_year") or "",
        "journal_source": source,
        "doi": doi,
        "url": landing,
        "pdf_url": pdf_url,
        "oa_status": oa.get("oa_status", ""),
        "oa_source": ("openalex:" + (best_oa.get("version") or "oa")) if pdf_url else "",
        "license": best_oa.get("license") or "",
        "is_oa": bool(oa.get("is_oa")),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "source_api": "openalex",
        "openalex_id": work.get("id", ""),
    }


def search(query: str, limit: int = 20, mailto: str = DEFAULT_MAILTO,
           session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """1つのクエリ文字列で OpenAlex を検索し、正規化候補のリストを返す。"""
    sess = session or requests.Session()
    params = {
        "search": query,
        "per_page": min(max(limit, 1), 200),
        "mailto": mailto,
    }
    try:
        resp = sess.get(OPENALEX_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:  # ネットワーク失敗は空で返す
        print(f"[search_openalex] query='{query}' でエラー: {exc}")
        return []

    results = resp.json().get("results", [])
    return [_normalize_work(w) for w in results[:limit]]


def search_many(queries: List[str], limit_per_query: int = 20,
                mailto: str = DEFAULT_MAILTO) -> List[Dict[str, Any]]:
    """複数クエリをまとめて検索（重複排除は deduplicate.py に任せる）。"""
    sess = requests.Session()
    out: List[Dict[str, Any]] = []
    for q in queries:
        out.extend(search(q, limit=limit_per_query, mailto=mailto, session=sess))
    return out


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="OpenAlex を検索して候補を表示")
    ap.add_argument("query", help="検索クエリ文字列")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    for cand in search(args.query, limit=args.limit):
        print(json.dumps(cand, ensure_ascii=False)[:300])
