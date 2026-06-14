"""MetadataExtractionAgent: 本文から可能な範囲でメタデータを抽出。

不明は推測せず unknown。unknown が多い accepted は needs_review に落として N から外す。
accepted には metadata.json を書き出す。
"""
from __future__ import annotations

from ..artifacts import count_unknown_fields, write_metadata_json
from ..models import ScreeningStatus
from ..pipeline import apply_screen
from .base import AgentResult

# この数を超える主要メタデータが unknown なら needs_review
MAX_UNKNOWN_FOR_ACCEPTED = 4


class MetadataExtractionAgent:
    name = "MetadataExtractionAgent"

    def run(self, db, max_unknown: int = MAX_UNKNOWN_FOR_ACCEPTED) -> AgentResult:
        res = AgentResult(self.name)
        demoted = []
        written = 0
        for rec in db.all():
            if rec.screening_status != ScreeningStatus.accepted:
                continue
            unknowns = count_unknown_fields(rec)
            if unknowns > max_unknown:
                rec.screening_status = ScreeningStatus.needs_review
                rec.rejection_reason = (
                    f"主要メタデータの unknown が多い ({unknowns}) ため要確認。"
                )
                db.upsert(rec)
                demoted.append(rec.paper_id)
                continue
            write_metadata_json(rec)
            written += 1
        res.info["metadata_written"] = written
        res.info["demoted_for_unknown"] = demoted
        res.add(f"metadata.json 書き出し {written} 件 / unknown過多で要確認 {len(demoted)} 件")
        return res
