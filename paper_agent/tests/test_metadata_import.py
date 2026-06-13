"""メタデータ取り込み (import-metadata) のテスト。"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_agent.db import PaperDB  # noqa: E402
from paper_agent.duplicate_checker import compare  # noqa: E402
from paper_agent.metadata_io import import_metadata  # noqa: E402
from paper_agent.models import DuplicateType, PaperRecord  # noqa: E402
from paper_agent.utils import normalize_title  # noqa: E402


def _db(tmp_path) -> PaperDB:
    return PaperDB(db_path=tmp_path / "papers.sqlite")


def _write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_import_from_csv_fills_fields(tmp_path):
    db = _db(tmp_path)
    db.upsert(PaperRecord(paper_id="P1", title="Orig title", authors="", sample_size=None))
    csv_path = tmp_path / "edit.csv"
    _write_csv(
        csv_path,
        ["paper_id", "authors", "sample_size", "doi", "title"],
        [{"paper_id": "P1", "authors": "Tran K.; Le M.", "sample_size": "250",
          "doi": "10.1/xyz", "title": ""}],  # title 空欄
    )
    stats = import_metadata(csv_path, db=db)
    assert stats.records_updated == 1
    rec = db.get("P1")
    assert rec.authors == "Tran K.; Le M."
    assert rec.sample_size == 250
    assert rec.doi == "10.1/xyz"
    db.close()


def test_blank_cells_do_not_overwrite(tmp_path):
    db = _db(tmp_path)
    db.upsert(PaperRecord(paper_id="P1", title="Keep me", authors="Existing A.",
                          sample_size=99, notes="keep notes"))
    csv_path = tmp_path / "edit.csv"
    # すべて空欄で上書きを試みる -> 既存値は保持される
    _write_csv(
        csv_path,
        ["paper_id", "title", "authors", "sample_size", "notes"],
        [{"paper_id": "P1", "title": "", "authors": "", "sample_size": "", "notes": ""}],
    )
    stats = import_metadata(csv_path, db=db)
    rec = db.get("P1")
    assert rec.title == "Keep me"
    assert rec.authors == "Existing A."
    assert rec.sample_size == 99
    assert rec.notes == "keep notes"
    assert stats.fields_updated == 0
    db.close()


def test_unknown_paper_id_is_skipped(tmp_path):
    db = _db(tmp_path)
    db.upsert(PaperRecord(paper_id="P1", title="x"))
    csv_path = tmp_path / "edit.csv"
    _write_csv(csv_path, ["paper_id", "authors"],
               [{"paper_id": "GHOST", "authors": "Nobody"}])
    stats = import_metadata(csv_path, db=db)
    assert "GHOST" in stats.skipped_missing_id
    assert stats.records_updated == 0
    db.close()


def test_invalid_document_type_is_warned_not_applied(tmp_path):
    db = _db(tmp_path)
    db.upsert(PaperRecord(paper_id="P1", title="x"))
    csv_path = tmp_path / "edit.csv"
    _write_csv(csv_path, ["paper_id", "document_type"],
               [{"paper_id": "P1", "document_type": "totally_invalid"}])
    stats = import_metadata(csv_path, db=db)
    rec = db.get("P1")
    assert rec.document_type.value == "unknown"  # 変更されない
    assert any("document_type" in w for w in stats.warnings)
    db.close()


def test_method_column_maps_to_research_method(tmp_path):
    db = _db(tmp_path)
    db.upsert(PaperRecord(paper_id="P1", title="x"))
    csv_path = tmp_path / "edit.csv"
    # Excel の列名は 'method' (export 側にあわせる)
    _write_csv(csv_path, ["paper_id", "method"], [{"paper_id": "P1", "method": "survey"}])
    import_metadata(csv_path, db=db)
    assert db.get("P1").research_method == "survey"
    db.close()


def test_import_from_xlsx_all_papers_sheet(tmp_path):
    import openpyxl

    db = _db(tmp_path)
    db.upsert(PaperRecord(paper_id="P1", title="keep", authors=""))
    xlsx = tmp_path / "inv.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "All_Papers"
    ws.append(["paper_id", "title", "authors", "year"])
    ws.append(["P1", "", "Tran K.", 2023])  # title 空欄, authors/year 補完
    wb.save(xlsx)

    import_metadata(xlsx, db=db)
    rec = db.get("P1")
    assert rec.title == "keep"          # 空欄は保持
    assert rec.authors == "Tran K."
    assert rec.year == 2023
    db.close()


def test_metadata_enables_same_dataset_detection(tmp_path):
    """authors/sample_size/generation_groups 補完で same_dataset_possible が出る。"""
    db = _db(tmp_path)
    t1 = "Multigenerational Work Values in Thai SMEs: A Thesis"
    t2 = "Comparing Generation X and Y Employees in Small Firms in Thailand"
    db.upsert(PaperRecord(paper_id="A", title=t1, normalized_title=normalize_title(t1)))
    db.upsert(PaperRecord(paper_id="B", title=t2, normalized_title=normalize_title(t2)))

    # 補完前: タイトルが違いメタデータも無いので重複ではない
    before = compare(db.get("A"), db.get("B"))
    assert before.duplicate_type == DuplicateType.not_duplicate

    # 人間が Excel/CSV で著者・標本数・対象国・世代区分・手法を補完
    csv_path = tmp_path / "edit.csv"
    header = ["paper_id", "authors", "sample_size", "target_country",
              "organization_context", "generation_groups", "research_method"]
    common = {"authors": "Suwan A.; Pongsak B.", "sample_size": "320",
              "target_country": "Thailand", "organization_context": "SME",
              "generation_groups": "gen_x; gen_y", "research_method": "survey"}
    _write_csv(csv_path, header,
               [{"paper_id": "A", **common}, {"paper_id": "B", **common}])
    stats = import_metadata(csv_path, db=db)
    assert stats.records_updated == 2

    # 補完後: 同一データの可能性ありとして検出される
    after = compare(db.get("A"), db.get("B"))
    assert after.duplicate_type == DuplicateType.same_dataset_possible
    assert after.recommended_action  # 「自動除外しない」旨
    db.close()
