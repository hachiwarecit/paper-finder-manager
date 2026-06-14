"""CandidateScreeningAgent: 候補が研究ルールに合いそうかをスコアリング。

候補段階では自動採用しない。candidate_score は優先順位付け用。
"""
from __future__ import annotations

from ..harvester import score_candidate
from ..models import Candidate
from .base import AgentResult


def _has_multigen_signal(cand: Candidate) -> bool:
    terms = [t for t in (cand.generation_keywords or "").split("; ") if t]
    markers = {"multigenerational", "multi-generational", "cross-generational",
               "intergenerational", "generational difference", "generational differences",
               "generation gap", "generational diversity", "generationally diverse"}
    if any(t in markers for t in terms):
        return True
    # 異なる世代グループを2つ以上 (boomer/x/y/z) 含むか
    gen_groups = set()
    for t in terms:
        if "boomer" in t:
            gen_groups.add("b")
        elif "generation x" in t or t == "gen x" or "gen-x" in t or "xers" in t:
            gen_groups.add("x")
        elif "generation y" in t or t == "gen y" or "millennial" in t:
            gen_groups.add("y")
        elif "generation z" in t or t == "gen z" or "zoomer" in t:
            gen_groups.add("z")
    return len(gen_groups) >= 2


class CandidateScreeningAgent:
    name = "CandidateScreeningAgent"

    def evaluate(self, cand: Candidate) -> dict:
        return {
            "country_ok": cand.target_country in ("Thailand", "Vietnam"),
            "workplace_ok": bool(cand.workplace_keywords),
            "multigen": _has_multigen_signal(cand),
            "category_ok": bool(cand.category_keywords),
            "fulltext_likely": bool(cand.open_access_flag or cand.pdf_url),
            "doctype_ok": cand.document_type_guess not in ("teaching_case", "conference_abstract"),
        }

    def run(self, candidates: list[Candidate], db) -> AgentResult:
        res = AgentResult(self.name)
        for cand in candidates:
            cand.candidate_score = score_candidate(cand)  # 重複状態反映後の再スコア
            db.upsert_candidate(cand)
        res.info["scored"] = len(candidates)
        res.add(f"{len(candidates)} 件の候補をスコアリング (自動採用はしない)。")
        return res
