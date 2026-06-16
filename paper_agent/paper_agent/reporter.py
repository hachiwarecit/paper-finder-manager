"""重複判定・採否判定の確認レポート (コンソール + Markdown)。

区分:
  - 完全重複                (screening_status == duplicate)
  - 同一データの可能性あり  (same_dataset_warning == True / 多くは needs_review)
  - 主分析候補              (screening_status == accepted)
  - 補助文献                (screening_status == supplementary)
  - 除外                    (screening_status == rejected)
  - 要確認                  (needs_review のうち同一データ警告以外)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .db import PaperDB
from .models import PaperRecord, ScreeningStatus
from .utils import DATA_DIR, get_logger, now_iso

logger = get_logger()


@dataclass
class ReportGroups:
    exact_duplicate: list[PaperRecord] = field(default_factory=list)
    same_dataset: list[PaperRecord] = field(default_factory=list)
    accepted: list[PaperRecord] = field(default_factory=list)
    supplementary: list[PaperRecord] = field(default_factory=list)
    rejected: list[PaperRecord] = field(default_factory=list)
    needs_review: list[PaperRecord] = field(default_factory=list)


def group_records(records: list[PaperRecord]) -> ReportGroups:
    g = ReportGroups()
    for r in records:
        status = r.screening_status
        if status == ScreeningStatus.duplicate:
            g.exact_duplicate.append(r)
        elif r.same_dataset_warning:
            # 同一データ警告は最優先で「同一データの可能性あり」に入れる
            g.same_dataset.append(r)
        elif status == ScreeningStatus.needs_review:
            g.needs_review.append(r)
        elif status == ScreeningStatus.accepted:
            g.accepted.append(r)
        elif status == ScreeningStatus.supplementary:
            g.supplementary.append(r)
        elif status == ScreeningStatus.rejected:
            g.rejected.append(r)
        else:  # candidate (未判定)
            g.needs_review.append(r)
    return g


def _short_title(r: PaperRecord) -> str:
    t = (r.title or "").strip().replace("\n", " ")
    if not t:
        return "(タイトル未取得)"
    return t if len(t) <= 90 else t[:87] + "…"


def _dup_line(r: PaperRecord) -> str:
    conf = "" if r.duplicate_confidence is None else f" conf={r.duplicate_confidence:.2f}"
    kind = "完全重複" if (r.duplicate_confidence or 0) >= 0.999 else "高類似"
    return (f"- `{r.paper_id}` [{kind}] → `{r.duplicate_of}`{conf}  \n"
            f"  {_short_title(r)}")


def _same_dataset_line(r: PaperRecord) -> str:
    of = f" (関連: `{r.duplicate_of}`)" if r.duplicate_of else ""
    return (f"- `{r.paper_id}`{of}  \n"
            f"  {_short_title(r)}  \n"
            f"  メモ: {r.notes or '-'}")


def _accept_line(r: PaperRecord) -> str:
    gens = r.generation_groups or "-"
    tr = " ⚠翻訳要" if r.original_language in ("th", "vi") else ""
    return (f"- `{r.paper_id}` [{r.target_country} / {r.category} / {r.number_of_generations}世代]{tr}  \n"
            f"  {_short_title(r)}  \n"
            f"  世代: {gens}")


def _reason_line(r: PaperRecord) -> str:
    reason = r.rejection_reason or r.notes or "-"
    dt = r.document_type.value if hasattr(r.document_type, "value") else str(r.document_type)
    return (f"- `{r.paper_id}` [{dt}]  \n"
            f"  {_short_title(r)}  \n"
            f"  理由: {reason}")


def build_markdown(records: list[PaperRecord]) -> str:
    g = group_records(records)
    total = len(records)
    lines: list[str] = []
    lines.append("# paper_agent 重複判定・採否レポート")
    lines.append("")
    lines.append(f"- 生成日時: {now_iso()}")
    lines.append(f"- 総件数: **{total}**")
    lines.append("")
    lines.append("## サマリ")
    lines.append("")
    lines.append("| 区分 | 件数 |")
    lines.append("|------|------|")
    lines.append(f"| 完全重複 | {len(g.exact_duplicate)} |")
    lines.append(f"| 同一データの可能性あり | {len(g.same_dataset)} |")
    lines.append(f"| 主分析候補 (accepted) | {len(g.accepted)} |")
    lines.append(f"| 補助文献 (supplementary) | {len(g.supplementary)} |")
    lines.append(f"| 除外 (rejected) | {len(g.rejected)} |")
    lines.append(f"| 要確認 (needs_review) | {len(g.needs_review)} |")
    lines.append("")

    def section(title: str, items: list[str], note: str = "") -> None:
        lines.append(f"## {title} ({len(items)} 件)")
        if note:
            lines.append("")
            lines.append(f"> {note}")
        lines.append("")
        if items:
            lines.extend(items)
        else:
            lines.append("- （該当なし）")
        lines.append("")

    section("完全重複", [_dup_line(r) for r in g.exact_duplicate],
            "同一論文/版違い。主分析には1件だけ残す。ファイルは削除せずフラグ管理。")
    section("同一データの可能性あり", [_same_dataset_line(r) for r in g.same_dataset],
            "タイトルは違うが同一研究の疑い。自動除外せず人間が確認すること。")
    section("主分析候補", [_accept_line(r) for r in g.accepted],
            "2世代以上比較・職場文脈・本文あり・カテゴリ該当。最終採用は人間が承認。")
    section("補助文献", [_reason_line(r) for r in g.supplementary],
            "単一世代/要旨のみ/Teaching Case/概念整理など。背景として利用可。")
    section("除外", [_reason_line(r) for r in g.rejected],
            "対象国不一致・職場文脈なし等。")
    section("要確認", [_reason_line(r) for r in g.needs_review],
            "Results/Discussion 混在など、抽出範囲の確認が必要。")

    # 翻訳が必要な論文の一覧
    tr_needed = [r for r in records if r.original_language in ("th", "vi")
                 and r.screening_status != ScreeningStatus.duplicate]
    lines.append(f"## 翻訳が必要な論文 ({len(tr_needed)} 件)")
    lines.append("")
    if tr_needed:
        for r in tr_needed:
            lines.append(f"- `{r.paper_id}` ({r.original_language}) {_short_title(r)}")
    else:
        lines.append("- （該当なし）")
    lines.append("")

    # Analysis N Summary (N に数えてよいのは accepted かつ重複なし等のみ)
    from .analysis import format_summary

    lines.append("## Analysis N Summary")
    lines.append("")
    lines.append("```")
    lines.append(format_summary(records))
    lines.append("```")
    lines.append("")
    lines.append("> 注: harvest 件数・download 件数は N ではありません。")
    lines.append("> N は `screening_status=accepted` かつ `duplicate_of` 空・"
                 "`same_dataset_warning=false`・`full_text_available=true`・"
                 "`number_of_generations>=2`・対象国が Thailand/Vietnam・`workplace_fit=true` のみ。")
    lines.append("")
    return "\n".join(lines)


def build_console_summary(records: list[PaperRecord]) -> str:
    g = group_records(records)
    rows = [
        ("完全重複", len(g.exact_duplicate)),
        ("同一データの可能性あり", len(g.same_dataset)),
        ("主分析候補 (accepted)", len(g.accepted)),
        ("補助文献 (supplementary)", len(g.supplementary)),
        ("除外 (rejected)", len(g.rejected)),
        ("要確認 (needs_review)", len(g.needs_review)),
    ]
    width = max(len(name) for name, _ in rows)
    out = [f"総件数: {len(records)}", "-" * (width + 8)]
    for name, n in rows:
        out.append(f"{name.ljust(width)} : {n}")

    from .analysis import format_summary
    out.append("")
    out.append(format_summary(records))
    return "\n".join(out)


def write_report(fmt: str = "md", db: PaperDB | None = None) -> Path | None:
    """レポートを生成。Markdown を data/10_exports/report.md に保存し Path を返す。"""
    own = db is None
    db = db or PaperDB()
    try:
        records = db.all()
    finally:
        if own:
            db.close()

    if fmt == "console":
        return None  # 呼び出し側がコンソール出力

    md = build_markdown(records)
    out_dir = DATA_DIR / "10_exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "report.md"
    out_path.write_text(md, encoding="utf-8")
    logger.info("レポート出力: %s", out_path)
    return out_path
