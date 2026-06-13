"""Open Access PDF の合法的ダウンロード (Priority B)。

- Open Access で合法に取得できる PDF のみダウンロードする。
- paywall 回避・Sci-Hub・スクレイピングは行わない。
- 失敗時は理由を記録し、例外で全体を止めない。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .utils import DATA_DIR, get_logger, now_iso, sha256_file, slugify

logger = get_logger()

# legality_note がこれらを含む場合はダウンロードしない (違法・不明確)
_LEGALITY_BLOCK_TERMS = ("illegal", "違法", "unknown", "unclear", "blocked", "不明")


@dataclass
class DownloadResult:
    ok: bool = False
    path: Optional[str] = None
    sha256: Optional[str] = None
    error: str = ""
    legality_note: str = ""


def download_pdf(pdf_url: str, paper_id: str, *, license_status: str = "unknown") -> DownloadResult:
    """OA PDF をダウンロードして data/02_downloaded に保存する。"""
    if not pdf_url:
        return DownloadResult(error="pdf_url が空")

    # 合法性チェック (簡易): 既知の違法サイトを弾く
    lowered = pdf_url.lower()
    for banned in ("sci-hub", "libgen", "z-lib"):
        if banned in lowered:
            return DownloadResult(
                error=f"違法と思われるホスト ({banned}) のため取得しない",
                legality_note="blocked: illegal source",
            )

    try:
        import requests  # type: ignore
    except ImportError:
        return DownloadResult(error="requests not installed")

    out_dir = DATA_DIR / "02_downloaded"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slugify(paper_id)}.pdf"

    try:
        headers = {"User-Agent": "paper_agent/1.0 (research; mailto:contact@example.com)"}
        resp = requests.get(pdf_url, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "pdf" not in ctype.lower() and not pdf_url.lower().endswith(".pdf"):
            return DownloadResult(
                error=f"PDF ではない可能性 (Content-Type={ctype})。HTML 取得は行わない。"
            )
        with out_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ダウンロード失敗 (%s): %s", paper_id, exc)
        return DownloadResult(error=str(exc))

    return DownloadResult(
        ok=True,
        path=str(out_path),
        sha256=sha256_file(out_path),
        legality_note=f"open access ({license_status})",
    )


def legality_ok(legality_note: str) -> bool:
    """legality_note が違法・不明確でないか。"""
    note = (legality_note or "").strip().lower()
    if not note:
        return False
    return not any(term in note for term in _LEGALITY_BLOCK_TERMS)


def _download_to_country_dir(pdf_url: str, candidate_id: str, country: str):
    """OA PDF を data/02_downloaded/<country>/ に保存する。"""
    lowered = pdf_url.lower()
    for banned in ("sci-hub", "libgen", "z-lib"):
        if banned in lowered:
            return DownloadResult(error=f"違法と思われるホスト ({banned})",
                                  legality_note="blocked: illegal source")
    try:
        import requests  # type: ignore
    except ImportError:
        return DownloadResult(error="requests not installed")

    sub = (country or "unknown").upper()
    out_dir = DATA_DIR / "02_downloaded" / sub
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slugify(candidate_id)}.pdf"
    try:
        headers = {"User-Agent": "paper_agent/1.0 (research; mailto:contact@example.com)"}
        resp = requests.get(pdf_url, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "pdf" not in ctype.lower() and not pdf_url.lower().endswith(".pdf"):
            return DownloadResult(error=f"PDF ではない可能性 (Content-Type={ctype})")
        with out_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
    except Exception as exc:  # noqa: BLE001
        return DownloadResult(error=str(exc))
    return DownloadResult(ok=True, path=str(out_path), sha256=sha256_file(out_path),
                          legality_note="open access")


def download_approved(db) -> dict:
    """candidate_status == approved_for_download の候補だけ PDF を取得する。

    PDF 取得に成功しても N には数えない。成功後は通常の ingest→dedupe→screen を通す。
    返り値は集計 dict。
    """
    from .models import CandidateStatus

    stats = {"approved": 0, "downloaded": 0, "skipped": 0, "failed": 0}
    approved = db.candidates_by_status(CandidateStatus.approved_for_download.value)
    stats["approved"] = len(approved)

    for cand in approved:
        cand.timestamp = now_iso()
        cand.attempted_url = cand.pdf_url or ""

        if not cand.pdf_url:
            cand.download_status = "skipped"
            cand.download_error = "pdf_url が空"
            stats["skipped"] += 1
            db.upsert_candidate(cand)
            continue
        if not legality_ok(cand.legality_note):
            cand.download_status = "skipped"
            cand.download_error = f"legality_note が違法/不明確: '{cand.legality_note}'"
            stats["skipped"] += 1
            db.upsert_candidate(cand)
            logger.info("%s: 合法性が不明確のためスキップ", cand.candidate_id)
            continue

        res = _download_to_country_dir(cand.pdf_url, cand.candidate_id, cand.country)
        if res.ok:
            cand.download_status = "success"
            cand.download_error = ""
            cand.candidate_status = CandidateStatus.downloaded
            cand.notes = (cand.notes + " | " if cand.notes else "") + f"downloaded -> {res.path}"
            stats["downloaded"] += 1
        else:
            cand.download_status = "failed"
            cand.download_error = res.error
            stats["failed"] += 1
            logger.warning("%s: ダウンロード失敗 %s", cand.candidate_id, res.error)
        db.upsert_candidate(cand)

    return stats
