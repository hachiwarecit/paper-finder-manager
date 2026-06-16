"""失敗分析 (なぜ候補から N が増えなかったか) の集計と Markdown 出力。"""
from __future__ import annotations

from .analysis import is_analysis_n
from .db import PaperDB
from .models import ScreeningStatus


def pipeline_counts(db: PaperDB) -> dict:
    cands = db.all_candidates()
    papers = db.all()

    def cand_n(*statuses):
        return sum(1 for c in cands if c.candidate_status.value in statuses)

    def paper_n(*statuses):
        return sum(1 for p in papers if p.screening_status.value in statuses)

    return {
        "candidate_total": len(cands),
        "approved_for_download": cand_n("approved_for_download"),
        "download_attempted": sum(1 for c in cands if c.download_status in ("attempted", "success", "failed")),
        "download_success": sum(1 for c in cands if c.download_status == "success"),
        "download_failed": sum(1 for c in cands if c.download_status == "failed"),
        "manual_pdf_required": cand_n("manual_pdf_required", "library_access_required",
                                      "contact_author_required"),
        "papers_ingested": len(papers),
        "screened": sum(1 for p in papers if p.screening_status != ScreeningStatus.candidate),
        "accepted": paper_n("accepted"),
        "rejected": paper_n("rejected"),
        "supplementary": paper_n("supplementary"),
        "needs_review": paper_n("needs_review"),
        "duplicate_papers": paper_n("duplicate"),
        "analysis_n": sum(1 for p in papers if is_analysis_n(p)),
    }


def candidate_drop_reasons(db: PaperDB) -> dict:
    """候補が N に届かなかった理由を分類集計する。"""
    cands = db.all_candidates()
    papers = db.all()
    EVIDENCE = ("title", "abstract", "full_text", "metadata")

    def blocker(name):
        return sum(1 for c in cands if name in (c.auto_approve_blockers or ""))

    reasons = {
        "target_country_source=query_only": sum(1 for c in cands if c.target_country_source == "query_only"),
        "target_country_evidence_missing": sum(1 for c in cands if not (c.target_country_evidence or "").strip()),
        "target_country_not_TH_VN": sum(1 for c in cands if c.target_country not in ("Thailand", "Vietnam")),
        "is_multi_country_study (要確認)": sum(1 for c in cands if c.is_multi_country_study),
        "generation_evidence_insufficient": blocker("generation_evidence_insufficient"),
        "workplace_evidence_missing": blocker("workplace_evidence_missing"),
        "pdf_url_missing": sum(1 for c in cands if not c.pdf_url),
        "legality_unknown": blocker("legality_unknown"),
        "duplicate": sum(1 for c in cands if c.duplicate_status.value in ("exact_duplicate", "probable_duplicate")),
        "same_dataset_possible": sum(1 for c in cands if c.duplicate_status.value == "same_dataset_possible"),
        "document_type_invalid (teaching/abstract)": sum(
            1 for c in cands if c.document_type_guess in ("teaching_case", "conference_abstract")),
        "download_failed": sum(1 for c in cands if c.download_status == "failed"),
        "screen_rejected (papers)": sum(1 for p in papers if p.screening_status == ScreeningStatus.rejected),
        "screen_supplementary (papers)": sum(1 for p in papers if p.screening_status == ScreeningStatus.supplementary),
        "screen_needs_review (papers)": sum(1 for p in papers if p.screening_status == ScreeningStatus.needs_review),
    }
    return reasons


def bottleneck(db: PaperDB) -> str:
    """最大のボトルネックを一言で。"""
    pc = pipeline_counts(db)
    if pc["candidate_total"] == 0:
        return "候補が0件 (検索が結果を返していない/ネットワーク未接続)。"
    if pc["approved_for_download"] == 0 and pc["manual_pdf_required"] == 0 and pc["papers_ingested"] == 0:
        return ("候補はあるが、自動取得できる合法OA PDFを持つ・かつ証拠ベースで対象国/世代/職場が"
                "揃う候補がほぼ無い。→ 手動PDF取得 (manual_pdf_queue) が必要。")
    if pc["download_success"] == 0 and pc["approved_for_download"] > 0:
        return "承認候補はあるが PDF 取得に成功していない (OA URL 不達 or ネットワーク)。"
    if pc["papers_ingested"] > 0 and pc["accepted"] == 0:
        return "PDFは取得できたが、本文スクリーニングで accepted に至っていない。"
    if pc["analysis_n"] < pc["accepted"]:
        return "accepted はあるが、N厳格条件 (証拠ベース国/多国間/重複) で N から外れている。"
    return "自動取得できる候補母数が不足。検索拡大 + 手動PDF取得が必要。"


def failure_analysis_md(db: PaperDB, target_n: int = 100) -> str:
    pc = pipeline_counts(db)
    reasons = candidate_drop_reasons(db)
    cands = db.all_candidates()
    high = sum(1 for c in cands if c.candidate_status.value == "manual_pdf_required" and c.manual_priority == "high")
    medium = sum(1 for c in cands if c.candidate_status.value == "manual_pdf_required" and c.manual_priority == "medium")

    lines: list[str] = []
    lines.append("# failure_analysis (なぜ候補からNが増えなかったか)")
    lines.append("")
    from .utils import now_iso
    lines.append(f"- 生成日時: {now_iso()}")
    lines.append(f"- target_N: {target_n}")
    lines.append(f"- 達成率: {pc['analysis_n']}/{target_n} "
                 f"({(pc['analysis_n']/target_n*100 if target_n else 0):.1f}%)")
    lines.append("")
    lines.append("## パイプライン集計")
    lines.append("")
    for k in ["candidate_total", "approved_for_download", "download_attempted", "download_success",
              "download_failed", "manual_pdf_required", "papers_ingested", "screened",
              "accepted", "rejected", "supplementary", "needs_review", "analysis_n"]:
        lines.append(f"- {k}: {pc[k]}")
    lines.append("")
    lines.append("## 候補が落ちた理由 (重複カウントあり)")
    lines.append("")
    for k, v in reasons.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## 手動PDF取得キュー")
    lines.append("")
    lines.append(f"- manual_pdf_required_high_priority: {high}")
    lines.append(f"- manual_pdf_required_medium_priority: {medium}")
    lines.append("")
    lines.append("## ボトルネック")
    lines.append("")
    lines.append(f"- {bottleneck(db)}")
    lines.append("")
    lines.append("## 次に改善すべきこと")
    lines.append("")
    lines.append("- 検索クエリ/検索ソースを増やし候補母数を拡大 (Phase A)。")
    lines.append("- 自動取得できない有望候補は manual_pdf_queue.csv / high_priority_candidates.csv を見て"
                 "人間が PDF を取得し、`ingest` で投入 (Phase B)。")
    lines.append("- 多国間研究は country_data_separable / country_specific_analysis_available を"
                 "Excel で確認・設定してから再判定。")
    return "\n".join(lines)
