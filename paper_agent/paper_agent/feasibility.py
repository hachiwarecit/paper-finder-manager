"""100本達成可能性の判定 (feasibility)。

厳格 N に加えて、手動PDF取得候補も含めた到達見込みを可視化する。
水増しはしない。あくまで「自動だけ」「手動込み」で何本見込めるかを示す。
"""
from __future__ import annotations

from .analysis import summarize
from .db import PaperDB
from .models import ScreeningStatus
from .utils import now_iso


def compute(db: PaperDB, target_n: int = 100) -> dict:
    papers = db.all()
    cands = db.all_candidates()
    s = summarize(papers)

    high = [c for c in cands if c.candidate_status.value == "manual_pdf_required" and c.manual_priority == "high"]
    medium = [c for c in cands if c.candidate_status.value == "manual_pdf_required" and c.manual_priority == "medium"]
    approved = [c for c in cands if c.candidate_status.value == "approved_for_download"]
    needs_review_c = [c for c in cands if c.candidate_status.value == "needs_review"]

    current_n = s.analysis_n
    # 自動だけの見込み: 現在N + 承認済み(まだ未処理)の楽観的見積もり
    auto_potential = current_n + len(approved)
    # 手動込み見込み: auto + high + medium (人間がPDFを取得できた場合)
    manual_potential = auto_potential + len(high) + len(medium)

    # 国×カテゴリの不足 (均等割り当て目安)
    countries = ["Thailand", "Vietnam"]
    cats = [f"category_{i}" for i in range(1, 7)]
    per_cell_target = max(1, target_n // (len(countries) * len(cats)))
    shortfall: dict[str, int] = {}
    for country in countries:
        for cat in cats:
            have = s.country_category.get((country, cat), 0)
            short = max(0, per_cell_target - have)
            if short:
                shortfall[f"{country}|{cat}"] = short

    reasons: list[str] = []
    if current_n < target_n:
        if auto_potential < target_n:
            reasons.append("自動取得 (OA PDF) だけでは候補母数が不足しており 100 本に届かない見込み。")
        if manual_potential < target_n:
            reasons.append("手動PDF取得候補を足しても 100 本に届かない見込み。検索ソース/クエリの"
                           "さらなる拡大、または収集対象の見直しが必要。")
        else:
            reasons.append("手動PDF取得候補を含めれば 100 本に到達できる可能性がある。"
                           "high_priority_candidates.csv の PDF を人間が取得して ingest すること。")

    return {
        "target_n": target_n,
        "current_accepted_n": current_n,
        "th_n": s.th_n,
        "vn_n": s.vn_n,
        "high_priority_count": len(high),
        "medium_priority_count": len(medium),
        "approved_pending": len(approved),
        "needs_review_candidates": len(needs_review_c),
        "auto_only_potential": auto_potential,
        "manual_included_potential": manual_potential,
        "auto_only_reaches_target": auto_potential >= target_n,
        "manual_included_reaches_target": manual_potential >= target_n,
        "shortfall_by_cell": shortfall,
        "reasons": reasons,
    }


def format_md(db: PaperDB, target_n: int = 100) -> str:
    f = compute(db, target_n)
    lines: list[str] = []
    lines.append("# feasibility (100本達成可能性)")
    lines.append("")
    lines.append(f"- 生成日時: {now_iso()}")
    lines.append(f"- target_N: {f['target_n']}")
    lines.append(f"- 現在の accepted N: **{f['current_accepted_n']}** (TH={f['th_n']}, VN={f['vn_n']})")
    lines.append(f"- 達成率: {f['current_accepted_n']}/{f['target_n']} "
                 f"({(f['current_accepted_n']/f['target_n']*100 if f['target_n'] else 0):.1f}%)")
    lines.append("")
    lines.append("## 手動PDF取得候補")
    lines.append("")
    lines.append(f"- high priority (manual_pdf_required): {f['high_priority_count']}")
    lines.append(f"- medium priority: {f['medium_priority_count']}")
    lines.append(f"- 承認済み未処理 (approved_for_download): {f['approved_pending']}")
    lines.append(f"- needs_review 候補 (多国間/同一データ疑い等): {f['needs_review_candidates']}")
    lines.append("")
    lines.append("## 到達見込み")
    lines.append("")
    lines.append(f"- 自動PDF取得だけ: {f['auto_only_potential']} 本 "
                 f"→ 100本到達: **{'可能' if f['auto_only_reaches_target'] else '不可'}**")
    lines.append(f"- 手動PDF取得込み: {f['manual_included_potential']} 本 "
                 f"→ 100本到達: **{'可能' if f['manual_included_reaches_target'] else '不可'}**")
    lines.append("")
    lines.append("## 国×カテゴリ別の不足数 (均等割り当て目安)")
    lines.append("")
    if f["shortfall_by_cell"]:
        for cell, short in sorted(f["shortfall_by_cell"].items()):
            lines.append(f"- {cell}: あと {short} 本")
    else:
        lines.append("- (不足なし)")
    lines.append("")
    lines.append("## 100本に届かない場合の理由")
    lines.append("")
    if f["reasons"]:
        for r in f["reasons"]:
            lines.append(f"- {r}")
    else:
        lines.append("- 現在の見込みで target_N に到達可能。")
    return "\n".join(lines)
