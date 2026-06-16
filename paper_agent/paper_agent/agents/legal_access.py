"""LegalAccessAgent: PDF取得の合法性を判定する。

許可: OA PDF / Unpaywall best_oa_location / 出版社公式OA / 大学・公的リポジトリ /
      Creative Commons / Open Access 表示
禁止: Sci-Hub / LibGen / paywall回避 / Google Scholar 大量スクレイピング /
      ResearchGate の不明確な自動取得 / ログイン必須 / robots.txt 違反
"""
from __future__ import annotations

from ..models import Candidate
from .base import AgentResult

FORBIDDEN_HOSTS = ("sci-hub", "scihub", "libgen", "z-lib", "zlib", "booksc")
# 合法性が不明確 (候補URLとして記録はするが自動ダウンロードしない)
UNCLEAR_HOSTS = ("researchgate.net", "academia.edu", "scribd.com", "/login", "signin")
# 公式・リポジトリらしいホストの手掛かり
REPOSITORY_HINTS = (".edu", ".ac.", "repository", "dspace", "eprints", "core.ac.uk",
                    "doaj", "ncbi.nlm.nih.gov", "arxiv.org", "ssrn")


class LegalAccessAgent:
    name = "LegalAccessAgent"

    def classify(self, url: str | None, open_access_flag: bool) -> tuple[str, bool]:
        """(legality_note, allowed_for_auto_download) を返す。"""
        if not url:
            return "unknown (no url)", False
        low = url.lower()
        if any(h in low for h in FORBIDDEN_HOSTS):
            return "forbidden: illegal source", False
        if any(h in low for h in UNCLEAR_HOSTS):
            return "unclear (manual check required)", False
        if open_access_flag:
            return "open access", True
        if any(h in low for h in REPOSITORY_HINTS):
            return "official/repository", True
        return "unknown (manual check required)", False

    def annotate(self, cand: Candidate) -> bool:
        note, allowed = self.classify(cand.pdf_url, cand.open_access_flag)
        cand.legality_note = note
        return allowed

    def run(self, candidates: list[Candidate], db) -> AgentResult:
        res = AgentResult(self.name)
        legal = 0
        for cand in candidates:
            if self.annotate(cand):
                legal += 1
            db.upsert_candidate(cand)
        res.info["legal"] = legal
        res.info["total"] = len(candidates)
        res.add(f"合法に自動取得できそうな候補: {legal}/{len(candidates)}")
        return res
