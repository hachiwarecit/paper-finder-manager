"""重複判定のテスト。

仕様 §8 の既知重複ケースのうち、メタデータだけで再現できるものを使う。
PDF ファイルは無くても動く (タイトル/著者/標本数/世代区分で判定)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_agent.duplicate_checker import compare, best_match  # noqa: E402
from paper_agent.models import DuplicateType, PaperRecord  # noqa: E402
from paper_agent.utils import normalize_title  # noqa: E402


def mk(pid, title="", **kw):
    return PaperRecord(paper_id=pid, title=title, normalized_title=normalize_title(title), **kw)


# --- Stage 1: 完全一致 ---
def test_exact_duplicate_by_doi():
    a = mk("VN_6-2", "Leadership styles and generational effects", doi="10.1234/abc")
    b = mk("VN_2-4", "Totally different title here", doi="10.1234/abc")
    res = compare(a, b)
    assert res.duplicate_type == DuplicateType.exact_duplicate
    assert "doi" in res.matched_fields


def test_exact_duplicate_by_normalized_title():
    # §8 Vietnam #1 のタイトル違い表記 (コロン/ハイフン差)
    a = mk("VN_6-2", "Leadership styles and generational effects: examples of US companies in Vietnam")
    b = mk("VN_2-4", "Leadership styles and generational effects - examples of US companies in Vietnam")
    res = compare(a, b)
    assert res.duplicate_type == DuplicateType.exact_duplicate
    assert "normalized_title" in res.matched_fields


def test_exact_duplicate_by_pdf_hash():
    a = mk("A", "title one", pdf_sha256="deadbeef")
    b = mk("B", "title two", pdf_sha256="deadbeef")
    res = compare(a, b)
    assert res.duplicate_type == DuplicateType.exact_duplicate
    assert "pdf_sha256" in res.matched_fields


def test_exact_duplicate_by_text_hash():
    a = mk("A", "title one", text_sha256="cafef00d")
    b = mk("B", "title two", text_sha256="cafef00d")
    res = compare(a, b)
    assert res.duplicate_type == DuplicateType.exact_duplicate


# --- Stage 2: probable_duplicate ---
def test_probable_duplicate_high_title_sim_same_country():
    a = mk("TH_3-5", "A Study of Employers Satisfaction With Generation Z in Thai Workplaces",
           authors="Somchai P.", target_country="Thailand", sample_size=200)
    b = mk("TH_6-2", "A Study of Employers' Satisfaction with Gen Z in Thai Workplaces",
           authors="Somchai P.", target_country="Thailand", sample_size=200)
    res = compare(a, b)
    assert res.duplicate_type in (DuplicateType.probable_duplicate, DuplicateType.exact_duplicate)


# --- Stage 3: same_dataset_possible ---
def test_same_dataset_possible_different_titles_same_study():
    # 学位論文版とジャーナル版: タイトルは違うが著者・国・標本数・世代区分・手法が一致
    a = mk(
        "TH_thesis",
        "Multigenerational Issues among Employees in Thai SMEs: A Dissertation",
        authors="Nguyen T.; Tran K.",
        target_country="Thailand",
        organization_context="SME",
        sample_size=350,
        research_method="survey",
        generation_groups="gen_x; gen_y",
    )
    b = mk(
        "TH_journal",
        "An Analysis of Generation X and Y in Small Enterprises",
        authors="Nguyen T.; Tran K.",
        target_country="Thailand",
        organization_context="SME",
        sample_size=350,
        research_method="survey",
        generation_groups="gen_x; gen_y",
    )
    res = compare(a, b)
    assert res.duplicate_type == DuplicateType.same_dataset_possible
    assert "自動除外しない" in res.recommended_action


def test_not_duplicate():
    a = mk("A", "Work values in Thai hospitality industry", authors="Alpha A.",
           target_country="Thailand", sample_size=100)
    b = mk("B", "Resistance to change in Vietnam listed companies", authors="Beta B.",
           target_country="Vietnam", sample_size=900)
    res = compare(a, b)
    assert res.duplicate_type == DuplicateType.not_duplicate


def test_best_match_picks_strongest():
    cand = mk("C", "Leadership styles and generational effects in Vietnam", doi="10.1/x")
    pool = [
        mk("X", "Unrelated paper", target_country="Vietnam"),
        mk("Y", "Leadership styles and generational effects in Vietnam", doi="10.1/x"),
    ]
    res = best_match(cand, pool)
    assert res.duplicate_type == DuplicateType.exact_duplicate
    assert res.matched_paper_id == "Y"
