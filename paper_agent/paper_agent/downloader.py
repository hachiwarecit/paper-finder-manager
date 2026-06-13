"""Open Access PDF の合法的ダウンロード (Priority B)。

- Open Access で合法に取得できる PDF のみダウンロードする。
- paywall 回避・Sci-Hub・スクレイピングは行わない。
- 失敗時は理由を記録し、例外で全体を止めない。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .utils import DATA_DIR, get_logger, sha256_file, slugify

logger = get_logger()


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
