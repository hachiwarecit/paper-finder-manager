"""harvest → 候補重複 → download-approved → import-metadata の検証。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paper_agent.downloader as downloader  # noqa: E402
from paper_agent.analysis import summarize  # noqa: E402
from paper_agent.db import PaperDB  # noqa: E402
from paper_agent.downloader import DownloadResult, download_approved  # noqa: E402
from paper_agent.harvester import harvest  # noqa: E402
from paper_agent.metadata_io import import_metadata  # noqa: E402
from paper_agent.models import CandidateStatus, PaperRecord, ScreeningStatus  # noqa: E402
from paper_agent.utils import normalize_title  # noqa: E402


def _db(tmp_path):
    return PaperDB(db_path=tmp_path / "papers.sqlite")


WORKS = [
    {"title": "Generational differences in Thai workplaces", "authors": "A B",
     "year": 2022, "doi": "10.1/new", "abstract": "Comparing Generation X and Y employees in Thai companies and organizations workplace.",
     "source_name": "OpenAlex", "source_url": "http://a", "pdf_url": "http://a.pdf",
     "open_access_flag": True, "document_type_guess": "journal_article"},
]


def test_harvest_saves_candidates(tmp_path):
    db = _db(tmp_path)
    cands = harvest("TH", "category_4", limit=10, db=db, raw_works=WORKS)
    assert len(cands) == 1
    saved = db.all_candidates()
    assert len(saved) == 1
    assert saved[0].candidate_status == CandidateStatus.pending
    assert saved[0].target_country == "Thailand"
    db.close()


def test_harvest_detects_duplicate_against_existing_db(tmp_path):
    db = _db(tmp_path)
    title = "Generational differences in Thai workplaces"
    db.upsert(PaperRecord(paper_id="TH-existing", title=title,
                          normalized_title=normalize_title(title), target_country="Thailand"))
    cands = harvest("TH", "category_4", limit=10, db=db, raw_works=WORKS)
    assert cands[0].candidate_status == CandidateStatus.duplicate
    assert cands[0].duplicate_of == "TH-existing"
    db.close()


def test_download_only_processes_approved(tmp_path, monkeypatch):
    db = _db(tmp_path)
    harvest("TH", "category_4", limit=10, db=db, raw_works=WORKS)
    cand = db.all_candidates()[0]
    # 1件目: approved, 2件目: pending
    cand.candidate_status = CandidateStatus.approved_for_download
    cand.legality_note = "open access"
    db.upsert_candidate(cand)

    # pending の候補も追加
    work2 = dict(WORKS[0])
    work2["doi"] = "10.9/pending"
    work2["title"] = "A pending candidate in Vietnam workplace"
    harvest("VN", "category_4", limit=10, db=db, raw_works=[work2])

    attempted = []

    def fake_dl(pdf_url, candidate_id, country):
        attempted.append(candidate_id)
        return DownloadResult(ok=True, path="/tmp/x.pdf", legality_note="open access")

    monkeypatch.setattr(downloader, "_download_to_country_dir", fake_dl)
    stats = download_approved(db)

    # approved だけが download 対象
    assert stats["approved"] == 1
    assert stats["downloaded"] == 1
    assert attempted == [cand.candidate_id]
    # pending はダウンロードされていない
    pendings = db.candidates_by_status("pending")
    assert all(c.download_status == "" for c in pendings)
    db.close()


def test_pending_candidate_is_not_downloaded(tmp_path, monkeypatch):
    db = _db(tmp_path)
    harvest("TH", "category_4", limit=10, db=db, raw_works=WORKS)  # pending
    monkeypatch.setattr(downloader, "_download_to_country_dir",
                        lambda *a, **k: DownloadResult(ok=True, path="/tmp/x.pdf"))
    stats = download_approved(db)
    assert stats["approved"] == 0
    assert stats["downloaded"] == 0
    db.close()


def test_download_skips_unclear_legality(tmp_path, monkeypatch):
    db = _db(tmp_path)
    harvest("TH", "category_4", limit=10, db=db, raw_works=WORKS)
    cand = db.all_candidates()[0]
    cand.candidate_status = CandidateStatus.approved_for_download
    cand.legality_note = "unknown (manual check required)"  # 不明確
    db.upsert_candidate(cand)

    called = []
    monkeypatch.setattr(downloader, "_download_to_country_dir",
                        lambda *a, **k: called.append(1) or DownloadResult(ok=True))
    stats = download_approved(db)
    assert stats["skipped"] == 1
    assert stats["downloaded"] == 0
    assert not called  # 合法性が不明確なので取得を試みない
    db.close()


def test_download_success_does_not_count_as_n(tmp_path, monkeypatch):
    db = _db(tmp_path)
    harvest("TH", "category_4", limit=10, db=db, raw_works=WORKS)
    cand = db.all_candidates()[0]
    cand.candidate_status = CandidateStatus.approved_for_download
    cand.legality_note = "open access"
    db.upsert_candidate(cand)
    monkeypatch.setattr(downloader, "_download_to_country_dir",
                        lambda *a, **k: DownloadResult(ok=True, path="/tmp/x.pdf"))
    download_approved(db)
    # ダウンロード成功しても PaperRecord は無く、N は 0 のまま
    assert summarize(db.all()).analysis_n == 0
    assert db.count() == 0
    db.close()


def test_edit_candidates_sheet_via_import_metadata(tmp_path):
    import openpyxl

    db = _db(tmp_path)
    harvest("TH", "category_4", limit=10, db=db, raw_works=WORKS)
    cid = db.all_candidates()[0].candidate_id

    xlsx = tmp_path / "inv.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Candidates"
    ws.append(["candidate_id", "candidate_status", "notes", "pdf_url"])
    ws.append([cid, "approved_for_download", "", ""])  # notes/pdf_url は空欄=保持
    wb.save(xlsx)

    stats = import_metadata(xlsx, db=db)
    assert stats.candidates_updated == 1
    cand = db.get_candidate(cid)
    assert cand.candidate_status == CandidateStatus.approved_for_download
    # 空欄の pdf_url は元の値を保持
    assert cand.pdf_url == "http://a.pdf"
    db.close()
