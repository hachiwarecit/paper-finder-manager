"""analysis_N の厳密な集計。

N に数えてよいのは、最終的に PaperRecord として以下をすべて満たす論文だけ。

    screening_status == accepted
    duplicate_of is empty
    same_dataset_warning == false
    full_text_available == true
    number_of_generations >= 2
    target_country is Thailand or Vietnam
    workplace_fit == true

検索 (harvest) で見つけた数や、PDF を保存できた数は **絶対に N に数えない**。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import DocumentType, PaperRecord, ScreeningStatus

_VALID_COUNTRIES = {"thailand", "vietnam"}
_EXCLUDED_DOC_TYPES = {DocumentType.teaching_case, DocumentType.conference_abstract}
# 対象国の証拠として N に使える出所 (query_only / unknown は不可)
_VALID_COUNTRY_SOURCES = {"title", "abstract", "full_text", "metadata"}


def is_analysis_n(rec: PaperRecord) -> bool:
    """この論文を analysis_N に数えてよいか (厳密判定)。

    AnalysisNAgent の唯一の判定基準。harvest/download/candidate 件数は数えない。
    対象国は検索国ラベルではなく、文書の証拠 (title/abstract/full_text/metadata) で
    確認できたものだけを認める。query_only / unknown は N に入れない。
    """
    dt = rec.document_type
    # 多国間研究は、国別データが分離可能 or 国別分析がある場合のみ N に入れてよい
    multi_ok = (not rec.is_multi_country_study) or \
        rec.country_data_separable or rec.country_specific_analysis_available
    return (
        rec.screening_status == ScreeningStatus.accepted
        and not (rec.duplicate_of or "").strip()
        and rec.same_dataset_warning is False
        and rec.full_text_available is True
        and (rec.number_of_generations or 0) >= 2
        and (rec.target_country or "").strip().lower() in _VALID_COUNTRIES
        and (rec.target_country_source or "") in _VALID_COUNTRY_SOURCES
        and bool((rec.target_country_evidence or "").strip())
        and rec.workplace_fit is True
        and dt not in _EXCLUDED_DOC_TYPES
        and multi_ok
    )


def _country_label(rec: PaperRecord) -> str:
    tc = (rec.target_country or "").strip().lower()
    if tc == "thailand":
        return "Thailand"
    if tc == "vietnam":
        return "Vietnam"
    return "Other"


@dataclass
class AnalysisNSummary:
    analysis_n: int = 0
    th_n: int = 0
    vn_n: int = 0
    country_category: dict[tuple[str, str], int] = field(default_factory=dict)
    n_records: list[PaperRecord] = field(default_factory=list)
    # 参考: N に入らなかった内訳
    excluded_duplicates: int = 0          # screening_status == duplicate
    same_dataset_possible: int = 0        # same_dataset_warning == True
    supplementary: int = 0
    rejected: int = 0
    needs_review: int = 0
    accepted_but_excluded: int = 0        # accepted だが N 条件を満たさない


def summarize(records: list[PaperRecord]) -> AnalysisNSummary:
    s = AnalysisNSummary()
    for r in records:
        if is_analysis_n(r):
            s.analysis_n += 1
            s.n_records.append(r)
            label = _country_label(r)
            if label == "Thailand":
                s.th_n += 1
            elif label == "Vietnam":
                s.vn_n += 1
            key = (label, r.category or "unknown")
            s.country_category[key] = s.country_category.get(key, 0) + 1
            continue

        # N に入らないものの内訳カウント
        status = r.screening_status
        if status == ScreeningStatus.duplicate:
            s.excluded_duplicates += 1
        elif r.same_dataset_warning:
            s.same_dataset_possible += 1
        elif status == ScreeningStatus.supplementary:
            s.supplementary += 1
        elif status == ScreeningStatus.rejected:
            s.rejected += 1
        elif status == ScreeningStatus.needs_review:
            s.needs_review += 1
        elif status == ScreeningStatus.accepted:
            # accepted だが full_text/generations/country/workplace のいずれか不足
            s.accepted_but_excluded += 1
    return s


def format_summary(records: list[PaperRecord]) -> str:
    """コンソール/Markdown 共通の Analysis N Summary テキストを返す。"""
    s = summarize(records)
    lines: list[str] = []
    lines.append("Analysis N Summary")
    lines.append("=" * 40)
    lines.append(f"Analysis N (total)        : {s.analysis_n}")
    lines.append(f"TH total accepted N       : {s.th_n}")
    lines.append(f"VN total accepted N       : {s.vn_n}")
    lines.append("")
    lines.append("Country x Category N:")
    if s.country_category:
        for (country, cat), n in sorted(s.country_category.items()):
            lines.append(f"  {country:8} x {cat:12} : {n}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Excluded duplicates       : {s.excluded_duplicates}")
    lines.append(f"Same dataset possible     : {s.same_dataset_possible}")
    lines.append(f"Supplementary count       : {s.supplementary}")
    lines.append(f"Rejected count            : {s.rejected}")
    lines.append(f"Needs review count        : {s.needs_review}")
    if s.accepted_but_excluded:
        lines.append(f"Accepted but N条件未達     : {s.accepted_but_excluded}")
    return "\n".join(lines)
