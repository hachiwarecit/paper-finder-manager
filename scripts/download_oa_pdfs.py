"""Open Access PDF の安全ダウンロード。

方針: 無料・合法・Open Access と確認できた PDF だけを自動保存する。
少しでも不明・有料・ログイン必須・非公式の可能性があるものは保存せず、
メタデータと URL だけ残して人間確認に回す。

- 出版社サイトの無断スクレイピングはしない。OpenAlex / Unpaywall が提示する
  OA PDF URL のみを対象にする。
- 403 / 401 / 429 を回避しようとしない（リトライ・UA 偽装・制限回避をしない）。
- 正直な User-Agent を使う。API のレート制限を守る。

保存先 (CLAUDE.md):
    pdfs/{country}/{slot}_{short_title}.pdf
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from common import PDF_DIR, REPORTS_DIR, now_iso, slugify

# Open Access とみなす oa_status
OA_STATUSES = {"gold", "green", "hybrid", "bronze", "diamond"}

# 信頼できる公開元（部分一致）。出版社公式 OA・リポジトリ・PMC・arXiv・DOAJ 等
TRUSTED_DOMAINS = [
    "ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "europepmc.org",
    "arxiv.org", "doaj.org", "mdpi.com", "mdpi-res.com", "frontiersin.org",
    "springeropen.com", "biomedcentral.com", "plos.org", "hindawi.com",
    "nature.com", "elifesciences.org", "peerj.com", "zenodo.org", "osf.io",
    "scielo", "doabooks.org", "ssoar.info",
]
# 非公式・違法性が高い / 権利状態が不明な個人アップロード（部分一致）
UNTRUSTED_DOMAINS = [
    "sci-hub", "scihub", "libgen", "library.lol", "researchgate.net",
    "academia.edu", "z-lib", "b-ok", "annas-archive", "1lib",
]
# HTML 本文に含まれていたら有料/ログインとみなすマーカー
PAYWALL_MARKERS = [
    "paywall", "please log in", "log in to", "sign in to", "access denied",
    "purchase", "rent this", "subscribe", "buy article", "get access",
    "institutional login", "add to cart", "login required", "403 forbidden",
    "you do not have access",
]

MIN_PDF_BYTES = 3000           # これ未満は PDF とみなさない
RATE_LIMIT_SECONDS = 1.0       # ダウンロード試行間の待機（レート制限尊重）
HONEST_UA = "paper-finder-manager/0.1 (academic research; +https://openalex.org)"

# legal_download_status の取りうる値
STATUS_SAFE_DOWNLOADED = "safe_oa_downloaded"
STATUS_SAFE_FAILED = "safe_oa_but_download_failed"
STATUS_MANUAL = "manual_check_required"
STATUS_PAYWALL = "paywalled_or_login_required"
STATUS_BLOCKED_403 = "blocked_403"
STATUS_NOT_PDF = "not_pdf_response"
STATUS_UNTRUSTED = "illegal_or_untrusted_source"
STATUS_UNKNOWN_LICENSE = "unknown_license"

SAFETY_LOG_CSV = REPORTS_DIR / "download_safety_log.csv"
SAFETY_LOG_COLUMNS = [
    "country", "category", "title", "doi", "pdf_url", "domain",
    "oa_status", "license", "legal_download_status", "legal_download_reason",
    "local_pdf_path",
]


# --------------------------------------------------------------------------
def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_untrusted(domain: str) -> bool:
    return any(d in domain for d in UNTRUSTED_DOMAINS)


def _is_trusted(domain: str) -> bool:
    if any(d in domain for d in TRUSTED_DOMAINS):
        return True
    if domain.endswith(".edu") or ".edu." in domain:
        return True
    if domain.endswith(".gov") or ".gov." in domain:
        return True
    if ".ac." in domain or domain.endswith(".ac"):
        return True
    return any(k in domain for k in (
        "repository", "dspace", "eprints", "e-prints", "openaccess"))


def _target_path(candidate: Dict[str, Any], country: str) -> Path:
    slot = candidate.get("slot") or f"{country}_unassigned"
    short = slugify(candidate.get("title", ""))
    return PDF_DIR / country / f"{slot}_{short}.pdf"


# --------------------------------------------------------------------------
def legal_safety_check(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """ネットワーク前の合法性ゲート。

    戻り値: {allowed, legal_download_status, legal_download_reason,
             domain, oa_source, oa_status, license}
    allowed=True のときのみ、ネットワーク検証に進んでよい。
    """
    url = candidate.get("pdf_url") or ""
    domain = _domain(url)
    info = {
        "domain": domain,
        "oa_source": candidate.get("oa_source", ""),
        "oa_status": candidate.get("oa_status", "") or "",
        "license": candidate.get("license", "") or "",
    }

    def verdict(status: str, reason: str, allowed: bool) -> Dict[str, Any]:
        return {**info, "allowed": allowed,
                "legal_download_status": status, "legal_download_reason": reason}

    if not url:
        return verdict(STATUS_MANUAL, "no pdf_url; save metadata only", False)
    if _is_untrusted(domain):
        return verdict(STATUS_UNTRUSTED,
                       f"untrusted/illegal source domain: {domain}", False)

    oa_confirmed = (bool(candidate.get("is_oa"))
                    or info["oa_status"].lower() in OA_STATUSES)
    if not oa_confirmed:
        return verdict(STATUS_UNKNOWN_LICENSE,
                       "open access not confirmed (is_oa/oa_status missing)", False)
    if not _is_trusted(domain):
        return verdict(STATUS_MANUAL,
                       f"domain not in trusted OA allowlist: {domain}", False)
    return verdict("safe_oa_pending",
                   "passed pre-network OA & domain checks", True)


def _inspect_and_save(candidate: Dict[str, Any], dest: Path,
                      session: requests.Session) -> Tuple[str, str, Optional[str]]:
    """ネットワーク検証して安全なら保存する。

    戻り値: (legal_download_status, reason, local_pdf_path|None)
    403/401/429 は回避せずそのまま記録する。
    """
    url = candidate["pdf_url"]
    try:
        resp = session.get(url, stream=True, timeout=60, allow_redirects=True,
                           headers={"User-Agent": HONEST_UA,
                                    "Accept": "application/pdf"})
    except requests.RequestException as exc:
        return STATUS_SAFE_FAILED, f"network error: {exc}", None

    with resp:
        code = resp.status_code
        if code == 401:
            return STATUS_PAYWALL, "HTTP 401 Unauthorized (login required)", None
        if code == 403:
            return STATUS_BLOCKED_403, "HTTP 403 Forbidden (not bypassed)", None
        if code == 429:
            return STATUS_MANUAL, "HTTP 429 Too Many Requests (rate limited; not retried)", None
        if code != 200:
            return STATUS_MANUAL, f"HTTP {code}", None

        # リダイレクト後のドメインも再確認
        final_domain = _domain(resp.url)
        if _is_untrusted(final_domain):
            return STATUS_UNTRUSTED, f"redirected to untrusted domain: {final_domain}", None
        if not _is_trusted(final_domain):
            return STATUS_MANUAL, f"redirected to non-allowlist domain: {final_domain}", None

        ctype = (resp.headers.get("Content-Type", "") or "").lower()
        try:
            first = next(resp.iter_content(chunk_size=8192), b"")
        except requests.RequestException as exc:
            return STATUS_SAFE_FAILED, f"read error: {exc}", None

        looks_pdf = first[:5].startswith(b"%PDF") or "application/pdf" in ctype
        if not looks_pdf:
            snippet = first[:2048].decode("utf-8", "ignore").lower()
            if "html" in ctype or "<html" in snippet:
                if any(m in snippet for m in PAYWALL_MARKERS):
                    return STATUS_PAYWALL, "HTML page with paywall/login markers", None
                return STATUS_NOT_PDF, "HTML response, not a PDF", None
            return STATUS_NOT_PDF, f"non-PDF content-type: {ctype or 'unknown'}", None

        # 保存
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with open(dest, "wb") as f:
                if first:
                    f.write(first)
                    size += len(first)
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
        except OSError as exc:
            return STATUS_SAFE_FAILED, f"write error: {exc}", None

        if size < MIN_PDF_BYTES:
            try:
                dest.unlink()
            except OSError:
                pass
            return STATUS_NOT_PDF, f"file too small ({size} bytes)", None

        rel = str(dest.relative_to(PDF_DIR.parent))
        return STATUS_SAFE_DOWNLOADED, f"OA PDF saved ({size} bytes)", rel


def download_one(candidate: Dict[str, Any], country: str,
                 session: requests.Session) -> Dict[str, Any]:
    """1 候補の安全チェック + ダウンロード。candidate を更新して結果を返す。"""
    gate = legal_safety_check(candidate)
    candidate["oa_source"] = gate.get("oa_source", candidate.get("oa_source", ""))
    candidate["license"] = gate.get("license", candidate.get("license", ""))
    candidate["pdf_checked_at"] = now_iso()

    if not gate["allowed"]:
        status, reason, local = gate["legal_download_status"], \
            gate["legal_download_reason"], None
    else:
        dest = _target_path(candidate, country)
        if dest.exists() and dest.stat().st_size >= MIN_PDF_BYTES:
            status, reason = STATUS_SAFE_DOWNLOADED, "already downloaded"
            local = str(dest.relative_to(PDF_DIR.parent))
        else:
            status, reason, local = _inspect_and_save(candidate, dest, session)

    candidate["legal_download_status"] = status
    candidate["legal_download_reason"] = reason
    if local:
        candidate["local_pdf_path"] = local

    return {
        "country": candidate.get("country", country),
        "category": candidate.get("category", ""),
        "title": candidate.get("title", ""),
        "doi": candidate.get("doi", ""),
        "pdf_url": candidate.get("pdf_url", ""),
        "domain": gate.get("domain", ""),
        "oa_status": candidate.get("oa_status", ""),
        "license": candidate.get("license", ""),
        "legal_download_status": status,
        "legal_download_reason": reason,
        "local_pdf_path": candidate.get("local_pdf_path", ""),
    }


def download_all(candidates: List[Dict[str, Any]],
                 session: Optional[requests.Session] = None,
                 rate_limit: float = RATE_LIMIT_SECONDS
                 ) -> Tuple[int, List[Dict[str, Any]]]:
    """候補リストを安全チェック付きで処理する。

    戻り値: (保存成功件数, 安全ログ行のリスト)
    重複・除外、既に保存済みのものはネットワークに行かない。
    """
    sess = session or requests.Session()
    saved = 0
    log_rows: List[Dict[str, Any]] = []
    network_hits = 0

    for cand in candidates:
        status = cand.get("screening_status", "")
        if status == "duplicate" or status.startswith("exclude"):
            continue
        if not cand.get("pdf_url"):
            continue
        already = bool(cand.get("local_pdf_path"))

        # レート制限: 実際にネットワークへ行く前に待機
        gate = legal_safety_check(cand)
        if gate["allowed"] and not already and network_hits > 0:
            time.sleep(rate_limit)
        if gate["allowed"] and not already:
            network_hits += 1

        row = download_one(cand, cand.get("country", "Unknown"), sess)
        log_rows.append(row)
        if row["legal_download_status"] == STATUS_SAFE_DOWNLOADED and not already:
            saved += 1

    return saved, log_rows


def write_safety_log(rows: List[Dict[str, Any]]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(SAFETY_LOG_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SAFETY_LOG_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in SAFETY_LOG_COLUMNS})


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="単一URLの合法性チェック")
    ap.add_argument("--url", required=True)
    ap.add_argument("--oa-status", default="gold")
    ap.add_argument("--is-oa", action="store_true")
    args = ap.parse_args()

    c = {"pdf_url": args.url, "oa_status": args.oa_status, "is_oa": args.is_oa}
    print(json.dumps(legal_safety_check(c), ensure_ascii=False, indent=2))
