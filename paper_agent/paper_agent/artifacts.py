"""accepted 論文の保存物 (metadata/screening/duplicate JSON, cleaned text) のパスと書き出し。

QualityAssuranceAgent は、accepted 論文にこれらが存在するかを検査する。
"""
from __future__ import annotations

import json
from pathlib import Path

from .cleaner import cleaned_filename
from .models import PaperRecord
from .utils import DATA_DIR, get_logger, now_iso

logger = get_logger()

ACCEPTED_DIR = DATA_DIR / "04_accepted"
CLEANED_DIR = DATA_DIR / "08_cleaned"


def metadata_json_path(rec: PaperRecord) -> Path:
    return ACCEPTED_DIR / f"{rec.paper_id}_metadata.json"


def screening_json_path(rec: PaperRecord) -> Path:
    return ACCEPTED_DIR / f"{rec.paper_id}_screening.json"


def duplicate_json_path(rec: PaperRecord) -> Path:
    return ACCEPTED_DIR / f"{rec.paper_id}_duplicate.json"


def cleaned_text_path(rec: PaperRecord) -> Path:
    return CLEANED_DIR / cleaned_filename(rec)


def has_metadata_json(rec: PaperRecord) -> bool:
    return metadata_json_path(rec).is_file()


def has_cleaned_text(rec: PaperRecord) -> bool:
    return cleaned_text_path(rec).is_file()


def write_metadata_json(rec: PaperRecord) -> Path:
    ACCEPTED_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "paper_id": rec.paper_id,
        "title": rec.title,
        "authors": rec.authors,
        "year": rec.year,
        "doi": rec.doi,
        "target_country": rec.target_country,
        "organization_context": rec.organization_context,
        "generation_groups": rec.generation_groups,
        "number_of_generations": rec.number_of_generations,
        "sample_size": rec.sample_size,
        "research_method": rec.research_method,
        "document_type": rec.document_type.value if hasattr(rec.document_type, "value") else str(rec.document_type),
        "original_language": rec.original_language,
        "analysis_language": rec.analysis_language,
        "source_url": rec.source_url,
        "pdf_url": rec.pdf_url,
        "written_at": now_iso(),
    }
    p = metadata_json_path(rec)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_screening_json(rec: PaperRecord, screening: dict) -> Path:
    ACCEPTED_DIR.mkdir(parents=True, exist_ok=True)
    p = screening_json_path(rec)
    p.write_text(json.dumps(screening, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_duplicate_json(rec: PaperRecord, duplicate: dict) -> Path:
    ACCEPTED_DIR.mkdir(parents=True, exist_ok=True)
    p = duplicate_json_path(rec)
    p.write_text(json.dumps(duplicate, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def count_unknown_fields(rec: PaperRecord) -> int:
    """主要メタデータのうち unknown / None の数を数える。"""
    fields = [
        rec.target_country, rec.organization_context, rec.research_method,
        rec.document_type.value if hasattr(rec.document_type, "value") else rec.document_type,
        rec.original_language,
    ]
    n = sum(1 for f in fields if f in (None, "", "unknown"))
    if rec.year is None:
        n += 1
    if not (rec.doi or rec.source_url):
        n += 1
    return n
