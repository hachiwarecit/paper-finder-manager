"""AnalysisNAgent: N カウントの唯一の責任者。

harvest件数・download件数・candidate件数は絶対に N にしない。
"""
from __future__ import annotations

from ..analysis import summarize
from .base import AgentResult


class AnalysisNAgent:
    name = "AnalysisNAgent"

    def count(self, db) -> int:
        return summarize(db.all()).analysis_n

    def run(self, db) -> AgentResult:
        res = AgentResult(self.name)
        s = summarize(db.all())
        res.info["analysis_n"] = s.analysis_n
        res.info["th_n"] = s.th_n
        res.info["vn_n"] = s.vn_n
        res.info["country_category"] = {f"{c}|{cat}": n for (c, cat), n in s.country_category.items()}
        res.info["excluded_duplicates"] = s.excluded_duplicates
        res.info["same_dataset_possible"] = s.same_dataset_possible
        res.info["supplementary"] = s.supplementary
        res.info["rejected"] = s.rejected
        res.info["needs_review"] = s.needs_review
        res.add(f"Analysis N = {s.analysis_n} (TH={s.th_n}, VN={s.vn_n})")
        return res
