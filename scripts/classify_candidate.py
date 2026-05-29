"""候補の自動スクリーニング。

CLAUDE.md の判定ルールを実装する:
  - country_check    : 対象国・対象文脈が title/abstract にあるか
  - workplace_check  : 職場・組織文脈の語があるか
  - category_check   : カテゴリキーワードの一致数・一致語
  - pdf_check        : OA PDF があるか

これらから screening_status を提案する。最終採用は人間が行う。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from common import (
    load_category_keywords,
    load_country_keywords,
    load_inclusion_rules,
)

_COUNTRY_KW = load_country_keywords()
_CATEGORY_KW = load_category_keywords()
_RULES = load_inclusion_rules()


def _haystack(candidate: Dict[str, Any]) -> str:
    parts = [
        candidate.get("title", ""),
        candidate.get("abstract", ""),
        candidate.get("journal_source", ""),
    ]
    return " ".join(p for p in parts if p).lower()


def country_check(candidate: Dict[str, Any], country: str) -> Tuple[bool, List[str]]:
    """title/abstract/source に対象国語が含まれるか。

    著者所属だけでは判定しない（abstract/title に文脈があるかを見る）。
    """
    text = _haystack(candidate)
    hits = [kw for kw in _COUNTRY_KW.get(country, []) if kw.lower() in text]
    return (len(hits) > 0, hits)


def workplace_check(candidate: Dict[str, Any]) -> Tuple[bool, List[str]]:
    text = _haystack(candidate)
    hits = [kw for kw in _RULES["workplace_keywords"] if kw.lower() in text]
    return (len(hits) > 0, hits)


def category_check(candidate: Dict[str, Any], category: str) -> Tuple[int, List[str]]:
    """カテゴリキーワードの一致数と一致語を返す。"""
    text = _haystack(candidate)
    keywords = _CATEGORY_KW.get(category, {}).get("keywords", [])
    hits = [kw for kw in keywords if kw.lower() in text]
    return (len(hits), hits)


def pdf_check(candidate: Dict[str, Any]) -> bool:
    return bool(candidate.get("pdf_url"))


def has_abstract_or_pdf(candidate: Dict[str, Any]) -> bool:
    return bool(candidate.get("abstract")) or pdf_check(candidate)


def classify(candidate: Dict[str, Any], country: str, category: str) -> Dict[str, Any]:
    """候補に判定結果を書き込み、screening_status を提案する。

    candidate を in-place で更新し、同じ dict を返す。
    """
    has_country, country_hits = country_check(candidate, country)
    has_workplace, workplace_hits = workplace_check(candidate)
    cat_count, cat_hits = category_check(candidate, category)
    has_text = has_abstract_or_pdf(candidate)
    has_pdf = pdf_check(candidate)

    candidate["target_country_region"] = ", ".join(country_hits) if has_country else ""
    candidate["research_context"] = ", ".join(workplace_hits)
    candidate["related_category"] = f"{cat_count} matches: " + ", ".join(cat_hits)

    reason_bits = []
    if has_country:
        reason_bits.append(f"country={'/'.join(country_hits)}")
    if has_workplace:
        reason_bits.append(f"workplace={len(workplace_hits)} terms")
    if cat_count:
        reason_bits.append(f"category={cat_count} terms")
    candidate["reason_for_inclusion"] = "; ".join(reason_bits)

    status = _decide_status(
        has_country=has_country,
        has_workplace=has_workplace,
        cat_count=cat_count,
        has_text=has_text,
        has_pdf=has_pdf,
    )
    candidate["screening_status"] = status
    return candidate


def _decide_status(*, has_country: bool, has_workplace: bool, cat_count: int,
                   has_text: bool, has_pdf: bool) -> str:
    """判定結果から screening_status を決める。

    除外条件を先に評価し、その後で強さの順に格上げする。
    """
    # --- 除外条件 (CLAUDE.md) ---
    if not has_text:
        return "exclude_no_abstract_or_pdf"
    if not has_workplace:
        return "exclude_no_workplace_context"
    if cat_count == 0:
        # どのカテゴリにも合致しない -> 要レビュー（誤分類の可能性もあるため除外はしない）
        return "needs_review"

    strong = _RULES["strong_candidate"]
    cand = _RULES["candidate"]

    # 対象国文脈が無い場合: 除外せず needs_review（著者所属のみの可能性）
    if not has_country:
        return "needs_review"

    # --- 格上げ判定 ---
    if (
        (not strong["require_country_context"] or has_country)
        and (not strong["require_workplace_context"] or has_workplace)
        and cat_count >= strong["min_category_matches"]
        and (not strong["require_abstract_or_pdf"] or has_text)
    ):
        # OA PDF がなければダウンロードが必要なことを示す
        return "strong_candidate" if has_pdf else "manual_download_needed"

    if (
        (not cand["require_workplace_context"] or has_workplace)
        and cat_count >= cand["min_category_matches"]
        and (not cand["require_abstract_or_pdf"] or has_text)
    ):
        return "candidate" if has_pdf else "manual_download_needed"

    return "needs_review"


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="候補JSONを分類")
    ap.add_argument("--country", required=True)
    ap.add_argument("--category", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--abstract", default="")
    args = ap.parse_args()

    c = {"title": args.title, "abstract": args.abstract}
    print(json.dumps(classify(c, args.country, args.category),
                      ensure_ascii=False, indent=2))
