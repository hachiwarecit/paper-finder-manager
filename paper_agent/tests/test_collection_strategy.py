"""収集戦略強化のテスト: 多国間研究・手動キュー・feasibility・検索拡大・候補母数。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_agent.agents import AutopilotConfig, SupervisorAgent  # noqa: E402
from paper_agent.agents.candidate_screening import classify_manual  # noqa: E402
from paper_agent.agents.search_planner import SearchPlannerAgent  # noqa: E402
from paper_agent.db import PaperDB  # noqa: E402
from paper_agent.harvester import make_candidate  # noqa: E402
from paper_agent.models import ScreeningStatus  # noqa: E402
from paper_agent.screener import detect_multi_country, screen  # noqa: E402
from paper_agent import feasibility  # noqa: E402


def _db(tmp_path):
    return PaperDB(db_path=tmp_path / "papers.sqlite")


def _work(title, abstract, pdf_url="http://x.pdf", oa=True):
    return {"title": title, "authors": "A B", "year": 2022,
            "doi": f"10.x/{abs(hash(title+abstract))%99999}", "abstract": abstract,
            "source_name": "OpenAlex", "source_url": "http://x", "pdf_url": pdf_url,
            "open_access_flag": oa, "document_type_guess": "journal_article"}


# --- 多国間研究の検出と screen ---
def test_detect_multi_country_by_n_countries():
    is_multi, ev = detect_multi_country(
        title="COVID-19 and the ageing workforce across 15 countries")
    assert is_multi is True and "15 countries" in ev


def test_multi_country_study_not_auto_accepted():
    text = (
        "Abstract\nGlobal perspectives across 15 countries including Thailand on the ageing workforce.\n\n"
        "Introduction\nThailand and many countries employ Baby Boomers Generation X Generation Y "
        "employees in workplaces and organizations. Work values and technology adoption across "
        "generations are compared across 15 countries including Thailand.\n\n"
        "Discussion\nGenerations differ across companies.\n\nConclusion\nGenerational diversity matters.\n"
    ) * 3
    res = screen(_paper(), text)
    assert res.is_multi_country_study is True
    assert res.decision == ScreeningStatus.needs_review   # 自動 accepted にしない


def _paper():
    from paper_agent.models import PaperRecord
    return PaperRecord(paper_id="P1", country="TH")


# --- 候補は pdf_url が無くても捨てない (母数) ---
def test_candidate_kept_without_pdf_url():
    c = make_candidate(_work("Multigenerational workforce in Thailand", "Thailand companies "
                             "Baby Boomers Generation X Generation Y Generation Z employees workplace.",
                             pdf_url=None, oa=False), "TH", "category_5")
    assert c.candidate_id  # 候補として作られる
    assert c.pdf_url is None
    assert c.manual_pdf_search_url  # 手動検索URL (DOI等) が入る


# --- 手動キュー分類: 有望だが自動取得できない候補 ---
def test_classify_manual_high_priority():
    c = make_candidate(_work("Multigenerational workforce in Thailand",
                             "Thailand companies Baby Boomers Generation X Generation Y Generation Z "
                             "employees workplace organizations work values technology adoption.",
                             pdf_url=None, oa=False), "TH", "category_5")
    status, prio = classify_manual(c)
    assert status == "manual_pdf_required"
    assert prio == "high"


def test_classify_manual_skips_query_only():
    c = make_candidate(_work("Generational study", "Generation X Generation Y employees workplace "
                             "(no country).", pdf_url=None, oa=False), "TH", "category_5")
    # 国の証拠が無い (query_only) → 手動キューに入れない
    status, prio = classify_manual(c)
    assert status is None


# --- 検索クエリが国×カテゴリごとに20件以上 ---
def test_search_planner_min_20_per_cell():
    plan = SearchPlannerAgent(["TH", "VN"], [1, 2, 3, 4, 5, 6]).plan()
    from collections import Counter
    cells = Counter((p["country"], p["category"]) for p in plan)
    assert all(n >= 20 for n in cells.values())
    assert len(cells) == 12   # 2国 × 6カテゴリ


# --- feasibility: 手動候補を含めた到達見込み ---
def test_feasibility_counts_manual_candidates(tmp_path):
    db = _db(tmp_path)
    # 手動 high/medium 候補を作る
    for i in range(3):
        c = make_candidate(_work(f"Multigenerational workforce in Thailand {i}",
                                 "Thailand companies Baby Boomers Generation X Generation Y Generation Z "
                                 "employees workplace organizations work values technology adoption.",
                                 pdf_url=None, oa=False), "TH", "category_5")
        from paper_agent.models import CandidateStatus
        c.candidate_status = CandidateStatus.manual_pdf_required
        c.manual_priority = "high"
        db.upsert_candidate(c)
    f = feasibility.compute(db, target_n=100)
    assert f["high_priority_count"] == 3
    assert f["manual_included_potential"] >= 3
    db.close()


# --- autopilot が新しい出力ファイルを生成する ---
def test_autopilot_emits_strategy_outputs(tmp_path):
    db = _db(tmp_path)

    def fetch(q, l):
        return [_work("Multigenerational workforce in Thailand " + q[:6],
                      "Thailand companies Baby Boomers Generation X Generation Y Generation Z "
                      "employees workplace organizations technology adoption.", pdf_url=None, oa=False)]

    cfg = AutopilotConfig(target_n=100, countries=["TH"], categories=[5],
                          per_query_limit=3, dry_run=True)
    SupervisorAgent(cfg).run(db, fetch=fetch)
    from paper_agent.utils import DATA_DIR
    exports = DATA_DIR / "10_exports"
    for name in ("failure_analysis.md", "feasibility.md", "manual_pdf_queue.csv",
                 "high_priority_candidates.csv"):
        assert (exports / name).is_file(), f"{name} が出力されていない"
    db.close()
