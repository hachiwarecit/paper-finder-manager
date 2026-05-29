"""Unpaywall API で DOI の Open Access PDF を確認する。

Unpaywall は email パラメータが必須。環境変数 UNPAYWALL_EMAIL で指定する。
合法的に取得可能な OA PDF のみを対象とし、出版社サイトのスクレイピングは行わない。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"
DEFAULT_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "research@example.com")


def check(doi: str, email: str = DEFAULT_EMAIL,
          session: Optional[requests.Session] = None) -> Dict[str, Any]:
    """DOI の OA 情報を返す。

    戻り値: {"is_oa": bool, "oa_status": str, "pdf_url": str|None}
    DOI がない / API 失敗時は is_oa=False を返す。
    """
    empty = {"is_oa": False, "oa_status": "", "pdf_url": None,
             "license": "", "host_type": ""}
    if not doi:
        return empty

    sess = session or requests.Session()
    try:
        resp = sess.get(
            UNPAYWALL_URL.format(doi=doi.strip()),
            params={"email": email},
            timeout=30,
        )
        if resp.status_code == 404:
            return empty
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[check_unpaywall] doi='{doi}' でエラー: {exc}")
        return empty

    data = resp.json()
    best = data.get("best_oa_location") or {}
    return {
        "is_oa": bool(data.get("is_oa")),
        "oa_status": data.get("oa_status", "") or "",
        "pdf_url": best.get("url_for_pdf") or None,
        "license": best.get("license") or "",
        "host_type": best.get("host_type") or "",  # publisher / repository
    }


def enrich(candidate: Dict[str, Any], email: str = DEFAULT_EMAIL,
           session: Optional[requests.Session] = None) -> Dict[str, Any]:
    """候補 dict を Unpaywall の結果で補強する。

    既に pdf_url がある場合は尊重し、無ければ Unpaywall の PDF を埋める。
    """
    info = check(candidate.get("doi", ""), email=email, session=session)
    if not candidate.get("pdf_url") and info["pdf_url"]:
        candidate["pdf_url"] = info["pdf_url"]
        candidate["oa_source"] = "unpaywall:" + (info.get("host_type") or "oa")
    if not candidate.get("oa_status") and info["oa_status"]:
        candidate["oa_status"] = info["oa_status"]
    if not candidate.get("license") and info.get("license"):
        candidate["license"] = info["license"]
    candidate["is_oa"] = info["is_oa"] or bool(candidate.get("pdf_url"))
    return candidate


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Unpaywall で DOI の OA 状況を確認")
    ap.add_argument("doi")
    args = ap.parse_args()
    print(json.dumps(check(args.doi), ensure_ascii=False, indent=2))
