"""Open Access PDF のダウンロード。

pdf_url がある候補のみダウンロードする。出版社サイトの無断スクレイピングは行わず、
OpenAlex / Unpaywall が提示する合法的な OA PDF URL のみを対象とする。

保存先 (CLAUDE.md):
    pdfs/{country}/{slot}_{short_title}.pdf
ファイル名はスペース・記号・日本語・長すぎるタイトルを避ける (common.slugify)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import requests

from common import PDF_DIR, slugify


def _target_path(candidate: Dict[str, Any], country: str) -> Path:
    slot = candidate.get("slot") or f"{country}_unassigned"
    short = slugify(candidate.get("title", ""))
    return PDF_DIR / country / f"{slot}_{short}.pdf"


def download(candidate: Dict[str, Any], country: str,
             session: Optional[requests.Session] = None) -> Optional[str]:
    """候補の OA PDF を保存する。

    成功時は保存先の相対パス文字列、PDF が無い/失敗時は None を返す。
    """
    pdf_url = candidate.get("pdf_url")
    if not pdf_url:
        return None

    sess = session or requests.Session()
    dest = _target_path(candidate, country)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 0:
        return str(dest.relative_to(PDF_DIR.parent))

    try:
        resp = sess.get(pdf_url, timeout=60, stream=True,
                        headers={"User-Agent": "paper-finder-manager/0.1"})
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        # HTML が返ってきた場合は OA PDF ではないとみなしスキップ
        if "pdf" not in ctype.lower() and not pdf_url.lower().endswith(".pdf"):
            print(f"[download_oa_pdfs] PDFではない応答 ({ctype}): {pdf_url}")
            return None
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    except requests.RequestException as exc:
        print(f"[download_oa_pdfs] 失敗 {pdf_url}: {exc}")
        return None

    return str(dest.relative_to(PDF_DIR.parent))


def download_all(candidates, session: Optional[requests.Session] = None) -> int:
    """候補リストの OA PDF をまとめて取得し local_pdf_path を埋める。

    返り値: ダウンロード成功件数。
    """
    sess = session or requests.Session()
    count = 0
    for cand in candidates:
        # 重複・除外は対象外
        if cand.get("screening_status", "").startswith("exclude"):
            continue
        if cand.get("screening_status") == "duplicate":
            continue
        if cand.get("local_pdf_path"):
            continue
        path = download(cand, cand.get("country", "Unknown"), session=sess)
        if path:
            cand["local_pdf_path"] = path
            count += 1
    return count


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="単一PDFをダウンロード")
    ap.add_argument("--url", required=True)
    ap.add_argument("--country", required=True)
    ap.add_argument("--slot", required=True)
    ap.add_argument("--title", required=True)
    args = ap.parse_args()

    c = {"pdf_url": args.url, "slot": args.slot, "title": args.title}
    print(download(c, args.country))
