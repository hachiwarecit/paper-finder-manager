"""autopilot (SupervisorAgent) と QualityAssuranceAgent のテスト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_agent.agents import AutopilotConfig, QualityAssuranceAgent, SupervisorAgent  # noqa: E402
from paper_agent.analysis import is_analysis_n, summarize  # noqa: E402
from paper_agent.db import PaperDB  # noqa: E402
from paper_agent.models import DocumentType, PaperRecord, ScreeningStatus  # noqa: E402
from paper_agent.utils import normalize_title  # noqa: E402


def _db(tmp_path):
    return PaperDB(db_path=tmp_path / "papers.sqlite")


def _fetch_oa(query, limit):
    """OA・多世代・職場・タイの候補 (auto-approve 対象になりうる)。"""
    return [{
        "title": f"Multigenerational workforce {query[:18]}",
        "authors": "Alpha A", "year": 2022, "doi": f"10.x/{abs(hash(query))%100000}",
        "abstract": ("Generation X and Generation Y and Generation Z employees in Thailand "
                     "companies organizations workplace technology adoption."),
        "source_name": "OpenAlex", "source_url": "http://x", "pdf_url": "http://x.pdf",
        "open_access_flag": True, "document_type_guess": "journal_article",
    }]


def _fetch_no_pdf(query, limit):
    return [{
        "title": f"No pdf {query[:18]}", "authors": "Beta B", "year": 2021,
        "doi": f"10.z/{abs(hash(query))%100000}",
        "abstract": "Generation X and Generation Y employees Vietnam workplace organizations.",
        "source_name": "Crossref", "source_url": "http://y", "pdf_url": None,
        "open_access_flag": False, "document_type_guess": "journal_article",
    }]


def _qualifying(pid, **kw):
    base = dict(screening_status=ScreeningStatus.accepted, full_text_available=True,
                number_of_generations=2, target_country="Thailand",
                target_country_source="abstract", target_country_evidence="'thailand' in abstract",
                workplace_fit=True,
                doi=f"10.acc/{pid}", category="category_5", document_type=DocumentType.journal_article,
                title=f"Title {pid}", normalized_title=normalize_title(f"Title {pid}"))
    base.update(kw)
    return PaperRecord(paper_id=pid, **base)


# --- dry-run は PDF を取得しない / 論文を作らない ---
def test_dryrun_does_not_download_or_create_papers(tmp_path):
    db = _db(tmp_path)
    cfg = AutopilotConfig(target_n=10, countries=["TH"], categories=[1],
                          per_query_limit=5, dry_run=True)
    res = SupervisorAgent(cfg).run(db, fetch=_fetch_oa)
    # 候補は保存されるが、論文は作られない / ダウンロードしていない
    assert db.candidate_count() > 0
    assert db.count() == 0
    assert all(c.download_status == "not_attempted" for c in db.all_candidates())
    assert res.final_n == 0
    db.close()


# --- harvest 件数は N に入らない ---
def test_harvest_count_not_in_n(tmp_path):
    db = _db(tmp_path)
    cfg = AutopilotConfig(target_n=10, countries=["TH"], categories=[1, 2],
                          per_query_limit=5, dry_run=True)
    SupervisorAgent(cfg).run(db, fetch=_fetch_oa)
    assert db.candidate_count() > 0
    assert summarize(db.all()).analysis_n == 0
    db.close()


# --- search_history に記録され、再実行で同一クエリを繰り返さない ---
def test_search_history_recorded_and_no_repeat(tmp_path):
    db = _db(tmp_path)
    cfg = AutopilotConfig(target_n=10, countries=["TH"], categories=[1],
                          per_query_limit=5, dry_run=True)
    SupervisorAgent(cfg).run(db, fetch=_fetch_oa)
    n_first = len(db.all_searches())
    assert n_first > 0
    # 2回目: 全クエリ実行済みなのですぐ停止、search_history は増えない
    res2 = SupervisorAgent(cfg).run(db, fetch=_fetch_oa)
    assert len(db.all_searches()) == n_first
    assert "尽くした" in res2.stop_reason
    db.close()


# --- 出力ファイルが生成される ---
def test_outputs_written(tmp_path):
    db = _db(tmp_path)
    cfg = AutopilotConfig(target_n=5, countries=["TH"], categories=[1],
                          per_query_limit=5, dry_run=True)
    res = SupervisorAgent(cfg).run(db, fetch=_fetch_oa)
    assert res.summary_path.exists()
    assert res.qa_report_path.exists()
    assert "autopilot summary" in res.summary_path.read_text(encoding="utf-8")
    assert "QA report" in res.qa_report_path.read_text(encoding="utf-8")
    db.close()


# --- target_N に到達したら停止 ---
def test_stops_when_target_reached(tmp_path):
    db = _db(tmp_path)
    db.upsert(_qualifying("TH-1"))
    db.upsert(_qualifying("TH-2"))
    cfg = AutopilotConfig(target_n=2, countries=["TH"], categories=[1],
                          per_query_limit=5, dry_run=True)
    res = SupervisorAgent(cfg).run(db, fetch=lambda q, l: [])
    assert "target_N 到達" in res.stop_reason
    assert res.final_n == 2
    db.close()


# --- 3ラウンド連続で新規acceptedが0なら停止 (非dry-run, pdfなし=採用に至らない) ---
def test_stops_after_three_zero_rounds(tmp_path):
    db = _db(tmp_path)
    cfg = AutopilotConfig(target_n=100, countries=["TH"], categories=[1],
                          per_query_limit=5, dry_run=False)
    res = SupervisorAgent(cfg).run(db, fetch=_fetch_no_pdf)
    assert "3ラウンド連続で新規acceptedが0" in res.stop_reason
    assert res.final_n == 0
    db.close()


# --- QA が不適合 accepted を検出して N から除外 ---
def test_qa_excludes_noncompliant_accepted(tmp_path):
    db = _db(tmp_path)
    db.upsert(_qualifying("OK"))                                  # 正当な N
    db.upsert(_qualifying("BAD_GEN", number_of_generations=1))   # 単一世代なのに accepted
    db.upsert(_qualifying("BAD_DUP", duplicate_of="OK"))         # duplicate_of あり
    db.upsert(_qualifying("BAD_TC", document_type=DocumentType.teaching_case))
    assert summarize(db.all()).analysis_n == 1  # is_analysis_n は元々除外する

    report = QualityAssuranceAgent().run(db, require_artifacts=False)
    demoted_ids = {pid for pid, _ in report.demoted}
    assert {"BAD_GEN", "BAD_DUP", "BAD_TC"} <= demoted_ids
    # 降格後、accepted は OK の1件、N も1件
    assert db.get("BAD_GEN").screening_status == ScreeningStatus.needs_review
    assert summarize(db.all()).analysis_n == 1
    assert is_analysis_n(db.get("OK"))
    db.close()


# --- QA: 同じDOIが複数acceptedなら片方を降格 ---
def test_qa_demotes_duplicate_doi_among_accepted(tmp_path):
    db = _db(tmp_path)
    db.upsert(_qualifying("A", doi="10.same/x"))
    db.upsert(_qualifying("B", doi="10.same/x"))
    report = QualityAssuranceAgent().run(db, require_artifacts=False)
    accepted = [r for r in db.all() if r.screening_status == ScreeningStatus.accepted]
    assert len(accepted) == 1  # 片方が降格
    assert any(c.num == 18 and not c.passed for c in report.checks)
    db.close()
