"""FullTextScreeningAgent: PDF/本文取得後に主分析へ入れてよいか判定。

採否判定 (pipeline.apply_screen) に委譲する。accepted の条件は screener が担保:
target_country TH/VN・workplace_fit・full_text・2世代以上・teaching_case/
conference_abstract でない・単一世代/要旨のみ/学生のみ/非職場でない。
"""
from __future__ import annotations

from ..pipeline import screen_all_papers
from .base import AgentResult


class FullTextScreeningAgent:
    name = "FullTextScreeningAgent"

    def run(self, db) -> AgentResult:
        res = AgentResult(self.name)
        counts = screen_all_papers(db)
        res.info["counts"] = counts
        res.add(f"採否判定: {counts}")
        return res
