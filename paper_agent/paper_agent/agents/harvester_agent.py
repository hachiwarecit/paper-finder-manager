"""HarvesterAgent: OpenAlex/Crossref から候補メタデータを収集 (PDFは取得しない)。"""
from __future__ import annotations

from ..harvester import harvest_query
from .base import AgentResult, logger


class HarvesterAgent:
    name = "HarvesterAgent"

    def run(self, query_item: dict, limit: int, db, fetch=None) -> AgentResult:
        res = AgentResult(self.name)
        cands = harvest_query(
            query_item["query"], query_item["country"], query_item["category"],
            limit, db, fetch=fetch,
        )
        db.record_search(query_item["query"], query_item["country"],
                         query_item["category"], len(cands))
        res.info["harvested"] = len(cands)
        res.info["candidate_ids"] = [c.candidate_id for c in cands]
        res.add(f"{len(cands)} 件の候補を収集 (PDFは取得していない)。")
        return res, cands
