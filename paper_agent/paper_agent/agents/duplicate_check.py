"""DuplicateCheckAgent: 候補段階・PDF取得後・メタデータ補完後の重複チェック。

same_dataset_possible は自動除外せず needs_review にする (Nには入れない)。
"""
from __future__ import annotations

from ..harvester import candidate_dedupe
from ..models import Candidate
from ..pipeline import dedupe_all_papers
from .base import AgentResult


class DuplicateCheckAgent:
    name = "DuplicateCheckAgent"

    def candidate_stage(self, candidates: list[Candidate], db) -> AgentResult:
        """候補と既存の論文・候補を比較して duplicate_status を更新する。"""
        res = AgentResult(self.name)
        existing_papers = db.all()
        for cand in candidates:
            others = [c for c in db.all_candidates() if c.candidate_id != cand.candidate_id]
            candidate_dedupe(cand, existing_papers, others)
            db.upsert_candidate(cand)
        res.info["stage"] = "candidate"
        res.info["checked"] = len(candidates)
        return res

    def paper_stage(self, db) -> AgentResult:
        """取り込み済みの論文どうしの重複チェック (dedupe-all 相当)。"""
        res = AgentResult(self.name)
        counts = dedupe_all_papers(db)
        res.info["stage"] = "paper"
        res.info["counts"] = counts
        res.add(f"論文段階の重複チェック: {counts}")
        return res
