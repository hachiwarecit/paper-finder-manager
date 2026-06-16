"""query_country と target_country の分離、auto-approve厳格化、download追跡の検証。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paper_agent.downloader as downloader  # noqa: E402
from paper_agent.agents import AutopilotConfig, QualityAssuranceAgent, SupervisorAgent  # noqa: E402
from paper_agent.agents.supervisor import SupervisorAgent as Sup  # noqa: E402
from paper_agent.analysis import is_analysis_n  # noqa: E402
from paper_agent.db import PaperDB  # noqa: E402
from paper_agent.downloader import DownloadResult  # noqa: E402
from paper_agent.harvester import make_candidate  # noqa: E402
from paper_agent.models import CandidateStatus, PaperRecord, ScreeningStatus  # noqa: E402
from paper_agent.utils import DATA_DIR, slugify  # noqa: E402


def _db(tmp_path):
    return PaperDB(db_path=tmp_path / "papers.sqlite")


def _work(title, abstract, **kw):
    base = dict(title=title, authors="A B", year=2022, doi=f"10.x/{abs(hash(title+abstract))%99999}",
                abstract=abstract, source_name="OpenAlex", source_url="http://x",
                pdf_url="http://x.pdf", open_access_flag=True, document_type_guess="journal_article")
    base.update(kw)
    return base


# --- 検索国ラベルだけでは target_country を決めない ---
def test_query_thailand_alone_does_not_set_target_country():
    # 検索は country='TH' だが、title/abstract に Thailand が無い
    w = _work("Generational differences in workplaces",
              "This study compares Generation X and Generation Y employees in companies.")
    c = make_candidate(w, "TH", "category_1")
    assert c.query_country == "TH"
    assert c.target_country == "unknown"
    assert c.target_country_source == "query_only"
    assert c.target_country_evidence == ""


def test_thailand_in_abstract_sets_target_country():
    w = _work("Generational differences in workplaces",
              "This study compares employees in Thailand companies and organizations.")
    c = make_candidate(w, "TH", "category_1")
    assert c.target_country == "Thailand"
    assert c.target_country_source == "abstract"
    assert c.target_country_evidence


def test_vietnam_in_title_sets_target_country():
    w = _work("Generational differences in Vietnam workplaces",
              "This study compares Generation X and Y employees.")
    c = make_candidate(w, "TH", "category_1")   # 検索国はTHでも、本文はVietnam
    assert c.target_country == "Vietnam"
    assert c.target_country_source == "title"


# --- auto-approve 厳格化: query_only / unknown / evidence空 は承認しない ---
def _sup():
    return Sup(AutopilotConfig(auto_approve_threshold=0.0))


def test_query_only_not_auto_approved(tmp_path):
    w = _work("Generational differences workplace",
              "Generation X and Generation Y employees in companies organizations workplace.")
    c = make_candidate(w, "TH", "category_2")
    assert c.target_country_source == "query_only"
    blockers = _sup()._auto_approve_blockers(c)
    assert any("target_country_source=query_only" in b for b in blockers)


def test_evidence_based_candidate_can_be_approved(tmp_path):
    db = _db(tmp_path)
    w = _work("Multigenerational workforce in Thailand",
              ("Thailand companies employ Baby Boomers Generation X Generation Y Generation Z "
               "employees workplace organizations technology adoption."))
    c = make_candidate(w, "TH", "category_5")
    c.legality_note = "open access"
    db.upsert_candidate(c)
    approved = _sup()._auto_approve([c.candidate_id], db)
    assert c.candidate_id in approved
    got = db.get_candidate(c.candidate_id)
    assert got.candidate_status == CandidateStatus.approved_for_download
    assert got.auto_approve_reason
    db.close()


def test_empty_evidence_blocks_approval():
    w = _work("Multigenerational workforce study",
              "Generation X Generation Y Generation Z employees workplace organizations.")
    c = make_candidate(w, "TH", "category_5")
    c.target_country = "Thailand"          # 国名はあるが
    c.target_country_source = "query_only"  # 証拠は query_only
    c.target_country_evidence = ""
    blockers = _sup()._auto_approve_blockers(c)
    assert "target_country_evidence_empty" in blockers


# --- non-dry-run: approved+legal PDF は download_attempted になり、status が空のまま放置されない ---
def _fake_download_factory():
    def fake(pdf_url, candidate_id, country):
        d = DATA_DIR / "02_downloaded" / (country or "TH").upper()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{slugify(candidate_id)}.txt"
        p.write_text(
            ("Multigenerational workforce in Thailand\n\nAbstract\nThailand companies employ "
             "Baby Boomers Generation X Generation Y Generation Z.\n\nIntroduction\nThailand "
             "organizations and companies employ a multigenerational workforce. Baby Boomers, "
             "Generation X, Generation Y and Generation Z employees compare technology adoption "
             "and digital transformation in Thai workplaces and firms across generations.\n\n"
             "Discussion\nGeneration Z adopts technology faster than Baby Boomers in Thai companies.\n\n"
             "Conclusion\nGenerational diversity shapes technology adoption in the Thailand workplace.\n") * 3,
            encoding="utf-8")
        return DownloadResult(ok=True, path=str(p), legality_note="open access")
    return fake


def test_nonsdryrun_approved_legal_is_attempted_and_status_not_empty(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(downloader, "_download_to_country_dir", _fake_download_factory())

    def fetch(query, limit):
        return [_work("Multigenerational workforce in Thailand " + query[:10],
                      ("Thailand companies employ Baby Boomers Generation X Generation Y "
                       "Generation Z employees workplace organizations technology adoption."))]

    cfg = AutopilotConfig(target_n=100, countries=["TH"], categories=[5],
                          per_query_limit=3, dry_run=False, auto_approve_threshold=0.3,
                          max_rounds=2)
    SupervisorAgent(cfg).run(db, fetch=fetch)
    cands = db.all_candidates()
    # download_status が空/none のまま放置されていない
    assert all(c.download_status for c in cands)
    # 少なくとも1件は試行 (attempted/success/downloaded系)
    assert any(c.download_status in ("success",) or c.candidate_status.value in ("downloaded", "screened")
               for c in cands)
    # ダウンロード→ingest で論文が作られている
    assert db.count() > 0
    db.close()


# --- download_attempted=0 のとき summary に理由内訳が出る ---
def test_summary_has_reasons_when_no_download_attempted(tmp_path):
    db = _db(tmp_path)

    def fetch_no_pdf(query, limit):
        return [{"title": f"No pdf {query[:10]}", "authors": "A", "year": 2021,
                 "doi": f"10.z/{abs(hash(query))%99999}",
                 "abstract": "Generation X Generation Y employees Thailand workplace.",
                 "source_name": "Crossref", "source_url": "http://y", "pdf_url": None,
                 "open_access_flag": False, "document_type_guess": "journal_article"}]

    cfg = AutopilotConfig(target_n=100, countries=["TH"], categories=[1],
                          per_query_limit=3, dry_run=False, max_rounds=3)
    res = SupervisorAgent(cfg).run(db, fetch=fetch_no_pdf)
    text = res.summary_path.read_text(encoding="utf-8")
    assert "download_attempted_count: 0" in text
    assert "download_attempted=0 の理由" in text
    db.close()


# --- QA: query_only / unknown source の accepted は needs_review に降格 ---
def test_qa_demotes_query_only_accepted(tmp_path):
    db = _db(tmp_path)
    db.upsert(PaperRecord(paper_id="Q1", screening_status=ScreeningStatus.accepted,
                          full_text_available=True, number_of_generations=2,
                          target_country="Thailand", target_country_source="query_only",
                          target_country_evidence="", workplace_fit=True, doi="10.q/1",
                          category="category_5"))
    db.upsert(PaperRecord(paper_id="U1", screening_status=ScreeningStatus.accepted,
                          full_text_available=True, number_of_generations=2,
                          target_country="Thailand", target_country_source="unknown",
                          target_country_evidence="", workplace_fit=True, doi="10.u/1",
                          category="category_5"))
    report = QualityAssuranceAgent().run(db, require_artifacts=False)
    assert db.get("Q1").screening_status == ScreeningStatus.needs_review
    assert db.get("U1").screening_status == ScreeningStatus.needs_review
    assert not is_analysis_n(db.get("Q1"))
    db.close()
