#!/usr/bin/env python3
"""scripts/viewer_template.html に data/quiz.json を埋め込んで dist/index.html を出力する。

出力は単一ファイル。外部通信・CDN・Web フォントは一切使わないので、
file:// で開いてもオフラインでそのまま動く。

使い方:
    python3 scripts/build_viewer.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER = "__QUIZ_DATA__"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", default="scripts/viewer_template.html", type=Path)
    ap.add_argument("--quiz", default="data/quiz.json", type=Path)
    ap.add_argument("--output", default="dist/index.html", type=Path)
    ap.add_argument("--pages", default="docs/index.html", type=Path,
                    help="GitHub Pages 用の複製先。--pages '' で無効化")
    args = ap.parse_args()
    if args.pages and str(args.pages) in ("", "."):
        args.pages = None

    for p in (args.template, args.quiz):
        if not p.exists():
            print(f"{p} が無い。先に build_quiz.py まで実行すること。", file=sys.stderr)
            return 1

    template = args.template.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        print(f"テンプレートに {PLACEHOLDER} が無い。", file=sys.stderr)
        return 1

    data = json.loads(args.quiz.read_text(encoding="utf-8"))
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # <script> の中に素で置くため、終了タグと見なされ得る並びだけ無害化する
    blob = blob.replace("</", "<\\/")

    html = template.replace(PLACEHOLDER, blob)

    # 外部参照が紛れ込んでいないか確認する (オフライン動作が要件のため)
    external = re.findall(r'(?:src|href)\s*=\s*["\'](?!#)([^"\']+)', html)
    remote = [u for u in external if re.match(r"^(https?:)?//|^data:image", u)]
    if remote:
        print("外部参照が残っている:", remote, file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    kb = args.output.stat().st_size / 1024
    print(f"出力: {args.output} ({kb:.0f} KB)")

    # GitHub Pages は「ブランチ + フォルダ」方式ではルートか /docs しか選べない。
    # dist/ は指定できないので、同じものを docs/ にも置く。
    if args.pages:
        args.pages.parent.mkdir(parents=True, exist_ok=True)
        args.pages.write_text(html, encoding="utf-8")
        print(f"      {args.pages} (GitHub Pages 用の同一ファイル)")

    print(f"  用語 {data['meta']['terms']} / 問題 {data['meta']['questions']}")
    print("  外部参照: なし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
