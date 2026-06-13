"""Unpaywall API による Open Access PDF の確認 (Priority B)。

DOI を渡すと、合法的に取得可能な OA PDF の URL とライセンス情報を返す。
ネットワーク不通や requests 不在でも例外で全体を止めない。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .utils import get_logger

logger = get_logger()

UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"


@dataclass
class UnpaywallResult:
    is_oa: bool = False
    pdf_url: Optional[str] = None
    license: str = "unknown"
    host_type: str = "unknown"
    error: str = ""


def lookup(doi: str, *, email: Optional[str] = None) -> UnpaywallResult:
    """DOI の OA 状況を調べる。"""
    if not doi:
        return UnpaywallResult(error="no doi")
    try:
        import requests  # type: ignore
    except ImportError:
        return UnpaywallResult(error="requests not installed")

    email = email or os.environ.get("CONTACT_EMAIL") or os.environ.get("OPENALEX_MAILTO")
    if not email:
        return UnpaywallResult(error="CONTACT_EMAIL 未設定 (Unpaywall は email 必須)")

    doi_clean = doi.replace("https://doi.org/", "").strip()
    try:
        resp = requests.get(
            UNPAYWALL_URL.format(doi=doi_clean), params={"email": email}, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unpaywall 照会失敗 (%s): %s", doi_clean, exc)
        return UnpaywallResult(error=str(exc))

    best = data.get("best_oa_location") or {}
    return UnpaywallResult(
        is_oa=bool(data.get("is_oa")),
        pdf_url=best.get("url_for_pdf") or best.get("url"),
        license=best.get("license") or "unknown",
        host_type=best.get("host_type") or "unknown",
    )
