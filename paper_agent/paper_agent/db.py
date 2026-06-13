"""SQLite による管理台帳ストア。

papers.sqlite に PaperRecord を1論文1行で保存する。
スキーマは PaperRecord のフィールドに対応した1テーブル ``papers``。
"""
from __future__ import annotations

import sqlite3
from enum import Enum
from pathlib import Path
from typing import Optional

from .models import Candidate, PaperRecord
from .utils import DB_PATH, get_logger

logger = get_logger()

# papers テーブルの列 (PaperRecord のフィールドに対応)
COLUMNS = [
    "paper_id", "country", "category", "title", "normalized_title",
    "authors", "year", "doi", "source_name", "source_url", "pdf_url",
    "local_pdf_path", "local_text_path", "original_language",
    "analysis_language", "full_text_available", "target_country",
    "organization_context", "workplace_fit", "generation_groups",
    "number_of_generations", "sample_size", "research_method", "main_topic",
    "peer_reviewed", "document_type", "license_status", "legality_note",
    "screening_status", "rejection_reason", "duplicate_group_id",
    "duplicate_of", "duplicate_confidence", "same_dataset_warning", "notes",
    "pdf_sha256", "text_sha256", "created_at", "updated_at",
]

# candidates テーブルの列 (Candidate のフィールドに対応)
CAND_COLUMNS = [
    "candidate_id", "title", "normalized_title", "authors", "year", "doi",
    "abstract", "source_name", "source_url", "pdf_url", "country", "category",
    "target_country", "generation_keywords", "workplace_keywords",
    "category_keywords", "document_type_guess", "open_access_flag",
    "legality_note", "candidate_score", "candidate_status", "duplicate_status",
    "duplicate_of", "same_dataset_warning", "notes", "download_status",
    "download_error", "attempted_url", "timestamp", "created_at", "updated_at",
]

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS papers (
    {", ".join(f"{c} TEXT" for c in COLUMNS if c not in ("paper_id",))},
    paper_id TEXT PRIMARY KEY
);
"""

_CREATE_CAND_SQL = f"""
CREATE TABLE IF NOT EXISTS candidates (
    {", ".join(f"{c} TEXT" for c in CAND_COLUMNS if c not in ("candidate_id",))},
    candidate_id TEXT PRIMARY KEY
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
        self.conn.execute(_CREATE_CAND_SQL)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """既存 DB に不足している列を追加する (後方互換)。"""
        cur = self.conn.execute("PRAGMA table_info(papers)")
        existing = {row["name"] for row in cur.fetchall()}
        for col in COLUMNS:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT")
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
            workplace_fit=as_bool("workplace_fit"),
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

    # ------------------------------------------------------------------
    # candidates (harvest 段階)
    # ------------------------------------------------------------------
    @staticmethod
    def _cand_to_row(cand: Candidate) -> dict:
        d = cand.model_dump()
        row = {}
        for c in CAND_COLUMNS:
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
    def _cand_from_row(row: sqlite3.Row) -> Candidate:
        d = dict(row)

        def as_bool(key):
            v = d.get(key)
            return str(v) in ("1", "True", "true") if v not in (None, "") else False

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
                return 0.0
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        return Candidate(
            candidate_id=d.get("candidate_id") or "",
            title=d.get("title") or "",
            normalized_title=d.get("normalized_title") or "",
            authors=d.get("authors") or "",
            year=as_int("year"),
            doi=d.get("doi") or None,
            abstract=d.get("abstract") or "",
            source_name=d.get("source_name") or "unknown",
            source_url=d.get("source_url") or None,
            pdf_url=d.get("pdf_url") or None,
            country=d.get("country") or "unknown",
            category=d.get("category") or "unknown",
            target_country=d.get("target_country") or "unknown",
            generation_keywords=d.get("generation_keywords") or "",
            workplace_keywords=d.get("workplace_keywords") or "",
            category_keywords=d.get("category_keywords") or "",
            document_type_guess=d.get("document_type_guess") or "unknown",
            open_access_flag=as_bool("open_access_flag"),
            legality_note=d.get("legality_note") or "unknown",
            candidate_score=as_float("candidate_score"),
            candidate_status=d.get("candidate_status") or "pending",
            duplicate_status=d.get("duplicate_status") or "new_candidate",
            duplicate_of=d.get("duplicate_of") or None,
            same_dataset_warning=as_bool("same_dataset_warning"),
            notes=d.get("notes") or "",
            download_status=d.get("download_status") or "",
            download_error=d.get("download_error") or "",
            attempted_url=d.get("attempted_url") or "",
            timestamp=d.get("timestamp") or "",
            created_at=d.get("created_at") or "",
            updated_at=d.get("updated_at") or "",
        )

    def upsert_candidate(self, cand: Candidate) -> None:
        cand.touch()
        row = self._cand_to_row(cand)
        placeholders = ", ".join(["?"] * len(CAND_COLUMNS))
        updates = ", ".join(f"{c}=excluded.{c}" for c in CAND_COLUMNS if c != "candidate_id")
        sql = (
            f"INSERT INTO candidates ({', '.join(CAND_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(candidate_id) DO UPDATE SET {updates}"
        )
        self.conn.execute(sql, [row[c] for c in CAND_COLUMNS])
        self.conn.commit()

    def get_candidate(self, candidate_id: str) -> Optional[Candidate]:
        cur = self.conn.execute(
            "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)
        )
        row = cur.fetchone()
        return self._cand_from_row(row) if row else None

    def all_candidates(self) -> list[Candidate]:
        cur = self.conn.execute("SELECT * FROM candidates ORDER BY candidate_id")
        return [self._cand_from_row(r) for r in cur.fetchall()]

    def candidates_by_status(self, status: str) -> list[Candidate]:
        cur = self.conn.execute(
            "SELECT * FROM candidates WHERE candidate_status=? ORDER BY candidate_id",
            (status,),
        )
        return [self._cand_from_row(r) for r in cur.fetchall()]

    def candidate_count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM candidates")
        return int(cur.fetchone()["n"])

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PaperDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
