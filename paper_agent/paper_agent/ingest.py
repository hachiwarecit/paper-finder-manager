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
from .models import CandidateStatus, PaperRecord, ScreeningStatus
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


def match_candidate(db: PaperDB, title: str, doi, authors: str = "", year=None):
    """取り込んだ PDF を candidates (manual_pdf_queue 等) と照合する。

    DOI 完全一致 → 正規化タイトル類似 (>=88) の順で最良候補を返す。無ければ None。
    """
    cands = db.all_candidates()
    if doi:
        d = str(doi).strip().lower()
        for c in cands:
            if c.doi and c.doi.strip().lower() == d:
                return c
    from rapidfuzz import fuzz
    nt = normalize_title(title)
    if not nt:
        return None
    best, best_score = None, 0.0
    for c in cands:
        cnt = c.normalized_title or normalize_title(c.title)
        if not cnt:
            continue
        score = fuzz.token_sort_ratio(nt, cnt)
        if score > best_score:
            best, best_score = c, score
    return best if best and best_score >= 88 else None


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
        # 候補メタデータ由来の対象国 (証拠ベースのみ) を引き継ぐ。
        seed_tc = seed.get("target_country", "unknown") or "unknown"
        seed_tc_source = seed.get("target_country_source", "unknown") or "unknown"
        if seed_tc in ("Thailand", "Vietnam") and seed_tc_source in ("title", "abstract", "full_text", "metadata"):
            tc, tc_source = seed_tc, "metadata"  # 論文から見ると候補メタ由来
            tc_evidence = seed.get("target_country_evidence", "") or "from candidate metadata"
        else:
            tc, tc_source, tc_evidence = "unknown", "unknown", ""
        rec = PaperRecord(
            paper_id=pid,
            country=country if country in COUNTRY_NAME else "unknown",
            query_country=(seed.get("query_country") or country or "unknown"),
            category=seed.get("category", "unknown"),
            title=seed.get("title", "") or "",
            authors=seed.get("authors", "") or "",
            doi=seed.get("doi") or None,
            source_name=seed.get("source_name", "local_ingest"),
            source_url=seed.get("source_url") or None,
            pdf_url=seed.get("pdf_url") or None,
            target_country=tc,
            target_country_source=tc_source,
            target_country_evidence=tc_evidence,
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

        # 手動取り込み (seed に candidate_id が無い) の場合、候補と照合してリンクする
        if not seed.get("candidate_id"):
            matched = match_candidate(db, rec.title, rec.doi, rec.authors, rec.year)
            if matched is not None:
                rec.notes = (rec.notes + "; " if rec.notes else "") + \
                    f"matched candidate {matched.candidate_id}"
                if not rec.source_url and matched.source_url:
                    rec.source_url = matched.source_url
                if rec.category == "unknown" and matched.category != "unknown":
                    rec.category = matched.category
                # 候補メタ由来の対象国 (証拠ベース) を引き継ぐ
                if (rec.target_country == "unknown"
                        and matched.target_country in ("Thailand", "Vietnam")
                        and matched.target_country_source in ("title", "abstract", "full_text", "metadata")):
                    rec.target_country = matched.target_country
                    rec.target_country_source = "metadata"
                    rec.target_country_evidence = matched.target_country_evidence or "from matched candidate"
                # 候補側を取得済みに更新
                matched.candidate_status = CandidateStatus.screened
                matched.downloaded_path = str(path)
                matched.download_status = "success"
                db.upsert_candidate(matched)
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
