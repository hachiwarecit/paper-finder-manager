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
from .duplicate_checker import best_match
from .exporter import export as export_inventory
from .extractor import extract_text
from .models import DuplicateType, PaperRecord, ScreeningStatus
from .screener import (
    assess_country,
    assess_generations,
    detected_generation_groups,
    enrich_record,
    screen,
)
from .translator_prep import prepare_translation
from .utils import (
    DATA_DIR,
    detect_language,
    ensure_dirs,
    get_logger,
    normalize_title,
    sha256_file,
    sha256_text,
    slugify,
)

logger = get_logger()

COUNTRY_NAME = {"TH": "Thailand", "VN": "Vietnam", "JP": "Japan"}
TEXT_DIR = "03_screening"


# ---------------------------------------------------------------------------
# テキスト取得ヘルパ
# ---------------------------------------------------------------------------
def get_text_for(rec: PaperRecord) -> str:
    """レコードの抽出テキストを返す。text パスが無ければ PDF から抽出する。"""
    if rec.local_text_path and Path(rec.local_text_path).is_file():
        try:
            return Path(rec.local_text_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if rec.local_pdf_path and Path(rec.local_pdf_path).is_file():
        res = extract_text(rec.local_pdf_path)
        return res.text
    return ""


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
def _make_paper_id(stem: str, country: str, db: PaperDB) -> str:
    base = slugify(stem, max_len=40) or "paper"
    pid = f"{country}-{base}"
    if not db.exists(pid):
        return pid
    i = 2
    while db.exists(f"{pid}-{i}"):
        i += 1
    return f"{pid}-{i}"


def cmd_ingest(args: argparse.Namespace) -> int:
    ensure_dirs()
    in_dir = Path(args.input)
    if not in_dir.exists():
        print(f"入力フォルダが存在しません: {in_dir}", file=sys.stderr)
        return 1

    country = (args.country or "unknown").upper()
    db = PaperDB()
    text_out_dir = DATA_DIR / TEXT_DIR
    text_out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(
        p for p in in_dir.rglob("*") if p.suffix.lower() in (".pdf", ".txt", ".text", ".md")
    )
    if not files:
        print(f"取り込み対象の PDF/TXT が見つかりません: {in_dir}")
        db.close()
        return 0

    ingested = 0
    for path in files:
        try:
            pid = _make_paper_id(path.stem, country, db)
            res = extract_text(path)
            text = res.text or ""
            full_text = bool(text and len(text) >= 200)

            # 抽出テキストを 03_screening に保存
            text_path = None
            if text:
                text_path = text_out_dir / f"{slugify(pid)}.txt"
                text_path.write_text(text, encoding="utf-8")

            is_pdf = path.suffix.lower() == ".pdf"
            rec = PaperRecord(
                paper_id=pid,
                country=country if country in COUNTRY_NAME else "unknown",
                title="",  # 後で extract/screen がタイトル推定
                authors="",
                source_name="local_ingest",
                local_pdf_path=str(path) if is_pdf else None,
                local_text_path=str(text_path) if text_path else (str(path) if not is_pdf else None),
                original_language=detect_language(text),
                full_text_available=full_text,
                pdf_sha256=sha256_file(path) if is_pdf else None,
                text_sha256=sha256_text(text),
                screening_status=ScreeningStatus.candidate,
                notes=f"ingested from {path.name}"
                + (f"; extract_error={res.error}" if res.error else ""),
            )
            # タイトル推定 (本文先頭から)
            from .extractor import guess_title

            title_guess = guess_title(text)
            if title_guess:
                rec.title = title_guess
                rec.normalized_title = normalize_title(title_guess)
            db.upsert(rec)
            ingested += 1
            if res.error:
                logger.warning("%s: 抽出に問題 (%s)", pid, res.error)
        except Exception as exc:  # noqa: BLE001
            logger.error("取り込み失敗 %s: %s", path.name, exc)

    db.close()
    print(f"取り込み完了: {ingested} 件 (国={country})")
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
        from .extractor import guess_title

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
def _apply_dedupe(rec: PaperRecord, others: list[PaperRecord], db: PaperDB) -> str:
    # 重複判定の前に、対象国・世代区分・言語などをテキストから補っておく
    enrich_record(rec, get_text_for(rec))
    result = best_match(rec, others)
    if result.duplicate_type == DuplicateType.not_duplicate:
        db.upsert(rec)  # enrich_record で補ったメタデータを保存
        return "not_duplicate"

    matched = db.get(result.matched_paper_id) if result.matched_paper_id else None
    group = (matched.duplicate_group_id if matched and matched.duplicate_group_id
             else result.matched_paper_id)
    rec.duplicate_group_id = group
    rec.duplicate_of = result.matched_paper_id
    rec.duplicate_confidence = result.confidence

    if result.duplicate_type in (DuplicateType.exact_duplicate, DuplicateType.probable_duplicate):
        rec.screening_status = ScreeningStatus.duplicate
        rec.rejection_reason = result.explanation
    elif result.duplicate_type == DuplicateType.same_dataset_possible:
        rec.screening_status = ScreeningStatus.needs_review
        rec.same_dataset_warning = True
        rec.notes = (rec.notes + " | " if rec.notes else "") + result.explanation
    db.upsert(rec)
    return result.duplicate_type.value


def cmd_dedupe(args: argparse.Namespace) -> int:
    db = PaperDB()
    rec = db.get(args.paper_id)
    if not rec:
        print(f"見つかりません: {args.paper_id}", file=sys.stderr)
        db.close()
        return 1
    others = [r for r in db.all() if r.paper_id != rec.paper_id]
    outcome = _apply_dedupe(rec, others, db)
    print(f"{rec.paper_id}: {outcome}" + (f" -> {rec.duplicate_of}" if rec.duplicate_of else ""))
    db.close()
    return 0


def cmd_dedupe_all(args: argparse.Namespace) -> int:
    db = PaperDB()
    records = db.all()
    if not records:
        print("レコードがありません。先に ingest してください。")
        db.close()
        return 0
    processed: list[PaperRecord] = []
    counts = {"exact_duplicate": 0, "probable_duplicate": 0, "same_dataset_possible": 0, "not_duplicate": 0}
    for rec in records:
        try:
            outcome = _apply_dedupe(rec, processed, db)
            counts[outcome] = counts.get(outcome, 0) + 1
        except Exception as exc:  # noqa: BLE001
            logger.error("dedupe 失敗 %s: %s", rec.paper_id, exc)
        processed.append(db.get(rec.paper_id) or rec)
    db.close()
    print("重複チェック完了:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return 0


# ---------------------------------------------------------------------------
# screen
# ---------------------------------------------------------------------------
def _apply_screen(rec: PaperRecord, db: PaperDB) -> str:
    text = get_text_for(rec)
    result = screen(rec, text)

    # メタデータをレコードに反映
    text_low = text.lower()
    _, detected_country, _ = assess_country(rec, text_low)
    if detected_country in ("thailand", "vietnam"):
        rec.target_country = detected_country.capitalize()
    gen_fit, n_gen, _, _ = assess_generations(text_low)
    # 検出された世代グループ名を記録
    rec.generation_groups = "; ".join(detected_generation_groups(text_low))
    rec.number_of_generations = n_gen
    rec.document_type = result.document_type
    rec.original_language = detect_language(text)
    rec.full_text_available = result.fulltext_fit
    if result.category_fit != "unknown":
        rec.category = result.category_fit
    if result.translation_required:
        rec.analysis_language = "en"  # 最終分析は英語

    # dedupe で確定済みの duplicate / same_dataset(needs_review+warning) は維持
    dedupe_locked = rec.screening_status == ScreeningStatus.duplicate or rec.same_dataset_warning
    if not dedupe_locked:
        rec.screening_status = result.decision
        rec.rejection_reason = "" if result.decision == ScreeningStatus.accepted else result.primary_reason
    else:
        # 重複側にも screening の根拠を notes として残す
        note = f"[screen] decision={result.decision.value}; cat={result.category_fit}"
        rec.notes = (rec.notes + " | " if rec.notes else "") + note

    if result.warnings:
        rec.notes = (rec.notes + " | " if rec.notes else "") + " ; ".join(result.warnings)

    db.upsert(rec)
    return rec.screening_status.value


def cmd_screen(args: argparse.Namespace) -> int:
    db = PaperDB()
    rec = db.get(args.paper_id)
    if not rec:
        print(f"見つかりません: {args.paper_id}", file=sys.stderr)
        db.close()
        return 1
    text = get_text_for(rec)
    result = screen(rec, text)
    _apply_screen(rec, db)
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
    records = db.all()
    if not records:
        print("レコードがありません。先に ingest してください。")
        db.close()
        return 0
    counts: dict[str, int] = {}
    for rec in records:
        try:
            outcome = _apply_screen(rec, db)
            counts[outcome] = counts.get(outcome, 0) + 1
        except Exception as exc:  # noqa: BLE001
            logger.error("screen 失敗 %s: %s", rec.paper_id, exc)
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
    print(f"  読み込み行数 : {stats.rows_read}")
    print(f"  更新レコード : {stats.records_updated}")
    print(f"  更新フィールド: {stats.fields_updated}")
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
# search
# ---------------------------------------------------------------------------
def cmd_search(args: argparse.Namespace) -> int:
    from .search_openalex import search as oa_search

    country = (args.country or "unknown").upper()
    cat = f"category_{args.category}" if args.category else "unknown"
    records = oa_search(args.query or _default_query(country), country=country,
                        category=cat, limit=args.limit)
    if not records:
        print("検索結果なし (ネットワーク/requests 未設定の可能性)。")
        return 0
    db = PaperDB()
    saved = 0
    for rec in records:
        # paper_id 衝突回避
        if db.exists(rec.paper_id):
            continue
        db.upsert(rec)
        saved += 1
    db.close()
    print(f"検索: {len(records)} 件取得, {saved} 件を candidate として保存。")
    print("※ 検索結果は自動採用しません。必ず人間が確認してください。")
    return 0


def _default_query(country: str) -> str:
    name = COUNTRY_NAME.get(country, country)
    return f"{name} generational differences workplace employees"


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

    sp = sub.add_parser("search", help="OpenAlex 検索 (candidate 保存)")
    sp.add_argument("--country", default="TH")
    sp.add_argument("--category", type=int, default=None, help="1..6")
    sp.add_argument("--query", default=None)
    sp.add_argument("--limit", type=int, default=20)
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
