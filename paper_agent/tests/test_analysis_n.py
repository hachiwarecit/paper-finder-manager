"""analysis_N の厳密集計テスト。

N に数えてよいのは accepted かつ重複なし・本文あり・2世代以上・対象国TH/VN・
workplace_fit のみ。harvest/download 件数は N ではない。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_agent.analysis import is_analysis_n, summarize  # noqa: E402
from paper_agent.models import PaperRecord, ScreeningStatus  # noqa: E402


def _ok(pid="N1", **kw):
    """N条件をすべて満たす accepted 論文。"""
    base = dict(
        screening_status=ScreeningStatus.accepted,
        duplicate_of=None,
        same_dataset_warning=False,
        full_text_available=True,
        number_of_generations=2,
        target_country="Thailand",
        workplace_fit=True,
        category="category_5",
    )
    base.update(kw)
    return PaperRecord(paper_id=pid, **base)


def test_accepted_clean_paper_counts():
    assert is_analysis_n(_ok()) is True


def test_duplicate_not_counted():
    assert is_analysis_n(_ok(screening_status=ScreeningStatus.duplicate, duplicate_of="X")) is False


def test_has_duplicate_of_not_counted():
    # accepted でも duplicate_of があれば N に入れない
    assert is_analysis_n(_ok(duplicate_of="X")) is False


def test_same_dataset_warning_not_counted():
    assert is_analysis_n(_ok(same_dataset_warning=True)) is False


def test_supplementary_not_counted():
    assert is_analysis_n(_ok(screening_status=ScreeningStatus.supplementary)) is False


def test_rejected_not_counted():
    assert is_analysis_n(_ok(screening_status=ScreeningStatus.rejected)) is False


def test_needs_review_not_counted():
    assert is_analysis_n(_ok(screening_status=ScreeningStatus.needs_review)) is False


def test_single_generation_not_counted():
    assert is_analysis_n(_ok(number_of_generations=1)) is False


def test_no_fulltext_not_counted():
    assert is_analysis_n(_ok(full_text_available=False)) is False


def test_wrong_country_not_counted():
    assert is_analysis_n(_ok(target_country="Japan")) is False


def test_no_workplace_fit_not_counted():
    assert is_analysis_n(_ok(workplace_fit=False)) is False


def test_summary_counts_only_qualifying():
    records = [
        _ok("A", target_country="Thailand", category="category_5"),
        _ok("B", target_country="Vietnam", category="category_6"),
        _ok("C", screening_status=ScreeningStatus.supplementary),   # not N
        _ok("D", same_dataset_warning=True),                        # not N
        _ok("E", screening_status=ScreeningStatus.duplicate, duplicate_of="A"),  # not N
        _ok("F", screening_status=ScreeningStatus.rejected),        # not N
        _ok("G", screening_status=ScreeningStatus.needs_review),    # not N
    ]
    s = summarize(records)
    assert s.analysis_n == 2
    assert s.th_n == 1
    assert s.vn_n == 1
    assert s.country_category[("Thailand", "category_5")] == 1
    assert s.country_category[("Vietnam", "category_6")] == 1
    assert s.supplementary == 1
    assert s.same_dataset_possible == 1
    assert s.excluded_duplicates == 1
    assert s.rejected == 1
    assert s.needs_review == 1
