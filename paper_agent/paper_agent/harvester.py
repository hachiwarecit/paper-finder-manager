"""候補収集 (harvest)。

検索 (OpenAlex + Crossref) → 候補メタデータ作成 → スコアリング →
候補段階の重複チェック → candidates テーブルへ保存。

重要な方針:
    - harvest では PDF を保存しない。候補メタデータだけを保存する。
    - candidate_score が高くても自動採用しない (人間確認の優先順位付けのみ)。
    - same_dataset_possible は自動除外せず needs_review にする。
    - harvest 件数は N ではない。

オフライン/ネットワーク不通でも例外で止めない。テストのため raw_works を注入できる。
"""
from __future__ import annotations

import hashlib
from typing import Optional

from . import search_crossref, search_openalex
from .db import PaperDB
from .duplicate_checker import compare
from .models import (
    Candidate,
    CandidateDuplicateStatus,
    CandidateStatus,
    DuplicateType,
    PaperRecord,
)
from .screener import _categories, _count_hits, _rules
from .utils import get_logger, normalize_title, now_iso

logger = get_logger()

COUNTRY_NAME = {"TH": "Thailand", "VN": "Vietnam", "JP": "Japan"}


# ---------------------------------------------------------------------------
# クエリ生成
# ---------------------------------------------------------------------------
def build_query(country: str, category: Optional[str]) -> str:
    name = COUNTRY_NAME.get((country or "").upper(), country)
    base = f"{name} generational differences workplace employees"
    if category:
        cats = _categories().get("categories", {})
        cat = cats.get(category, {})
        kws = cat.get("keywords", [])[:3]
        if kws:
            base += " " + " ".join(kws)
    return base


# ---------------------------------------------------------------------------
# キーワード抽出
# ---------------------------------------------------------------------------
def _matched(text_low: str, keywords) -> list[str]:
    return [kw for kw in (keywords or []) if kw and kw.lower() in text_low]


def _generation_terms(text_low: str) -> list[str]:
    rules = _rules()
    found: list[str] = []
    for _group, kws in rules.get("generations", {}).items():
        found += _matched(text_low, kws)
    found += _matched(text_low, rules.get("multigenerational_markers", []))
    # 重複除去 (順序維持)
    seen: set[str] = set()
    out = []
    for t in found:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _workplace_terms(text_low: str) -> list[str]:
    return _matched(text_low, _rules().get("workplace", {}).get("keywords", []))


def _category_terms(text_low: str, category: Optional[str]) -> tuple[str, list[str]]:
    """(best_category, matched_terms)。category 指定時はそのカテゴリ語を優先。"""
    cats = _categories().get("categories", {})
    scores = {cid: _matched(text_low, c.get("keywords", [])) for cid, c in cats.items()}
    if category and category in scores and scores[category]:
        return category, scores[category]
    # 指定が無い/当たらない場合は最も語数の多いカテゴリ
    best = max(scores.items(), key=lambda kv: len(kv[1]), default=(None, []))
    if best[0] and best[1]:
        return best[0], best[1]
    return category or "unknown", []


def _detect_target_country(text_low: str, country_label: str) -> str:
    rules = _rules().get("country", {})
    th = _count_hits(text_low, rules.get("thailand", {}).get("keywords", []))
    vn = _count_hits(text_low, rules.get("vietnam", {}).get("keywords", []))
    if th or vn:
        return "Thailand" if th >= vn else "Vietnam"
    label = (country_label or "").upper()
    if label in ("TH", "VN"):
        return COUNTRY_NAME[label]
    return "unknown"


# ---------------------------------------------------------------------------
# 候補生成 + スコアリング
# ---------------------------------------------------------------------------
def _candidate_id(work: dict, country: str) -> str:
    key = (work.get("doi") or normalize_title(work.get("title") or "")).strip().lower()
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10] if key else "unknown"
    return f"CAND-{(country or 'XX').upper()}-{h}"


def score_candidate(cand: Candidate) -> float:
    """0..1 のスコア。人間確認の優先順位付け用 (自動採用には使わない)。"""
    score = 0.0
    if cand.target_country in ("Thailand", "Vietnam"):
        score += 0.20
    if cand.workplace_keywords:
        score += 0.15
    gen_terms = [t for t in cand.generation_keywords.split("; ") if t]
    if len(gen_terms) >= 2:
        score += 0.20
    elif len(gen_terms) == 1:
        score += 0.08
    if cand.category_keywords:
        score += 0.15
    if cand.abstract and len(cand.abstract) > 200:
        score += 0.15  # 本文が取得できそう (抄録が実体としてある)
    if cand.open_access_flag or cand.pdf_url:
        score += 0.10
    if cand.duplicate_status == CandidateDuplicateStatus.new_candidate:
        score += 0.05
    return round(min(score, 1.0), 3)


def make_candidate(work: dict, country: str, category: Optional[str]) -> Candidate:
    """正規化 work 辞書から Candidate を作る (重複判定・スコアは別途)。"""
    title = work.get("title") or ""
    abstract = work.get("abstract") or ""
    text_low = f"{title}\n{abstract}".lower()

    gen_terms = _generation_terms(text_low)
    wp_terms = _workplace_terms(text_low)
    best_cat, cat_terms = _category_terms(text_low, category)
    target_country = _detect_target_country(text_low, country)

    oa = bool(work.get("open_access_flag"))
    pdf_url = work.get("pdf_url")
    legality = "open access" if (oa and pdf_url) else "unknown (manual check required)"

    cand = Candidate(
        candidate_id=_candidate_id(work, country),
        title=title,
        normalized_title=normalize_title(title),
        authors=work.get("authors") or "",
        year=work.get("year"),
        doi=(work.get("doi") or None),
        abstract=abstract,
        source_name=work.get("source_name") or "unknown",
        source_url=work.get("source_url"),
        pdf_url=pdf_url,
        country=(country or "unknown").upper() if country else "unknown",
        category=best_cat or (category or "unknown"),
        target_country=target_country,
        generation_keywords="; ".join(gen_terms),
        workplace_keywords="; ".join(wp_terms),
        category_keywords="; ".join(cat_terms),
        document_type_guess=work.get("document_type_guess") or "unknown",
        open_access_flag=oa,
        legality_note=legality,
        candidate_status=CandidateStatus.pending,
        duplicate_status=CandidateDuplicateStatus.new_candidate,
        notes=f"harvested {now_iso()} from {work.get('source_name', 'unknown')}",
    )
    cand.candidate_score = score_candidate(cand)
    return cand


# ---------------------------------------------------------------------------
# 候補段階の重複チェック
# ---------------------------------------------------------------------------
def _candidate_as_record(cand: Candidate) -> PaperRecord:
    """重複判定で既存 PaperRecord と比較するためのプロキシ。"""
    return PaperRecord(
        paper_id=cand.candidate_id,
        title=cand.title,
        normalized_title=cand.normalized_title,
        authors=cand.authors,
        year=cand.year,
        doi=cand.doi,
        target_country=cand.target_country,
        generation_groups=cand.generation_keywords,
        main_topic=cand.abstract,
    )


def candidate_dedupe(cand: Candidate, existing_papers: list[PaperRecord],
                     existing_candidates: list[Candidate]) -> Candidate:
    """候補を既存の論文・候補と比較し、duplicate_status/candidate_status を設定する。"""
    proxy = _candidate_as_record(cand)
    pool = list(existing_papers) + [
        _candidate_as_record(c) for c in existing_candidates
        if c.candidate_id != cand.candidate_id
    ]

    best = None
    order = {DuplicateType.exact_duplicate: 0, DuplicateType.probable_duplicate: 1,
             DuplicateType.same_dataset_possible: 2}
    for other in pool:
        res = compare(proxy, other)
        if res.duplicate_type == DuplicateType.not_duplicate:
            continue
        if best is None or order[res.duplicate_type] < order[best.duplicate_type]:
            best = res
        if best.duplicate_type == DuplicateType.exact_duplicate:
            break

    if best is None:
        cand.duplicate_status = CandidateDuplicateStatus.new_candidate
        cand.candidate_status = CandidateStatus.pending
        cand.duplicate_of = None
        cand.same_dataset_warning = False
    elif best.duplicate_type == DuplicateType.exact_duplicate:
        cand.duplicate_status = CandidateDuplicateStatus.exact_duplicate
        cand.candidate_status = CandidateStatus.duplicate
        cand.duplicate_of = best.matched_paper_id
    elif best.duplicate_type == DuplicateType.probable_duplicate:
        cand.duplicate_status = CandidateDuplicateStatus.probable_duplicate
        cand.candidate_status = CandidateStatus.needs_review
        cand.duplicate_of = best.matched_paper_id
    else:  # same_dataset_possible
        cand.duplicate_status = CandidateDuplicateStatus.same_dataset_possible
        cand.candidate_status = CandidateStatus.needs_review
        cand.same_dataset_warning = True
        cand.duplicate_of = best.matched_paper_id

    # 重複状態が変わったのでスコアを再計算
    cand.candidate_score = score_candidate(cand)
    return cand


# ---------------------------------------------------------------------------
# 検索集約
# ---------------------------------------------------------------------------
def _fetch_works(query: str, limit: int) -> list[dict]:
    """OpenAlex + Crossref を検索し、DOI/正規化タイトルで重複排除して返す。"""
    works: list[dict] = []
    works += search_openalex.search_normalized(query, limit=limit)
    works += search_crossref.search_normalized(query, limit=limit)

    seen: set[str] = set()
    deduped: list[dict] = []
    for w in works:
        key = (w.get("doi") or normalize_title(w.get("title") or "")).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(w)
    return deduped[:limit]


# ---------------------------------------------------------------------------
# harvest 本体
# ---------------------------------------------------------------------------
def harvest_query(query: str, country: str, category: Optional[str], limit: int,
                  db: PaperDB, fetch=None) -> list[Candidate]:
    """1つの検索クエリで候補を収集・保存する (autopilot 用)。

    fetch(query, limit) -> 正規化 work 辞書のリスト。None ならネットワーク検索。
    既存の論文・候補と照合して候補段階の重複判定を行う。
    """
    if fetch is not None:
        works = fetch(query, limit)
    else:
        works = _fetch_works(query, limit)

    existing_papers = db.all()
    saved: list[Candidate] = []
    for w in (works or [])[:limit]:
        try:
            cand = make_candidate(w, country, category)
            existing_candidates = db.all_candidates() + saved
            candidate_dedupe(cand, existing_papers, existing_candidates)
            db.upsert_candidate(cand)
            saved.append(cand)
        except Exception as exc:  # noqa: BLE001
            logger.error("候補処理失敗 (%s): %s", str(w.get("title", ""))[:50], exc)
    return saved


def harvest(country: str, category: Optional[str], limit: int = 100,
            db: PaperDB | None = None, raw_works: Optional[list[dict]] = None) -> list[Candidate]:
    """候補を収集して candidates テーブルへ保存する。保存した候補リストを返す。"""
    own = db is None
    db = db or PaperDB()
    try:
        if raw_works is None:
            query = build_query(country, category)
            raw_works = _fetch_works(query, limit)

        existing_papers = db.all()
        saved: list[Candidate] = []
        for w in raw_works[:limit]:
            try:
                cand = make_candidate(w, country, category)
                # 既存の論文 + これまでに保存した候補 + 今回分と照合
                existing_candidates = db.all_candidates() + saved
                candidate_dedupe(cand, existing_papers, existing_candidates)
                db.upsert_candidate(cand)
                saved.append(cand)
            except Exception as exc:  # noqa: BLE001
                logger.error("候補処理失敗 (%s): %s", w.get("title", "")[:50], exc)
        logger.info("harvest: %d 件の候補を保存 (country=%s, category=%s)",
                    len(saved), country, category)
        return saved
    finally:
        if own:
            db.close()
