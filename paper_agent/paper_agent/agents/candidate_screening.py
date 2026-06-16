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


_EVIDENCE_SOURCES = ("title", "abstract", "full_text", "metadata")


def classify_manual(cand: Candidate) -> tuple[str | None, str]:
    """pending の有望候補を手動PDF取得キュー等に分類する。

    返り値 (new_candidate_status, manual_priority)。変更不要なら (None, "")。
    自動取得できる候補 (auto_approve対象) は触らない。
    """
    from ..downloader import LEGAL_NOTES_OK

    if cand.candidate_status.value != "pending":
        return None, ""

    country_ok = (cand.target_country in ("Thailand", "Vietnam")
                  and cand.target_country_source in _EVIDENCE_SOURCES
                  and bool((cand.target_country_evidence or "").strip()))
    doctype_ok = cand.document_type_guess not in ("teaching_case", "conference_abstract")
    if not (country_ok and doctype_ok):
        return None, ""  # 有望でない → pending のまま

    # 多国間研究 (分離可否未確認) は手動高優先ではなく needs_review にする
    if cand.is_multi_country_study and not (cand.country_data_separable
                                            or cand.country_specific_analysis_available):
        return "needs_review", ""

    has_legal_pdf = bool(cand.pdf_url) and cand.legality_note in LEGAL_NOTES_OK
    if has_legal_pdf:
        return None, ""  # 自動承認 (DownloadAgent) が扱う

    multigen = _has_multigen_signal(cand)
    workplace = bool(cand.workplace_keywords)
    category = bool(cand.category_keywords)
    if multigen and workplace and category:
        return "manual_pdf_required", "high"
    if multigen or workplace:
        return "manual_pdf_required", "medium"
    return None, ""
