"""ファイル取り込みの共通ロジック (cli と autopilot の両方が使う)。

PDF/TXT からテキスト抽出し、PaperRecord を作って DB に保存する。
autopilot からは、候補メタデータ (doi/source_url/pdf_url/category 等) を seed として
引き継いで PaperRecord を作れる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .db import PaperDB
from .extractor import extract_text, guess_title
from .models import PaperRecord, ScreeningStatus
from .utils import (
    DATA_DIR,
    detect_language,
    get_logger,
    normalize_title,
    sha256_file,
    sha256_text,
    slugify,
)

logger = get_logger()

COUNTRY_NAME = {"TH": "Thailand", "VN": "Vietnam", "JP": "Japan"}
TEXT_DIR = "03_screening"


def make_paper_id(stem: str, country: str, db: PaperDB) -> str:
    base = slugify(stem, max_len=40) or "paper"
    pid = f"{country}-{base}"
    if not db.exists(pid):
        return pid
    i = 2
    while db.exists(f"{pid}-{i}"):
        i += 1
    return f"{pid}-{i}"


def ingest_file(path: str | Path, country: str, db: PaperDB, *,
                paper_id: Optional[str] = None, seed: Optional[dict] = None) -> Optional[PaperRecord]:
    """1ファイルを取り込み PaperRecord を保存して返す。失敗時 None。"""
    path = Path(path)
    if not path.is_file():
        logger.warning("取り込み対象なし: %s", path)
        return None
    try:
        country = (country or "unknown").upper()
        pid = paper_id or make_paper_id(path.stem, country, db)
        res = extract_text(path)
        text = res.text or ""
        full_text = bool(text and len(text) >= 200)

        text_out_dir = DATA_DIR / TEXT_DIR
        text_out_dir.mkdir(parents=True, exist_ok=True)
        text_path = None
        if text:
            text_path = text_out_dir / f"{slugify(pid)}.txt"
            text_path.write_text(text, encoding="utf-8")

        is_pdf = path.suffix.lower() == ".pdf"
        seed = seed or {}
        rec = PaperRecord(
            paper_id=pid,
            country=country if country in COUNTRY_NAME else "unknown",
            category=seed.get("category", "unknown"),
            title=seed.get("title", "") or "",
            authors=seed.get("authors", "") or "",
            doi=seed.get("doi") or None,
            source_name=seed.get("source_name", "local_ingest"),
            source_url=seed.get("source_url") or None,
            pdf_url=seed.get("pdf_url") or None,
            target_country=seed.get("target_country", "unknown") or "unknown",
            local_pdf_path=str(path) if is_pdf else None,
            local_text_path=str(text_path) if text_path else (str(path) if not is_pdf else None),
            original_language=detect_language(text),
            full_text_available=full_text,
            pdf_sha256=sha256_file(path) if is_pdf else None,
            text_sha256=sha256_text(text),
            screening_status=ScreeningStatus.candidate,
            notes=f"ingested from {path.name}"
            + (f"; extract_error={res.error}" if res.error else "")
            + (f"; from_candidate={seed.get('candidate_id')}" if seed.get("candidate_id") else ""),
        )
        if not rec.title:
            tg = guess_title(text)
            if tg:
                rec.title = tg
        rec.normalized_title = normalize_title(rec.title)
        db.upsert(rec)
        if res.error:
            logger.warning("%s: 抽出に問題 (%s)", pid, res.error)
        return rec
    except Exception as exc:  # noqa: BLE001
        logger.error("取り込み失敗 %s: %s", path.name, exc)
        return None


def ingest_folder(input_dir: str | Path, country: str, db: PaperDB) -> list[PaperRecord]:
    """フォルダ内の PDF/TXT を再帰的に取り込む。"""
    in_dir = Path(input_dir)
    if not in_dir.exists():
        logger.warning("入力フォルダが存在しません: %s", in_dir)
        return []
    files = sorted(
        p for p in in_dir.rglob("*") if p.suffix.lower() in (".pdf", ".txt", ".text", ".md")
    )
    out = []
    for path in files:
        rec = ingest_file(path, country, db)
        if rec is not None:
            out.append(rec)
    return out
