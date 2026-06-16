"""PDF ファイル名の重複チェック（名前だけを見る軽量版）。

集めた PDF の中に **同じファイル名** が複数ないかを調べるだけのツール。
ハッシュ計算も本文テキスト抽出もしない。ファイル名 (basename) が一致する
ものを「重複の疑い」としてレポートするだけで、**元の PDF は一切移動・削除
しない**（organize_pdfs / preprocess と同じ「元は消さない」安全方針）。

判定: ファイル名が同じか。
    既定 … 完全一致（拡張子の大文字小文字だけは無視: .pdf == .PDF）
    --ignore-case … 名前全体の大文字小文字も無視（X.pdf == x.pdf）

辞書分析用に N≈100 を集める途中で、別ソースから落とした同名 PDF や
コピーの取り違えを早めに見つけるための簡易チェッカー。

使い方:
    # 既定（pdfs/ と organized_pdfs/ を再帰スキャン）
    python scripts/check_duplicate_pdfs.py

    # フォルダを指定（複数指定可）
    python scripts/check_duplicate_pdfs.py --input pdfs
    python scripts/check_duplicate_pdfs.py --input pdfs --input /path/to/folder

    # 大文字小文字を無視して比較
    python scripts/check_duplicate_pdfs.py --ignore-case
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from common import ORGANIZED_PDF_DIR, PDF_DIR, REPORTS_DIR

DUP_REPORT = REPORTS_DIR / "duplicate_pdf_names.csv"
REPORT_COLUMNS = ["group", "file_name", "count", "path"]


# --------------------------------------------------------------------------
# スキャン
# --------------------------------------------------------------------------
def find_pdfs(roots: List[Path]) -> List[Path]:
    """roots 以下を再帰スキャンして PDF ファイルのパス一覧を返す。

    拡張子は大文字小文字を問わず .pdf を対象。同じ実ファイル（重複指定や
    シンボリックリンク）は二重に数えない。
    """
    pdfs: List[Path] = []
    seen: set = set()
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() == ".pdf":
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    pdfs.append(p)
    return pdfs


def group_by_name(pdfs: List[Path], *, ignore_case: bool) -> Dict[str, List[Path]]:
    """ファイル名をキーにまとめる。ignore_case なら名前全体を小文字化。"""
    groups: Dict[str, List[Path]] = defaultdict(list)
    for p in pdfs:
        key = p.name.lower() if ignore_case else p.name
        groups[key].append(p)
    return groups


def duplicate_groups(groups: Dict[str, List[Path]]) -> List[List[Path]]:
    """2件以上ある（=名前が重複している）グループだけをキー順で返す。"""
    return [groups[k] for k in sorted(groups) if len(groups[k]) > 1]


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------
def build_rows(dups: List[List[Path]]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for i, paths in enumerate(dups, start=1):
        count = len(paths)
        for p in sorted(paths, key=str):
            rows.append({
                "group": i,
                "file_name": p.name,
                "count": count,
                "path": str(p),
            })
    return rows


def write_report(rows: List[Dict[str, object]]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(DUP_REPORT, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_console(total: int, dups: List[List[Path]]) -> None:
    redundant = sum(len(paths) for paths in dups) - len(dups)  # 余分なファイル数
    print(f"PDF を {total} 件スキャンしました。")
    if not dups:
        print("同じ名前の PDF はありませんでした。✅")
        return
    print(f"名前が重複しているグループ: {len(dups)} 件 "
          f"（うち余分なファイル: {redundant} 件）\n")
    for i, paths in enumerate(dups, start=1):
        print(f"■ [{i}] {paths[0].name}  (x{len(paths)})")
        for p in sorted(paths, key=str):
            print(f"    - {p}")
    print()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _default_roots() -> List[Path]:
    """既定スキャン対象: 存在する pdfs/ と organized_pdfs/。"""
    return [d for d in (PDF_DIR, ORGANIZED_PDF_DIR) if d.exists()]


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="PDF ファイル名の重複（同名）をチェックする（名前だけを見る）")
    ap.add_argument("--input", action="append", default=None, metavar="DIR",
                    help="スキャンするフォルダ（複数指定可）。"
                         "既定: pdfs/ と organized_pdfs/")
    ap.add_argument("--ignore-case", action="store_true",
                    help="ファイル名全体の大文字小文字を無視して比較する")
    return ap.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    roots = [Path(p) for p in args.input] if args.input else _default_roots()

    if not roots:
        print("スキャン対象のフォルダがありません "
              "(pdfs/ も organized_pdfs/ も無く、--input も未指定)。")
        return 0

    print("スキャン対象: " + ", ".join(str(r) for r in roots))
    pdfs = find_pdfs(roots)
    if not pdfs:
        print("PDF が見つかりませんでした。")
        return 0

    groups = group_by_name(pdfs, ignore_case=args.ignore_case)
    dups = duplicate_groups(groups)
    rows = build_rows(dups)
    write_report(rows)

    print_console(len(pdfs), dups)
    print(f"レポート: {DUP_REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
