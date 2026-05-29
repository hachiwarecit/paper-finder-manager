"""候補の自動スクリーニング（スコアベース）。

CLAUDE.md の判定に加え、医学・感染症・疫学・公衆衛生論文や「物理的作業環境」を
扱う論文を、対象国や "workplace" 語が含まれていても除外/要レビューに回す。

候補ごとに 4 つのスコアを計算する:
  - country_score                : 対象国・対象文脈の語の一致数
  - workplace_organization_score : 組織・人・心理・世代・文化の語の一致数
  - category_score               : 6カテゴリのキーワード一致数
  - exclusion_medical_score      : 医学/公衆衛生 + 物理的作業環境の語の一致数

exclusion_medical_score が高く workplace_organization_score が低いものは
exclude_no_workplace_context にし、screening_reason に理由を記録する。
最終採用は人間が行う。
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

_ORG_KW = _RULES.get("organizational_keywords", _RULES.get("workplace_keywords", []))
_MED_KW = _RULES.get("medical_exclusion_keywords", [])
_PHYS_KW = _RULES.get("physical_environment_keywords", [])
_SCORING = _RULES.get("scoring", {})
_ORG_MIN = _SCORING.get("org_context_min", 2)
_ORG_WEAK = _SCORING.get("org_context_weak", 1)
_MED_HIGH = _SCORING.get("medical_high", 2)
_STRONG_CAT_MIN = _SCORING.get("strong_category_min", 2)


def _haystack(candidate: Dict[str, Any]) -> str:
    parts = [
        candidate.get("title", ""),
        candidate.get("abstract", ""),
        candidate.get("journal_source", ""),
    ]
    return " ".join(p for p in parts if p).lower()


def _hits(text: str, keywords: List[str]) -> List[str]:
    """text に含まれるキーワードを（重複なしで）返す。"""
    seen = []
    for kw in keywords:
        if kw.lower() in text and kw not in seen:
            seen.append(kw)
    return seen


# --- 個別チェック（互換 API） ---------------------------------------------
def country_check(candidate: Dict[str, Any], country: str) -> Tuple[bool, List[str]]:
    """title/abstract/source に対象国語が含まれるか（著者所属だけでは判定しない）。"""
    hits = _hits(_haystack(candidate), _COUNTRY_KW.get(country, []))
    return (len(hits) > 0, hits)


def organizational_check(candidate: Dict[str, Any]) -> Tuple[int, List[str]]:
    """組織・人・心理・世代・文化の語の一致。"""
    hits = _hits(_haystack(candidate), _ORG_KW)
    return (len(hits), hits)


# 旧名互換（組織文脈チェックに委譲）
def workplace_check(candidate: Dict[str, Any]) -> Tuple[bool, List[str]]:
    count, hits = organizational_check(candidate)
    return (count > 0, hits)


def category_check(candidate: Dict[str, Any], category: str) -> Tuple[int, List[str]]:
    keywords = _CATEGORY_KW.get(category, {}).get("keywords", [])
    hits = _hits(_haystack(candidate), keywords)
    return (len(hits), hits)


def medical_check(candidate: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(医学/公衆衛生の一致語, 物理的作業環境の一致語) を返す。"""
    text = _haystack(candidate)
    return (_hits(text, _MED_KW), _hits(text, _PHYS_KW))


def pdf_check(candidate: Dict[str, Any]) -> bool:
    return bool(candidate.get("pdf_url"))


def has_abstract_or_pdf(candidate: Dict[str, Any]) -> bool:
    return bool(candidate.get("abstract")) or pdf_check(candidate)


# --- 分類本体 ---------------------------------------------------------------
def classify(candidate: Dict[str, Any], country: str, category: str) -> Dict[str, Any]:
    """候補にスコア・判定・理由を書き込む。in-place 更新して同じ dict を返す。"""
    has_country, country_hits = country_check(candidate, country)
    org_count, org_hits = organizational_check(candidate)
    cat_count, cat_hits = category_check(candidate, category)
    med_hits, phys_hits = medical_check(candidate)
    has_text = has_abstract_or_pdf(candidate)
    has_pdf = pdf_check(candidate)

    country_score = len(country_hits)
    workplace_organization_score = org_count
    category_score = cat_count
    exclusion_medical_score = len(med_hits) + len(phys_hits)

    candidate["country_score"] = country_score
    candidate["workplace_organization_score"] = workplace_organization_score
    candidate["category_score"] = category_score
    candidate["exclusion_medical_score"] = exclusion_medical_score

    candidate["target_country_region"] = ", ".join(country_hits)
    research_context = ", ".join(org_hits)
    if phys_hits:
        research_context += (
            (" | " if research_context else "")
            + "physical-env: " + ", ".join(phys_hits)
        )
    candidate["research_context"] = research_context
    candidate["related_category"] = f"{cat_count} matches: " + ", ".join(cat_hits)

    # 採用根拠の要約（ポジティブ側）
    candidate["reason_for_inclusion"] = (
        f"country={country_score}, org={workplace_organization_score}, "
        f"category={category_score}, medical/physical={exclusion_medical_score}"
    )

    status, reasons = _decide(
        country_score=country_score,
        org_score=workplace_organization_score,
        org_hits=org_hits,
        cat_score=category_score,
        cat_hits=cat_hits,
        country_hits=country_hits,
        med_hits=med_hits,
        phys_hits=phys_hits,
        med_score=exclusion_medical_score,
        has_text=has_text,
        has_pdf=has_pdf,
    )
    candidate["screening_status"] = status
    candidate["screening_reason"] = "; ".join(reasons)
    return candidate


def _decide(*, country_score: int, org_score: int, org_hits: List[str],
            cat_score: int, cat_hits: List[str], country_hits: List[str],
            med_hits: List[str], phys_hits: List[str], med_score: int,
            has_text: bool, has_pdf: bool) -> Tuple[str, List[str]]:
    """スコアから screening_status と理由のリストを決める。"""
    if not has_text:
        return "exclude_no_abstract_or_pdf", ["no abstract or PDF available"]

    med_high = med_score >= _MED_HIGH
    org_ok = org_score >= _ORG_MIN

    def med_reasons() -> List[str]:
        r = []
        if med_hits:
            r.append("medical/public health context: " + ", ".join(med_hits))
        if phys_hits:
            r.append("physical workplace environment, not organizational "
                     "workplace: " + ", ".join(phys_hits))
        return r

    # 1) 医学/物理環境が強く、組織文脈が弱い -> 除外
    if med_high and not org_ok:
        reasons = med_reasons() or ["medical context without organizational "
                                    "workplace context"]
        return "exclude_no_workplace_context", reasons

    # 2) 医学/物理環境が強いが組織文脈もある -> 人間レビュー
    if med_high and org_ok:
        return "needs_review", med_reasons() + [
            "organizational signals present; needs human review"]

    # 3) 組織文脈が無い -> 除外
    if org_score == 0:
        reasons = ["no organizational workplace context "
                   "(organizational keywords not found)"]
        if phys_hits:
            reasons.append("physical workplace environment, not organizational "
                           "workplace: " + ", ".join(phys_hits))
        return "exclude_no_workplace_context", reasons

    # 4) 組織文脈が弱い（語が1つだけ）-> 人間レビュー
    if org_score <= _ORG_WEAK:
        return "needs_review", [
            f"weak organizational workplace context (only {org_score} signal)"]

    # 5) カテゴリ不一致 -> 人間レビュー
    if cat_score == 0:
        return "needs_review", ["no category keyword match"]

    # 6) 対象国文脈が未確認 -> 人間レビュー
    if country_score == 0:
        return "needs_review", [
            "target country/context not confirmed in title/abstract"]

    # 7) 格上げ
    positive = [
        f"organizational signals: {', '.join(org_hits)}",
        f"category matches ({cat_score}): {', '.join(cat_hits)}",
        f"country context: {', '.join(country_hits)}",
    ]
    if country_score >= 1 and org_ok and cat_score >= _STRONG_CAT_MIN:
        if has_pdf:
            return "strong_candidate", positive
        return "manual_download_needed", positive + ["no OA PDF; manual download"]

    if has_pdf:
        return "candidate", positive
    return "manual_download_needed", positive + ["no OA PDF; manual download"]


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
