"""paper_agent コマンドラインインタフェース。

サブコマンド:
    init                 プロジェクト初期化 (フォルダ + DB 作成)
    ingest               フォルダから PDF/TXT を取り込み
    extract              テキスト抽出 (再実行)
    dedupe / dedupe-all  重複チェック
    screen / screen-all  採否判定
    clean                KH Coder 用クリーニング
    prepare-translation  翻訳準備
    export               Excel/CSV 台帳出力
    search               OpenAlex 検索 (candidate 保存)

設計方針: 1件のエラーで全体が止まらないよう、各論文の処理は try/except で囲む。
ファイルは勝手に削除しない。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cleaner import clean_to_file
from .db import PaperDB
from .exporter import export as export_inventory
from .extractor import extract_text, guess_title
from .models import PaperRecord, ScreeningStatus
from .utils import DATA_DIR
from .pipeline import apply_dedupe, apply_screen, dedupe_all_papers, get_text_for, screen_all_papers
from .screener import screen
from .translator_prep import prepare_translation
from .utils import (
    detect_language,
    ensure_dirs,
    get_logger,
    normalize_title,
    sha256_text,
    slugify,
)

logger = get_logger()

COUNTRY_NAME = {"TH": "Thailand", "VN": "Vietnam", "JP": "Japan"}
TEXT_DIR = "03_screening"


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    ensure_dirs()
    db = PaperDB()
    n = db.count()
    db.close()
    print("初期化完了。")
    print(f"  data/ サブフォルダを作成しました。")
    print(f"  DB: 既存レコード {n} 件")
    return 0


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------
def cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest import ingest_folder

    ensure_dirs()
    in_dir = Path(args.input)
    if not in_dir.exists():
        print(f"入力フォルダが存在しません: {in_dir}", file=sys.stderr)
        return 1
    country = (args.country or "unknown").upper()
    db = PaperDB()
    try:
        recs = ingest_folder(in_dir, country, db)
    finally:
        db.close()
    if not recs:
        print(f"取り込み対象の PDF/TXT が見つかりません: {in_dir}")
        return 0
    print(f"取り込み完了: {len(recs)} 件 (国={country})")
    return 0


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------
def cmd_extract(args: argparse.Namespace) -> int:
    db = PaperDB()
    rec = db.get(args.paper_id)
    if not rec:
        print(f"見つかりません: {args.paper_id}", file=sys.stderr)
        db.close()
        return 1
    text = ""
    if rec.local_pdf_path and Path(rec.local_pdf_path).is_file():
        res = extract_text(rec.local_pdf_path)
        text = res.text
        if res.error:
            print(f"抽出エラー: {res.error}")
    else:
        text = get_text_for(rec)

    if text:
        text_path = DATA_DIR / TEXT_DIR / f"{slugify(rec.paper_id)}.txt"
        text_path.write_text(text, encoding="utf-8")
        rec.local_text_path = str(text_path)
        rec.full_text_available = len(text) >= 200
        rec.text_sha256 = sha256_text(text)
        rec.original_language = detect_language(text)
        if not rec.title:
            tg = guess_title(text)
            if tg:
                rec.title = tg
                rec.normalized_title = normalize_title(tg)
        db.upsert(rec)
    print(f"{rec.paper_id}: 抽出 {len(text)} 文字, 言語={rec.original_language}")
    db.close()
    return 0


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------
def cmd_dedupe(args: argparse.Namespace) -> int:
    db = PaperDB()
    rec = db.get(args.paper_id)
    if not rec:
        print(f"見つかりません: {args.paper_id}", file=sys.stderr)
        db.close()
        return 1
    others = [r for r in db.all() if r.paper_id != rec.paper_id]
    outcome = apply_dedupe(rec, others, db)
    print(f"{rec.paper_id}: {outcome}" + (f" -> {rec.duplicate_of}" if rec.duplicate_of else ""))
    db.close()
    return 0


def cmd_dedupe_all(args: argparse.Namespace) -> int:
    db = PaperDB()
    if not db.all():
        print("レコードがありません。先に ingest してください。")
        db.close()
        return 0
    counts = dedupe_all_papers(db)
    db.close()
    print("重複チェック完了:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return 0


# ---------------------------------------------------------------------------
# screen
# ---------------------------------------------------------------------------
def cmd_screen(args: argparse.Namespace) -> int:
    db = PaperDB()
    rec = db.get(args.paper_id)
    if not rec:
        print(f"見つかりません: {args.paper_id}", file=sys.stderr)
        db.close()
        return 1
    text = get_text_for(rec)
    result = screen(rec, text)
    apply_screen(rec, db)
    print(f"{rec.paper_id}: {result.decision.value} (confidence={result.confidence})")
    print(f"  country={result.country_fit} workplace={result.workplace_fit} "
          f"generation={result.generation_fit} fulltext={result.fulltext_fit} "
          f"category={result.category_fit}")
    for r in result.reasons:
        print(f"  - {r}")
    for w in result.warnings:
        print(f"  ! {w}")
    db.close()
    return 0


def cmd_screen_all(args: argparse.Namespace) -> int:
    db = PaperDB()
    if not db.all():
        print("レコードがありません。先に ingest してください。")
        db.close()
        return 0
    counts = screen_all_papers(db)
    db.close()
    print("採否判定完了:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    return 0


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------
def cmd_clean(args: argparse.Namespace) -> int:
    db = PaperDB()
    rec = db.get(args.paper_id)
    if not rec:
        print(f"見つかりません: {args.paper_id}", file=sys.stderr)
        db.close()
        return 1
    text = get_text_for(rec)
    if not text:
        print(f"{rec.paper_id}: テキストがありません。先に extract してください。")
        db.close()
        return 1
    out = clean_to_file(rec, text)
    print(f"{rec.paper_id}: cleaned -> {out}")
    db.close()
    return 0


# ---------------------------------------------------------------------------
# prepare-translation
# ---------------------------------------------------------------------------
def cmd_prepare_translation(args: argparse.Namespace) -> int:
    db = PaperDB()
    rec = db.get(args.paper_id)
    if not rec:
        print(f"見つかりません: {args.paper_id}", file=sys.stderr)
        db.close()
        return 1
    text = get_text_for(rec)
    out = prepare_translation(rec, text, translate=args.translate)
    print(f"{rec.paper_id}: 翻訳準備 -> {out}")
    db.close()
    return 0


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def cmd_export(args: argparse.Namespace) -> int:
    out = export_inventory(fmt=args.format)
    print(f"台帳出力: {out}")
    return 0


# ---------------------------------------------------------------------------
# import-metadata
# ---------------------------------------------------------------------------
def cmd_import_metadata(args: argparse.Namespace) -> int:
    from .metadata_io import import_metadata

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"入力ファイルが見つかりません: {in_path}", file=sys.stderr)
        return 1
    try:
        stats = import_metadata(in_path)
    except Exception as exc:  # noqa: BLE001
        print(f"取り込み失敗: {exc}", file=sys.stderr)
        return 1
    print("メタデータ取り込み完了:")
    print(f"  読み込み行数      : {stats.rows_read}")
    print(f"  論文 更新レコード : {stats.records_updated} ({stats.fields_updated} フィールド)")
    print(f"  候補 更新レコード : {stats.candidates_updated} ({stats.candidate_fields_updated} フィールド)")
    if stats.skipped_missing_id:
        print(f"  DB未登録でスキップ: {len(stats.skipped_missing_id)} 件 "
              f"({', '.join(stats.skipped_missing_id[:5])}{' ...' if len(stats.skipped_missing_id) > 5 else ''})")
    for w in stats.warnings[:20]:
        print(f"  ! {w}")
    print("\n次に再判定を実行してください:")
    print("  python -m paper_agent dedupe-all")
    print("  python -m paper_agent screen-all")
    print("  python -m paper_agent report --full")
    print("  python -m paper_agent export --format xlsx")
    return 0


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def cmd_report(args: argparse.Namespace) -> int:
    from .reporter import build_console_summary, build_markdown, write_report

    db = PaperDB()
    records = db.all()
    db.close()
    if not records:
        print("レコードがありません。先に ingest / dedupe-all / screen-all を実行してください。")
        return 0

    if args.format in ("console", "both"):
        print(build_console_summary(records))
    if args.full:
        print()
        print(build_markdown(records))
    if args.format in ("md", "both"):
        out = write_report(fmt="md")
        print(f"\nMarkdown レポート: {out}")
    return 0


# ---------------------------------------------------------------------------
# harvest (候補収集)
# ---------------------------------------------------------------------------
def cmd_harvest(args: argparse.Namespace) -> int:
    from .harvester import harvest

    country = (args.country or "unknown").upper()
    cat = f"category_{args.category}" if args.category else None
    db = PaperDB()
    try:
        candidates = harvest(country, cat, limit=args.limit, db=db)
    finally:
        db.close()
    if not candidates:
        print("候補なし (ネットワーク/requests 未設定の可能性)。")
        return 0
    counts: dict[str, int] = {}
    for c in candidates:
        counts[c.candidate_status.value] = counts.get(c.candidate_status.value, 0) + 1
    print(f"harvest 完了: {len(candidates)} 件の候補を保存 (country={country}, category={cat})")
    for k, v in sorted(counts.items()):
        print(f"  candidate_status={k}: {v}")
    print("\n※ harvest 件数は N ではありません。PDF は取得していません。")
    print("※ candidate_score は人間確認の優先順位付け用で、自動採用はしません。")
    print("次: export → Excel の Candidates シートで approved_for_download を付ける → "
          "import-metadata → download-approved")
    return 0


# search は harvest の別名 (後方互換)
def cmd_search(args: argparse.Namespace) -> int:
    print("注: `search` は `harvest` に統合されました。harvest を実行します。")
    return cmd_harvest(args)


# ---------------------------------------------------------------------------
# download-approved (承認済み候補だけ PDF 取得)
# ---------------------------------------------------------------------------
def cmd_download_approved(args: argparse.Namespace) -> int:
    from .downloader import download_approved

    db = PaperDB()
    try:
        stats = download_approved(db)
    finally:
        db.close()
    print("download-approved 完了:")
    print(f"  承認済み候補       : {stats['approved']}")
    print(f"  ダウンロード成功   : {stats['downloaded']}")
    print(f"  スキップ(不適格)   : {stats['skipped']}")
    print(f"  失敗               : {stats['failed']}")
    if stats["downloaded"]:
        print("\nPDF は data/02_downloaded/<国>/ に保存しました。")
        print("※ ダウンロード成功は N ではありません。次の通常処理を通してください:")
        print("  python -m paper_agent ingest --input \"./data/02_downloaded/TH\" --country TH")
        print("  python -m paper_agent dedupe-all")
        print("  python -m paper_agent screen-all")
    return 0


# ---------------------------------------------------------------------------
# analysis-n (最終的に N に数える論文の集計)
# ---------------------------------------------------------------------------
def cmd_analysis_n(args: argparse.Namespace) -> int:
    from .analysis import format_summary

    db = PaperDB()
    records = db.all()
    db.close()
    print(format_summary(records))
    return 0


# ---------------------------------------------------------------------------
# autopilot (エージェント型ワークフロー)
# ---------------------------------------------------------------------------
def cmd_autopilot(args: argparse.Namespace) -> int:
    from .agents import AutopilotConfig, SupervisorAgent

    ensure_dirs()
    countries = [c.strip().upper() for c in (args.countries or "TH,VN").split(",") if c.strip()]
    categories = [int(c.strip()) for c in str(args.categories or "1,2,3,4,5,6").split(",") if c.strip()]
    config = AutopilotConfig(
        target_n=args.target_n,
        countries=countries,
        categories=categories,
        per_query_limit=args.per_query_limit,
        dry_run=args.dry_run,
        qa_strict=args.qa_strict,
        max_rounds=args.max_rounds,
    )
    mode = "DRY-RUN (PDFは取得しない)" if args.dry_run else "通常実行 (PDF取得あり)"
    print(f"autopilot 開始: {mode}")
    print(f"  target_N={config.target_n} countries={countries} categories={categories} "
          f"per_query_limit={config.per_query_limit}")

    db = PaperDB()
    try:
        supervisor = SupervisorAgent(config)
        result = supervisor.run(db)
    finally:
        db.close()

    print("\n=== autopilot 終了 ===")
    print(f"停止理由: {result.stop_reason}")
    print(f"最終 accepted N: {result.final_n}")
    if config.target_n > result.final_n:
        print(f"※ 目標 {config.target_n} に未達 ({config.target_n - result.final_n} 本不足)。"
              "水増しせず、条件に合う論文だけを N にしています。")

    # QA 不整合の表示 (qa-strict)
    total_demoted = sum(len(qa.demoted) for qa in result.qa_reports)
    if total_demoted:
        print(f"QA: 不適合な accepted を {total_demoted} 件 needs_review に降格し N から除外しました。")
        if args.qa_strict:
            last_failures = result.qa_reports[-1].failures if result.qa_reports else []
            for c in last_failures:
                print(f"  ! QA[{c.num}] {c.name}: {c.offending[:5]}")
    print(f"\n出力:")
    print(f"  {result.summary_path}")
    print(f"  {result.qa_report_path}")
    print(f"  {DATA_DIR/'10_exports'/'paper_inventory.xlsx'}")
    print(f"  {DATA_DIR/'10_exports'/'report.md'}")
    print(f"  {DATA_DIR/'10_exports'/'accepted_for_analysis.csv'} ほか CSV")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m paper_agent",
        description="タイ・ベトナム多世代職場研究の論文収集・重複判定・前処理エージェント",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="プロジェクト初期化").set_defaults(func=cmd_init)

    sp = sub.add_parser("ingest", help="フォルダから PDF/TXT を取り込み")
    sp.add_argument("--input", required=True, help="入力フォルダ")
    sp.add_argument("--country", default="unknown", help="国コード TH/VN/JP")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("extract", help="テキスト抽出")
    sp.add_argument("--paper-id", required=True)
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("dedupe", help="重複チェック (1件)")
    sp.add_argument("--paper-id", required=True)
    sp.set_defaults(func=cmd_dedupe)

    sub.add_parser("dedupe-all", help="重複チェック (全件)").set_defaults(func=cmd_dedupe_all)

    sp = sub.add_parser("screen", help="採否判定 (1件)")
    sp.add_argument("--paper-id", required=True)
    sp.set_defaults(func=cmd_screen)

    sub.add_parser("screen-all", help="採否判定 (全件)").set_defaults(func=cmd_screen_all)

    sp = sub.add_parser("clean", help="KH Coder 用クリーニング")
    sp.add_argument("--paper-id", required=True)
    sp.set_defaults(func=cmd_clean)

    sp = sub.add_parser("prepare-translation", help="翻訳準備")
    sp.add_argument("--paper-id", required=True)
    sp.add_argument("--translate", action="store_true", help="APIキーがあれば自動翻訳する")
    sp.set_defaults(func=cmd_prepare_translation)

    sp = sub.add_parser("export", help="台帳出力")
    sp.add_argument("--format", default="xlsx", choices=["xlsx", "csv"])
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("import-metadata",
                        help="編集済み Excel/CSV からメタデータを取り込み DB を更新")
    sp.add_argument("--input", required=True, help="編集した xlsx または csv のパス")
    sp.set_defaults(func=cmd_import_metadata)

    sp = sub.add_parser("report", help="重複判定・採否の確認レポート (コンソール/Markdown)")
    sp.add_argument("--format", default="both", choices=["console", "md", "both"],
                    help="console=要約のみ / md=Markdownファイル / both=両方 (既定)")
    sp.add_argument("--full", action="store_true", help="コンソールにも全文 (区分別一覧) を表示")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("analysis-n", help="最終的に N に数える論文を集計")
    sp.set_defaults(func=cmd_analysis_n)

    sp = sub.add_parser("autopilot", help="エージェント型ワークフローで候補収集〜N確保を自律実行")
    sp.add_argument("--target-n", type=int, default=100, help="目標 accepted N (既定100)")
    sp.add_argument("--countries", default="TH,VN", help="対象国 (カンマ区切り)")
    sp.add_argument("--categories", default="1,2,3,4,5,6", help="カテゴリ (カンマ区切り)")
    sp.add_argument("--per-query-limit", type=int, default=50, help="1クエリあたりの取得上限")
    sp.add_argument("--dry-run", action="store_true", help="PDFを取得せず候補収集まで")
    sp.add_argument("--max-rounds", type=int, default=None, help="最大ラウンド数")
    sp.add_argument("--qa-strict", action="store_true", help="QA不整合をエラーとして表示 (該当はNから除外)")
    sp.set_defaults(func=cmd_autopilot)

    sp = sub.add_parser("harvest", help="候補をOpenAlex/Crossrefから収集 (PDFは取得しない)")
    sp.add_argument("--country", default="TH", help="国コード TH/VN")
    sp.add_argument("--category", type=int, default=None, help="1..6")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_harvest)

    sp = sub.add_parser("download-approved",
                        help="approved_for_download の候補だけ PDF を取得")
    sp.set_defaults(func=cmd_download_approved)

    sp = sub.add_parser("search", help="(非推奨) harvest の別名")
    sp.add_argument("--country", default="TH")
    sp.add_argument("--category", type=int, default=None, help="1..6")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_search)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        logger.error("コマンド実行エラー: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
