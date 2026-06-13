"""SQLite による管理台帳ストア。

papers.sqlite に PaperRecord を1論文1行で保存する。
スキーマは PaperRecord のフィールドに対応した1テーブル ``papers``。
"""
from __future__ import annotations

import sqlite3
from enum import Enum
from pathlib import Path
from typing import Optional

from .models import PaperRecord
from .utils import DB_PATH, get_logger

logger = get_logger()

# papers テーブルの列 (PaperRecord のフィールドに対応)
COLUMNS = [
    "paper_id", "country", "category", "title", "normalized_title",
    "authors", "year", "doi", "source_name", "source_url", "pdf_url",
    "local_pdf_path", "local_text_path", "original_language",
    "analysis_language", "full_text_available", "target_country",
    "organization_context", "generation_groups", "number_of_generations",
    "sample_size", "research_method", "main_topic", "peer_reviewed",
    "document_type", "license_status", "legality_note", "screening_status",
    "rejection_reason", "duplicate_group_id", "duplicate_of",
    "duplicate_confidence", "same_dataset_warning", "notes",
    "pdf_sha256", "text_sha256", "created_at", "updated_at",
]

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS papers (
    {", ".join(f"{c} TEXT" for c in COLUMNS if c not in ("paper_id",))},
    paper_id TEXT PRIMARY KEY
);
"""


class PaperDB:
    """papers.sqlite への薄いラッパ。"""

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._create()

    def _create(self) -> None:
        self.conn.execute(_CREATE_SQL)
        self.conn.commit()

    # ------------------------------------------------------------------
    # 変換ヘルパ
    # ------------------------------------------------------------------
    @staticmethod
    def _to_row(rec: PaperRecord) -> dict:
        d = rec.model_dump()
        row = {}
        for c in COLUMNS:
            v = d.get(c)
            if isinstance(v, bool):
                v = "1" if v else "0"
            elif isinstance(v, Enum):
                v = v.value
            elif v is None:
                v = None
            else:
                v = str(v)
            row[c] = v
        return row

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PaperRecord:
        d = dict(row)

        def as_bool(key, default=False):
            v = d.get(key)
            if v in (None, ""):
                return default
            return str(v) in ("1", "True", "true")

        def as_int(key):
            v = d.get(key)
            if v in (None, ""):
                return None
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None

        def as_float(key):
            v = d.get(key)
            if v in (None, ""):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        return PaperRecord(
            paper_id=d.get("paper_id") or "",
            country=d.get("country") or "unknown",
            category=d.get("category") or "unknown",
            title=d.get("title") or "",
            normalized_title=d.get("normalized_title") or "",
            authors=d.get("authors") or "",
            year=as_int("year"),
            doi=d.get("doi") or None,
            source_name=d.get("source_name") or "unknown",
            source_url=d.get("source_url") or None,
            pdf_url=d.get("pdf_url") or None,
            local_pdf_path=d.get("local_pdf_path") or None,
            local_text_path=d.get("local_text_path") or None,
            original_language=d.get("original_language") or "unknown",
            analysis_language=d.get("analysis_language") or "en",
            full_text_available=as_bool("full_text_available"),
            target_country=d.get("target_country") or "unknown",
            organization_context=d.get("organization_context") or "unknown",
            generation_groups=d.get("generation_groups") or "",
            number_of_generations=as_int("number_of_generations") or 0,
            sample_size=as_int("sample_size"),
            research_method=d.get("research_method") or "unknown",
            main_topic=d.get("main_topic") or "unknown",
            peer_reviewed=(None if d.get("peer_reviewed") in (None, "") else as_bool("peer_reviewed")),
            document_type=d.get("document_type") or "unknown",
            license_status=d.get("license_status") or "unknown",
            legality_note=d.get("legality_note") or "",
            screening_status=d.get("screening_status") or "candidate",
            rejection_reason=d.get("rejection_reason") or "",
            duplicate_group_id=d.get("duplicate_group_id") or None,
            duplicate_of=d.get("duplicate_of") or None,
            duplicate_confidence=as_float("duplicate_confidence"),
            same_dataset_warning=as_bool("same_dataset_warning"),
            notes=d.get("notes") or "",
            pdf_sha256=d.get("pdf_sha256") or None,
            text_sha256=d.get("text_sha256") or None,
            created_at=d.get("created_at") or "",
            updated_at=d.get("updated_at") or "",
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def upsert(self, rec: PaperRecord) -> None:
        rec.touch()
        row = self._to_row(rec)
        placeholders = ", ".join(["?"] * len(COLUMNS))
        updates = ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "paper_id")
        sql = (
            f"INSERT INTO papers ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(paper_id) DO UPDATE SET {updates}"
        )
        self.conn.execute(sql, [row[c] for c in COLUMNS])
        self.conn.commit()

    def get(self, paper_id: str) -> Optional[PaperRecord]:
        cur = self.conn.execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,))
        row = cur.fetchone()
        return self._from_row(row) if row else None

    def all(self) -> list[PaperRecord]:
        cur = self.conn.execute("SELECT * FROM papers ORDER BY paper_id")
        return [self._from_row(r) for r in cur.fetchall()]

    def exists(self, paper_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM papers WHERE paper_id=?", (paper_id,))
        return cur.fetchone() is not None

    def by_status(self, status: str) -> list[PaperRecord]:
        cur = self.conn.execute(
            "SELECT * FROM papers WHERE screening_status=? ORDER BY paper_id", (status,)
        )
        return [self._from_row(r) for r in cur.fetchall()]

    def count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM papers")
        return int(cur.fetchone()["n"])

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PaperDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
