"""OpenAlex API による論文検索 (Priority B)。

検索結果はすぐ採用せず、candidate として PaperRecord に変換して返すだけ。
ネットワーク不通や requests 不在でも例外で全体を止めず、空リストを返す。
"""
from __future__ import annotations

import os
from typing import Optional

from .models import DocumentType, PaperRecord, ScreeningStatus
from .utils import get_logger, normalize_title, now_iso

logger = get_logger()

OPENALEX_URL = "https://api.openalex.org/works"


def _reconstruct_abstract(inv_index: Optional[dict]) -> str:
    if not inv_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def search_normalized(query: str, *, limit: int = 50,
                      mailto: Optional[str] = None) -> list[dict]:
    """OpenAlex を検索し、ソース非依存の正規化 work 辞書のリストを返す。

    harvest が使う。ネットワーク不通/requests 不在でも例外で止めず [] を返す。
    """
    try:
        import requests  # type: ignore
    except ImportError:
        logger.warning("requests 不在のため OpenAlex 検索をスキップ。")
        return []

    mailto = mailto or os.environ.get("OPENALEX_MAILTO") or os.environ.get("CONTACT_EMAIL")
    params = {"search": query, "per-page": min(max(limit, 1), 200), "mailto": mailto or ""}
    try:
        resp = requests.get(OPENALEX_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAlex 検索失敗: %s", exc)
        return []

    out: list[dict] = []
    for work in data.get("results", [])[:limit]:
        oa = work.get("open_access", {}) or {}
        out.append({
            "title": work.get("title") or work.get("display_name") or "",
            "authors": "; ".join(
                a.get("author", {}).get("display_name", "")
                for a in work.get("authorships", [])
            ),
            "year": work.get("publication_year"),
            "doi": (work.get("doi") or "").replace("https://doi.org/", "") or None,
            "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
            "source_name": "OpenAlex",
            "source_url": work.get("id"),
            "pdf_url": oa.get("oa_url"),
            "open_access_flag": bool(oa.get("is_oa")),
            "document_type_guess": "journal_article" if work.get("type") == "article" else "unknown",
            "license": oa.get("oa_status") or "unknown",
        })
    logger.info("OpenAlex: %d 件取得 (query=%r)", len(out), query)
    return out


def search(query: str, *, country: str = "unknown", category: str = "unknown",
           limit: int = 20, mailto: Optional[str] = None) -> list[PaperRecord]:
    """OpenAlex を検索し PaperRecord(candidate) のリストを返す。"""
    try:
        import requests  # type: ignore
    except ImportError:
        logger.warning("requests 不在のため OpenAlex 検索をスキップ。")
        return []

    mailto = mailto or os.environ.get("OPENALEX_MAILTO") or os.environ.get("CONTACT_EMAIL")
    params = {
        "search": query,
        "per-page": min(max(limit, 1), 200),
        "mailto": mailto or "",
    }
    try:
        resp = requests.get(OPENALEX_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAlex 検索失敗: %s", exc)
        return []

    records: list[PaperRecord] = []
    for i, work in enumerate(data.get("results", [])[:limit]):
        title = work.get("title") or work.get("display_name") or ""
        doi = (work.get("doi") or "").replace("https://doi.org/", "") or None
        authors = "; ".join(
            a.get("author", {}).get("display_name", "")
            for a in work.get("authorships", [])
        )
        abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
        oa = work.get("open_access", {}) or {}
        pdf_url = oa.get("oa_url")
        pid = f"{country}-OA-{i+1:03d}"
        rec = PaperRecord(
            paper_id=pid,
            country=country,
            category=category,
            title=title,
            normalized_title=normalize_title(title),
            authors=authors,
            year=work.get("publication_year"),
            doi=doi,
            source_name="OpenAlex",
            source_url=work.get("id"),
            pdf_url=pdf_url,
            main_topic=abstract[:500],
            document_type=DocumentType.journal_article if work.get("type") == "article" else DocumentType.unknown,
            license_status=oa.get("oa_status", "unknown") or "unknown",
            full_text_available=bool(pdf_url),
            peer_reviewed=not work.get("is_paratext", False),
            screening_status=ScreeningStatus.candidate,
            notes=f"OpenAlex 取得 {now_iso()}",
        )
        records.append(rec)
    logger.info("OpenAlex: %d 件取得 (query=%r)", len(records), query)
    return records
