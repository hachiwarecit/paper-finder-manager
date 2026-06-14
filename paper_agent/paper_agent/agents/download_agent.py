"""DownloadAgent: approved_for_download かつ合法な候補だけ PDF を取得。

取得成功しても N に数えない。取得後は ingest → dedupe → screen に流す。
"""
from __future__ import annotations

from ..downloader import download_approved
from .base import AgentResult


class DownloadAgent:
    name = "DownloadAgent"

    def run(self, db) -> AgentResult:
        res = AgentResult(self.name)
        stats = download_approved(db)
        res.info.update(stats)
        res.add(f"承認済み {stats['approved']} 件中 成功 {stats['downloaded']} / "
                f"スキップ {stats['skipped']} / 失敗 {stats['failed']}")
        return res
