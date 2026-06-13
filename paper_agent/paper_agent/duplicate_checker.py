"""3段階の重複判定。

Stage 1: 完全一致 (exact_duplicate)
    - DOI 一致
    - PDF の SHA256 一致
    - 正規化タイトル完全一致
    - 抽出本文ハッシュ一致

Stage 2: 高類似 (probable_duplicate)
    - タイトル類似度・著者重なり・年近接・抄録類似・標本数・対象国・
      組織/業界・世代区分・研究手法をスコア化
    - タイトル類似が高く、著者・標本数・対象国が一致 -> probable_duplicate

Stage 3: 同一データ/改題版 (same_dataset_possible)
    - タイトルは異なるが、著者・対象国・業界・標本数・調査対象・世代区分が重なる
    - 学位論文⇔雑誌論文、会議版⇔ジャーナル版 の関係
    - 自動除外せず needs_review にする

公開関数:
    compare(candidate, existing)           -> DuplicateResult
    find_duplicates(candidate, records)     -> list[DuplicateResult] (重複のみ)
    best_match(candidate, records)          -> DuplicateResult
"""
from __future__ import annotations

from typing import Iterable

from rapidfuzz import fuzz

from .models import DuplicateResult, DuplicateType, PaperRecord
from .utils import author_overlap, normalize_title


# しきい値
TITLE_EXACT = 100
TITLE_PROBABLE = 88        # これ以上で「タイトルが似ている」
TITLE_SAME_DATASET = 55    # これ以上 88 未満で「やや似ている」
ABSTRACT_SIM_HIGH = 85


def _title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    # token_sort_ratio で語順の違いを吸収
    return float(fuzz.token_sort_ratio(na, nb))


def _abstract_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(a[:2000], b[:2000]))


def _gen_groups_overlap(a: str, b: str) -> bool:
    sa = {x.strip().lower() for x in (a or "").split(";") if x.strip()}
    sb = {x.strip().lower() for x in (b or "").split(";") if x.strip()}
    if not sa or not sb:
        return False
    return len(sa & sb) >= 1


def _same(a, b) -> bool:
    """None/unknown を除いた厳密一致。"""
    if a in (None, "", "unknown") or b in (None, "", "unknown"):
        return False
    return str(a).strip().lower() == str(b).strip().lower()


def compare(cand: PaperRecord, other: PaperRecord) -> DuplicateResult:
    """候補 cand と既存 other を比較し DuplicateResult を返す。"""
    matched: list[str] = []

    # ----- Stage 1: 完全一致 -----
    if _same(cand.doi, other.doi):
        matched.append("doi")
    if cand.pdf_sha256 and other.pdf_sha256 and cand.pdf_sha256 == other.pdf_sha256:
        matched.append("pdf_sha256")
    if cand.text_sha256 and other.text_sha256 and cand.text_sha256 == other.text_sha256:
        matched.append("text_sha256")

    nt_cand = cand.normalized_title or normalize_title(cand.title)
    nt_other = other.normalized_title or normalize_title(other.title)
    title_exact = bool(nt_cand) and nt_cand == nt_other
    if title_exact:
        matched.append("normalized_title")

    if matched:
        return DuplicateResult(
            candidate_paper_id=cand.paper_id,
            matched_paper_id=other.paper_id,
            duplicate_type=DuplicateType.exact_duplicate,
            confidence=1.0,
            matched_fields=matched,
            explanation=f"完全一致フィールド: {', '.join(matched)}",
            recommended_action="duplicate としてフラグ。本文は最も完全な版を1つ残す。",
        )

    # ----- スコア計算 -----
    title_sim = _title_similarity(cand.title, other.title)
    auth_ov = author_overlap(cand.authors, other.authors)
    abs_sim = _abstract_similarity(cand.main_topic, other.main_topic)
    same_sample = _same(cand.sample_size, other.sample_size) and cand.sample_size not in (None, 0)
    same_country = _same(cand.target_country, other.target_country)
    same_org = _same(cand.organization_context, other.organization_context)
    same_method = _same(cand.research_method, other.research_method)
    gen_overlap = _gen_groups_overlap(cand.generation_groups, other.generation_groups)
    year_close = (
        cand.year is not None
        and other.year is not None
        and abs(cand.year - other.year) <= 2
    )

    # ----- Stage 2: 高類似 (probable_duplicate) -----
    # タイトルがかなり似ていて、著者か標本数か対象国のいずれかが一致
    if title_sim >= TITLE_PROBABLE and (auth_ov >= 0.5 or same_sample or same_country):
        fields = ["title_similarity"]
        if auth_ov >= 0.5:
            fields.append("authors")
        if same_sample:
            fields.append("sample_size")
        if same_country:
            fields.append("target_country")
        if year_close:
            fields.append("year")
        conf = min(0.99, 0.6 + title_sim / 100 * 0.3 + auth_ov * 0.1)
        return DuplicateResult(
            candidate_paper_id=cand.paper_id,
            matched_paper_id=other.paper_id,
            duplicate_type=DuplicateType.probable_duplicate,
            confidence=round(conf, 3),
            matched_fields=fields,
            explanation=(
                f"タイトル類似度={title_sim:.0f}, 著者重なり={auth_ov:.2f}; "
                f"標本数一致={same_sample}, 対象国一致={same_country}"
            ),
            recommended_action="probable_duplicate。人間が会議版/雑誌版などを確認し採否を決める。",
        )

    # ----- Stage 3: 同一データ/改題版 (same_dataset_possible) -----
    # タイトルが完全同一ではないが、研究の中身が重なる
    same_signals = [auth_ov >= 0.5, same_country, same_org, same_sample, same_method, gen_overlap]
    same_score = sum(1 for s in same_signals if s)
    abstract_high = abs_sim >= ABSTRACT_SIM_HIGH

    if auth_ov >= 0.5 and same_score >= 3 and title_sim < TITLE_PROBABLE:
        fields = []
        if auth_ov >= 0.5:
            fields.append("authors")
        if same_country:
            fields.append("target_country")
        if same_org:
            fields.append("organization")
        if same_sample:
            fields.append("sample_size")
        if same_method:
            fields.append("research_method")
        if gen_overlap:
            fields.append("generation_groups")
        conf = min(0.95, 0.4 + same_score * 0.1 + (0.1 if abstract_high else 0))
        return DuplicateResult(
            candidate_paper_id=cand.paper_id,
            matched_paper_id=other.paper_id,
            duplicate_type=DuplicateType.same_dataset_possible,
            confidence=round(conf, 3),
            matched_fields=fields,
            explanation=(
                "タイトルは異なるが、著者・対象国・標本数・世代区分などが重なる。"
                "学位論文⇔雑誌版や会議版⇔ジャーナル版の可能性。"
            ),
            recommended_action="自動除外しない。needs_review として人間が確認する。",
        )

    # ----- 重複なし -----
    return DuplicateResult(
        candidate_paper_id=cand.paper_id,
        matched_paper_id=other.paper_id,
        duplicate_type=DuplicateType.not_duplicate,
        confidence=round(max(title_sim, abs_sim) / 100, 3),
        matched_fields=[],
        explanation=f"明確な重複なし (title_sim={title_sim:.0f}).",
        recommended_action="重複として扱わない。",
    )


def find_duplicates(cand: PaperRecord, records: Iterable[PaperRecord]) -> list[DuplicateResult]:
    """既存レコード群との比較で、重複/同一データ候補のみを返す。"""
    results: list[DuplicateResult] = []
    for other in records:
        if other.paper_id == cand.paper_id:
            continue
        res = compare(cand, other)
        if res.duplicate_type != DuplicateType.not_duplicate:
            results.append(res)
    # 強い重複を優先
    order = {
        DuplicateType.exact_duplicate: 0,
        DuplicateType.probable_duplicate: 1,
        DuplicateType.same_dataset_possible: 2,
    }
    results.sort(key=lambda r: (order.get(r.duplicate_type, 9), -r.confidence))
    return results


def best_match(cand: PaperRecord, records: Iterable[PaperRecord]) -> DuplicateResult:
    """最も強い重複判定を1つ返す。なければ not_duplicate。"""
    dups = find_duplicates(cand, records)
    if dups:
        return dups[0]
    return DuplicateResult(
        candidate_paper_id=cand.paper_id,
        duplicate_type=DuplicateType.not_duplicate,
        confidence=0.0,
        explanation="既存レコードに重複なし。",
        recommended_action="新規として扱う。",
    )
