"""download経路・settlement・QA FAIL のテスト (今回の重大バグの回帰防止)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paper_agent.downloader as downloader  # noqa: E402
from paper_agent.agents import AutopilotConfig, QualityAssuranceAgent, SupervisorAgent  # noqa: E402
from paper_agent.db import PaperDB  # noqa: E402
from paper_agent.downloader import DownloadResult, download_approved  # noqa: E402
from paper_agent.harvester import make_candidate  # noqa: E402
from paper_agent.models import Candidate, CandidateStatus, CandidateDuplicateStatus  # noqa: E402
from paper_agent.utils import DATA_DIR, slugify  # noqa: E402


def _db(tmp_path):
    return PaperDB(db_path=tmp_path / "papers.sqlite")


def _good_work(q, l):
    return [{"title": f"Multigenerational workforce in Thailand {q[:8]}", "authors": "A B",
             "year": 2022, "doi": f"10.x/{abs(hash(q))%99999}",
             "abstract": ("Thailand companies employ Baby Boomers Generation X Generation Y "
                          "Generation Z employees workplace organizations technology adoption."),
             "source_name": "OpenAlex", "source_url": "http://x", "pdf_url": "http://x.pdf",
             "open_access_flag": True, "document_type_guess": "journal_article"}]


def _fake_ok():
    def fake(pdf_url, candidate_id, country):
        d = DATA_DIR / "02_downloaded" / (country or "TH").upper()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{slugify(candidate_id)}.txt"
        p.write_text(("Multigenerational workforce in Thailand\n\nAbstract\nThailand companies "
                      "Baby Boomers Generation X Generation Y Generation Z.\n\nIntroduction\n"
                      "Thailand organizations employ a multigenerational workforce. Baby Boomers, "
                      "Generation X, Generation Y and Generation Z employees compare technology "
                      "adoption in Thai workplaces and firms.\n\nDiscussion\nGeneration Z adopts "
                      "technology faster than Baby Boomers in Thai companies.\n\nConclusion\n"
                      "Generational diversity shapes technology adoption in the Thailand workplace.\n") * 3,
                     encoding="utf-8")
        return DownloadResult(ok=True, path=str(p), legality_note="open access")
    return fake


# --- settlement: 検索枯渇後でも approved を必ず download する ---
def test_settlement_downloads_pending_after_exhausted_search(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_download_to_country_dir", _fake_ok())
    db = _db(tmp_path)
    cfg_dry = AutopilotConfig(target_n=10, countries=["TH"], categories=[1],
                              per_query_limit=3, dry_run=True, auto_approve_threshold=0.4)
    SupervisorAgent(cfg_dry).run(db, fetch=_good_work)
    assert len(db.candidates_by_status("approved_for_download")) >= 1  # dry-run が承認済み
    # 同一DBで non-dry-run → 検索枯渇でも settlement が download する
    cfg = AutopilotConfig(target_n=10, countries=["TH"], categories=[1],
                          per_query_limit=3, dry_run=False, auto_approve_threshold=0.4)
    res = SupervisorAgent(cfg).run(db, fetch=_good_work)
    assert len(res.qa_reports) >= 1                       # QAラウンド数 != 0
    cands = db.all_candidates()
    # approved が not_attempted のまま残らない
    assert all(c.download_status not in ("", "not_attempted")
               for c in db.candidates_by_status("approved_for_download"))
    assert any(c.download_status == "success" for c in cands)
    assert db.count() > 0
    db.close()


# --- fresh non-dry-run: approved+legal なら download_attempted_count > 0 ---
def test_fresh_nondryrun_attempts_download(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_download_to_country_dir", _fake_ok())
    db = _db(tmp_path)
    cfg = AutopilotConfig(target_n=100, countries=["TH"], categories=[5],
                          per_query_limit=3, dry_run=False, auto_approve_threshold=0.3, max_rounds=2)
    res = SupervisorAgent(cfg).run(db, fetch=_good_work)
    text = res.summary_path.read_text(encoding="utf-8")
    # download_attempted_count > 0
    cands = db.all_candidates()
    attempted = sum(1 for c in cands if c.download_status in ("attempted", "success", "failed"))
    assert attempted > 0
    assert len(res.qa_reports) >= 1
    db.close()


# --- download失敗時も failed と download_error が保存される ---
def test_download_failure_records_status_and_error(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_download_to_country_dir",
                        lambda *a, **k: DownloadResult(ok=False, error="boom 503"))
    db = _db(tmp_path)
    c = make_candidate(_good_work("q", 1)[0], "TH", "category_5")
    c.legality_note = "open access"
    c.candidate_status = CandidateStatus.approved_for_download
    db.upsert_candidate(c)
    download_approved(db)
    got = db.get_candidate(c.candidate_id)
    assert got.download_status == "failed"
    assert got.download_error == "boom 503"
    assert got.attempted_url == "http://x.pdf"
    assert got.download_timestamp  # タイムスタンプが残る
    db.close()


# --- QA: approved+legal PDF があるのに download未試行なら FAIL (check29) ---
def test_qa_fails_when_approved_but_no_download_attempt(tmp_path):
    db = _db(tmp_path)
    c = make_candidate(_good_work("q", 1)[0], "TH", "category_5")
    c.legality_note = "open access"
    c.candidate_status = CandidateStatus.approved_for_download
    c.download_status = "not_attempted"
    db.upsert_candidate(c)
    report = QualityAssuranceAgent().run(db, require_artifacts=True)
    check29 = [c for c in report.checks if c.num == 29][0]
    assert check29.passed is False  # FAIL を検出
    db.close()


# --- download対象なし: summary が approved_for_download=0 を明記 ---
def test_summary_states_no_download_target(tmp_path):
    db = _db(tmp_path)

    def fetch_no_pdf(q, l):
        return [{"title": f"No pdf {q[:8]}", "authors": "A", "year": 2021,
                 "doi": f"10.z/{abs(hash(q))%99999}",
                 "abstract": "Generation X Generation Y employees Thailand workplace.",
                 "source_name": "Crossref", "source_url": "http://y", "pdf_url": None,
                 "open_access_flag": False, "document_type_guess": "journal_article"}]

    cfg = AutopilotConfig(target_n=100, countries=["TH"], categories=[1],
                          per_query_limit=3, dry_run=False, max_rounds=3)
    res = SupervisorAgent(cfg).run(db, fetch=fetch_no_pdf)
    text = res.summary_path.read_text(encoding="utf-8")
    assert "approved_for_download が0だから試行対象なし" in text
    assert "papers / N が 0 の理由" in text
    db.close()


# --- QAラウンド数は non-dry-run で 0 にならない (検索枯渇でも) ---
def test_qa_rounds_never_zero_in_nondryrun(tmp_path):
    db = _db(tmp_path)
    # dry-run で全クエリを実行し検索空間を尽くす (dry-runには3連続0停止がない)
    SupervisorAgent(AutopilotConfig(target_n=5, countries=["TH"], categories=[1],
                                    per_query_limit=3, dry_run=True)).run(db, fetch=lambda q, l: [])
    # 同一DBで non-dry-run: 検索枯渇で rounds=0 でも final QA が必ず記録される
    res2 = SupervisorAgent(AutopilotConfig(target_n=5, countries=["TH"], categories=[1],
                                           per_query_limit=3, dry_run=False)).run(db, fetch=lambda q, l: [])
    assert "尽くした" in res2.stop_reason
    assert len(res2.qa_reports) >= 1
    db.close()
