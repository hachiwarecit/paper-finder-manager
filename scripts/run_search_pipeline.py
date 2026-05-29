"""検索パイプラインのエントリポイント。

OpenAlex で検索 -> Unpaywall で OA 補強 -> 自動分類 -> 重複チェック ->
OA PDF ダウンロード -> CSV/JSON とレポートを出力、までを通す。

使い方:
    # 単一の国 × カテゴリ
    python scripts/run_search_pipeline.py \
        --country Thailand \
        --category "Status Quo Bias / Resistance to Change" --limit 20

    # 1か国の全カテゴリ
    python scripts/run_search_pipeline.py --country Thailand --all-categories --limit 20

    # 3か国 × 全カテゴリ
    python scripts/run_search_pipeline.py --all-countries --all-categories --limit 20

MVP では OpenAlex + Unpaywall を使う。Crossref / Semantic Scholar は後で追加する。
最終採用判断は人間が行う。
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

import requests

import classify_candidate
import deduplicate as dedup
import download_oa_pdfs
import export_tables
import search_openalex
from check_unpaywall import DEFAULT_EMAIL, enrich
from common import (
    CATEGORIES,
    COUNTRIES,
    PAPERS_PER_SLOT,
    load_search_queries,
    make_id,
    now_iso,
    slot_name,
)

# 自動 slot 割り当て時のスコア用ステータス重み
_STATUS_WEIGHT = {
    "strong_candidate": 4,
    "candidate": 3,
    "manual_download_needed": 2,
    "needs_review": 1,
}


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="論文候補検索パイプライン")
    g_country = ap.add_mutually_exclusive_group(required=True)
    g_country.add_argument("--country", choices=COUNTRIES, help="対象国")
    g_country.add_argument("--all-countries", action="store_true",
                           help="3か国すべて")

    g_cat = ap.add_mutually_exclusive_group(required=True)
    g_cat.add_argument("--category", choices=CATEGORIES, help="対象カテゴリ")
    g_cat.add_argument("--all-categories", action="store_true",
                       help="全6カテゴリ")

    ap.add_argument("--limit", type=int, default=20,
                    help="クエリごとの取得件数上限")
    ap.add_argument("--no-download", action="store_true",
                    help="OA PDF のダウンロードをスキップ")
    ap.add_argument("--mailto", default=search_openalex.DEFAULT_MAILTO,
                    help="OpenAlex polite pool 用メール")
    ap.add_argument("--email", default=DEFAULT_EMAIL,
                    help="Unpaywall 用メール (必須)")
    return ap.parse_args(argv)


def _targets(args: argparse.Namespace):
    countries = COUNTRIES if args.all_countries else [args.country]
    categories = CATEGORIES if args.all_categories else [args.category]
    for c in countries:
        for cat in categories:
            yield c, cat


def collect(country: str, category: str, queries: List[str], *,
            limit: int, mailto: str, email: str,
            session: requests.Session) -> List[Dict[str, Any]]:
    """1つの国 × カテゴリについて検索・補強・分類した候補を返す。"""
    raw = search_openalex.search_many(queries, limit_per_query=limit, mailto=mailto)
    print(f"  [{country} / {category}] OpenAlex から {len(raw)} 件取得")

    out: List[Dict[str, Any]] = []
    for cand in raw:
        cand["country"] = country
        cand["category"] = category
        cand["slot"] = ""
        cand["local_pdf_path"] = ""
        cand["notes"] = ""
        cand["duplicate_of"] = ""
        enrich(cand, email=email, session=session)
        classify_candidate.classify(cand, country, category)
        cand["id"] = make_id(cand)
        cand.setdefault("created_at", now_iso())
        cand["updated_at"] = now_iso()
        out.append(cand)
    return out


def _score(cand: Dict[str, Any]) -> tuple:
    status_w = _STATUS_WEIGHT.get(cand.get("screening_status", ""), 0)
    try:
        matches = int(str(cand.get("related_category", "0")).split()[0])
    except (ValueError, IndexError):
        matches = 0
    has_pdf = 1 if cand.get("pdf_url") else 0
    try:
        year = int(cand.get("year") or 0)
    except (ValueError, TypeError):
        year = 0
    return (status_w, matches, has_pdf, year)


def assign_slots(candidates: List[Dict[str, Any]]) -> None:
    """国 × カテゴリごとに上位候補へ暫定 slot を割り当てる。

    重複・除外は対象外。既に slot が手動指定されている候補は尊重する。
    各枠 (1本目・2本目) に最大 PAPERS_PER_SLOT 件まで。
    """
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    taken: Dict[tuple, set] = {}

    for cand in candidates:
        key = (cand.get("country"), cand.get("category"))
        if cand.get("slot"):  # 手動 or 既存割り当てを尊重
            taken.setdefault(key, set()).add(cand["slot"])
            continue
        status = cand.get("screening_status", "")
        if status == "duplicate" or status.startswith("exclude"):
            continue
        groups.setdefault(key, []).append(cand)

    for (country, category), cands in groups.items():
        if not country or not category:
            continue
        cands.sort(key=_score, reverse=True)
        used = taken.get((country, category), set())
        paper_no = 1
        for cand in cands:
            while paper_no <= PAPERS_PER_SLOT:
                slot = slot_name(country, paper_no, category)
                if slot not in used:
                    break
                paper_no += 1
            if paper_no > PAPERS_PER_SLOT:
                break
            slot = slot_name(country, paper_no, category)
            cand["slot"] = slot
            used.add(slot)
            paper_no += 1


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    all_queries = load_search_queries()
    session = requests.Session()

    new_candidates: List[Dict[str, Any]] = []
    for country, category in _targets(args):
        queries = all_queries.get(country, {}).get(category, [])
        if not queries:
            print(f"  [{country} / {category}] クエリ未定義、スキップ")
            continue
        new_candidates.extend(collect(
            country, category, queries,
            limit=args.limit, mailto=args.mailto, email=args.email,
            session=session,
        ))

    print(f"新規候補: {len(new_candidates)} 件")

    # 既存とマージ
    existing = export_tables.load_existing()
    merged = export_tables.merge(existing, new_candidates)

    # 重複チェック (全体に対して)
    dedup.deduplicate(merged)

    # 暫定 slot 割り当て
    assign_slots(merged)

    # OA PDF ダウンロード
    if not args.no_download:
        n = download_oa_pdfs.download_all(merged, session=session)
        print(f"OA PDF を {n} 件ダウンロードしました")

    # 出力
    export_tables.export_all(merged)
    print(f"候補総数: {len(merged)} 件 -> data/ と reports/ に出力しました")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
