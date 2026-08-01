#!/usr/bin/env python3
"""IPA 応用情報技術者試験シラバス (Ver.7.2) から用語を構造化抽出する。

入力は IPA 公開のシラバス。PDF 版を Word (.docx) に変換したものを想定する
(PDF 直読みは段組み・テキストボックスの復元が不安定なため .docx を正とする)。

出力: data/terms.json

    {
      "id": "t0142",
      "term": "スループット",
      "reading": "",
      "abbr_full": "",
      "category": "コンピュータ構成要素",     # 中分類 (23 個)
      "subcategory": "システムの評価指標",     # 小分類
      "description": ""                       # シラバスに用語ごとの説明文は無い
    }

補助フィールド (Phase 2 の誤答選択肢生成で使う):
    major     大分類
    topic     項目 (小分類の下、シラバス本文の見出し)
    subtopic  項目の下の枝 (「① 確率」等)。誤答選択肢を最も狭い箱から取るために使う。

項目ごとの「内容」文 (IPA 原文) は data/topics.json に分けて出力する。
用語ごとに持たせると同じ文が何十回も重複して JSON が肥大するため。

原文は改変しない。要約・整形・生成は一切行わない。

使い方:
    python3 scripts/extract.py [--input input/syllabus_ap_ver7_2.docx]
                               [--output data/terms.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter, OrderedDict
from dataclasses import dataclass, asdict
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

# 中分類 / 大分類 の目次行
RE_MAJOR = re.compile(r"^大分類\s*(\d+)\s*[：:]\s*(.+?)\s*$")
RE_MIDDLE = re.compile(r"^中分類\s*(\d+)\s*[：:]\s*(.+?)(\d+)?\s*$")
# 目次の小分類行は末尾にページ番号が付く
RE_TOC_LEAF = re.compile(r"^(.+?)(\d{1,3})$")
# 「内容」文の終端 (用語例の羅列と区別するための唯一の手掛かり)
RE_CONTENT_END = re.compile(r"[。．]\s*$")
# ①② などの枝番マーカー (小分類の下の枝。用語例の切れ目でもある)
RE_MARKER = re.compile(r"^[①-⑳]\s*")
# (ⅰ)(ⅱ) … の手順列挙。これは「内容」側の構造で用語ではない。
RE_ROMAN = re.compile(r"^[（(][ⅰ-ⅹⅠ-Ⅹ]")
# 略語展開: ABBR（Full Name：和訳）
RE_ABBR = re.compile(r"^(.+?)（([^（）]*[A-Za-z][^（）]*)）$")
# 和文どうしに挟まれた空白 (PDF の行折り返しの跡)
RE_JP_GAP = re.compile(r"(?<=[ぁ-んァ-ヴ一-龥ー々〆]) +(?=[ぁ-んァ-ヴ一-龥ー々〆])")

# PDF→Word 変換で本文側の文字が落ちた箇所の個別修正。
# 原文の字面に戻すだけで、説明の追加や書き換えは行わない。
CORRECTIONS = {
    "ベロシテ": "ベロシティ",
}


# --------------------------------------------------------------------------
# docx 読み出し
# --------------------------------------------------------------------------

def _para_text(p_el) -> str:
    """w:p のテキスト。ルビ (w:rt) と入れ子テキストボックスは除く。"""
    out: list[str] = []

    def walk(node):
        for ch in node:
            tag = ch.tag
            if tag in (MC + "Fallback", W + "rt", W + "txbxContent"):
                continue
            if tag == W + "t":
                out.append(ch.text or "")
            else:
                walk(ch)

    walk(p_el)
    return "".join(out).strip()


def load_paragraphs(docx_path: Path) -> list[tuple[str, str]]:
    """(スタイル名, テキスト) の並び。

    このシラバスは PDF→Word 変換の産物で、同じ内容が本文とテキストボックスに
    2〜3 重に現れる。本文ストーリーのみを採用して重複を落とす。
    """
    with zipfile.ZipFile(docx_path) as z:
        root = ET.fromstring(z.read("word/document.xml"))

    mains: list = []

    def collect(node, inside_textbox: bool):
        for ch in node:
            if ch.tag == MC + "Fallback":
                continue
            if ch.tag == W + "txbxContent":
                collect(ch, True)
                continue
            if ch.tag == W + "p":
                if not inside_textbox:
                    mains.append(ch)
                collect(ch, inside_textbox)
                continue
            collect(ch, inside_textbox)

    body = root.find(W + "body")
    if body is None:
        raise SystemExit("document.xml に w:body が無い。docx が壊れている可能性がある。")
    collect(body, False)

    paragraphs: list[tuple[str, str]] = []
    for el in mains:
        style = ""
        p_pr = el.find(W + "pPr")
        if p_pr is not None:
            st = p_pr.find(W + "pStyle")
            if st is not None:
                style = st.get(W + "val") or ""
        paragraphs.append((style, _para_text(el)))
    return paragraphs


# --------------------------------------------------------------------------
# 目次から 大分類 / 中分類 / 小分類 の階層を得る
# --------------------------------------------------------------------------

@dataclass
class Leaf:
    major: str
    category: str
    subcategory: str


def parse_toc(paragraphs: list[tuple[str, str]]) -> list[Leaf]:
    """目次を読み、小分類を出現順に返す。

    本文側の Heading1 (小分類見出し) と順番で突き合わせるため、順序が意味を持つ。
    """
    try:
        start = next(i for i, (_, t) in enumerate(paragraphs) if t == "目次")
    except StopIteration:
        raise SystemExit("目次が見つからない。シラバスの構造が想定と違う。")

    leaves: list[Leaf] = []
    major = category = ""
    for style, text in paragraphs[start + 1 :]:
        if not text:
            continue
        m = RE_MAJOR.match(text)
        if m:
            major = m.group(2).strip()
            continue
        m = RE_MIDDLE.match(text)
        if m:
            category = m.group(2).strip()
            continue
        if style not in ("ListParagraph", "TOC4"):
            # 目次を抜けて本文に入った
            if style in ("Heading1", "Heading2"):
                break
            continue
        if text in ("はじめに", "シラバスの構成"):
            continue
        m = RE_TOC_LEAF.match(text)
        name = (m.group(1) if m else text).strip()
        if not name or not category:
            continue
        leaves.append(Leaf(major=major, category=category, subcategory=name))
    return leaves


# --------------------------------------------------------------------------
# 用語列の分解
# --------------------------------------------------------------------------

def split_terms(chunk: str) -> list[str]:
    """読点区切りの用語列を分解する。括弧の内側の読点では切らない。

    例) 回帰分析（単回帰分析，重回帰分析）  -> 1 語として扱う
        仮説検定（… p 値（有意確率），…）  -> 入れ子も保つ
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in chunk:
        if ch in "（(":
            depth += 1
            buf.append(ch)
        elif ch in "）)":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch in "，、,) " and False:
            pass
        elif ch in "，、," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _balance_parens(t: str) -> str:
    """行分割で生じた不揃いな括弧を落とす。中身は触らない。"""
    opens = t.count("（") + t.count("(")
    closes = t.count("）") + t.count(")")
    while closes > opens and t and t[-1] in "）)":
        t = t[:-1].rstrip()
        closes -= 1
    while opens > closes and t and t[0] in "（(":
        t = t[1:].lstrip()
        opens -= 1
    return t


def clean_term(raw: str) -> tuple[str, str]:
    """(term, abbr_full) を返す。原文の字面は保つ。"""
    t = raw.strip().strip("・")
    t = RE_MARKER.sub("", t).strip()
    t = re.sub(r"\s+", " ", t)
    # PDF の行折り返しで語の途中に空白が入っている ("ベンチマーキン グ")。
    # 和文どうしに挟まれた空白だけを詰める。"パック 10 進数" のように
    # 英数字が絡む空白は原文の表記なので触らない。
    t = RE_JP_GAP.sub("", t)
    t = _balance_parens(t)
    t = CORRECTIONS.get(t, t)
    m = RE_ABBR.match(t)
    if m:
        head = m.group(1).strip()
        inner = m.group(2).strip()
        # 英字を含む括弧内のみ略語展開とみなす (「（あふれ）」等は別名なので残す)
        if head and re.search(r"[A-Za-z]", inner):
            return head, inner
    return t, ""


def is_term_like(term: str) -> bool:
    """用語として採用してよいか。"""
    if not term or len(term) > 80:
        return False
    if RE_CONTENT_END.search(term):
        return False
    if re.fullmatch(r"[-–—・\d\s.,:：;／/]+", term):
        return False
    if re.fullmatch(r"-?\s*[ivxIVX]+\s*-?", term):  # ページ番号 -ii- 等
        return False
    if RE_ROMAN.match(term):                        # (ⅱ)現行業務の調査・分析
        return False
    # 「〜を理解する」「〜を修得し」等は内容文の取りこぼし
    if re.search(r"(理解する|修得し|応用する|適用する|把握する)", term):
        return False
    # 用語ではなく文の断片 (助詞を伴って長い)
    if len(term) > 20 and re.search(r"(が|を|に|は|など)[^（）]*$", term) and "，" not in term:
        if re.search(r"(のように|かなど|ことなど|における.+など)", term):
            return False
    return True


# --------------------------------------------------------------------------
# 本文の走査
# --------------------------------------------------------------------------

@dataclass
class Term:
    id: str
    term: str
    reading: str
    abbr_full: str
    category: str
    subcategory: str
    description: str
    major: str
    topic: str
    subtopic: str
    syllabus_content: str


@dataclass
class Report:
    total: int
    by_category: "OrderedDict[str, int]"
    by_major: "OrderedDict[str, int]"
    empty_description: int
    subcategories_seen: int
    subcategories_in_toc: int
    unmatched_headings: list
    topics_without_terms: list
    duplicates: int


def extract(paragraphs: list[tuple[str, str]], leaves: list[Leaf]):
    body_start = 0
    for i, (style, text) in enumerate(paragraphs):
        if style == "Heading1" and text not in ("はじめに", "シラバスの構成"):
            body_start = i
            break

    leaf_by_name: dict[str, list[Leaf]] = {}
    for lf in leaves:
        leaf_by_name.setdefault(lf.subcategory, []).append(lf)

    terms: list[Term] = []
    unmatched: list[str] = []
    topics_without_terms: list[str] = []
    seen_subcats: "OrderedDict[str, None]" = OrderedDict()
    leaf_cursor = 0

    cur: Leaf | None = None
    topic = ""
    subtopic = ""
    content_lines: list[str] = []
    pending: list[tuple[str, str]] = []   # (subtopic, 用語例チャンク)

    def flush_topic():
        """1 つの項目 (Heading2 / 本文 ListParagraph) 分を確定する。"""
        nonlocal pending, content_lines
        if cur is None:
            pending = []
            content_lines = []
            return
        content = " ".join(content_lines).strip()
        got = False
        for sub, group in pending:
            for raw in split_terms(group):
                term_text, abbr = clean_term(raw)
                if not is_term_like(term_text):
                    continue
                terms.append(
                    Term(
                        id="",
                        term=term_text,
                        reading="",
                        abbr_full=abbr,
                        category=cur.category,
                        subcategory=cur.subcategory,
                        description="",
                        major=cur.major,
                        topic=topic,
                        subtopic=sub,
                        syllabus_content=content,
                    )
                )
                got = True
        if topic and not got:
            topics_without_terms.append(f"{cur.category} / {cur.subcategory} / {topic}")
        pending = []
        content_lines = []

    # 論理行バッファ。PDF→Word 変換で 1 文が複数段落に折り返されているため、
    # 「。」で終わるまで連結してから内容/用語例を判定する。
    line_buf: list[str] = []

    def flush_line():
        """溜めた論理行を内容 or 用語例に振り分ける。"""
        nonlocal line_buf
        if not line_buf:
            return
        joined = "".join(line_buf)
        line_buf = []
        if not joined.strip():
            return
        if RE_CONTENT_END.search(joined) or RE_ROMAN.match(joined):
            content_lines.append(joined)
        else:
            pending.append((subtopic, joined))

    for style, text in paragraphs[body_start:]:
        # 本文中の項目見出しは Heading2 が正だが、28 件が ListParagraph に落ちている
        is_topic_heading = style == "Heading2" or (style == "ListParagraph" and text)

        if style == "Heading1":
            flush_line()
            flush_topic()
            topic = ""
            subtopic = ""
            name = text.strip()
            # 本文の Heading1 は小分類。目次の並びと順に突き合わせる。
            matched = None
            for k in range(leaf_cursor, min(leaf_cursor + 4, len(leaves))):
                if leaves[k].subcategory == name:
                    matched = leaves[k]
                    leaf_cursor = k + 1
                    break
            if matched is None and name in leaf_by_name:
                matched = leaf_by_name[name][0]
            if matched is None:
                # 【目標】文と見出しが結合しているケース: 末尾の語を見出しとみなす
                tail = name.split("。")[-1].strip()
                if tail in leaf_by_name:
                    matched = leaf_by_name[tail][0]
            if matched is None:
                if name and not name.startswith("【目標】"):
                    unmatched.append(name[:60])
                continue
            cur = matched
            seen_subcats[cur.subcategory] = None
            continue

        if is_topic_heading:
            flush_line()
            flush_topic()
            topic = text.strip()
            subtopic = ""
            continue

        if style != "BodyText":
            continue
        if not text:
            continue          # 空行は折り返しを分断しない

        # 「① 確率」「② 統計」— 項目の下の枝。用語例の切れ目でもある。
        if RE_MARKER.match(text):
            flush_line()
            label = RE_MARKER.sub("", text).strip()
            # マーカー行が長い場合は枝の見出しではなく本文が続いている
            if len(label) <= 40:
                subtopic = label
                continue
            subtopic = ""
            text = label

        line_buf.append(text)
        if RE_CONTENT_END.search(text):
            flush_line()

    flush_line()
    flush_topic()

    # 重複 (同じ用語が同じ小分類に 2 回出る) を除去し、ID を振る
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Term] = []
    dup = 0
    for t in terms:
        key = (t.term, t.category, t.subcategory)
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        deduped.append(t)
    for i, t in enumerate(deduped, 1):
        t.id = f"t{i:04d}"

    by_cat: "OrderedDict[str, int]" = OrderedDict()
    by_major: "OrderedDict[str, int]" = OrderedDict()
    for lf in leaves:
        by_cat.setdefault(lf.category, 0)
        by_major.setdefault(lf.major, 0)
    for t in deduped:
        by_cat[t.category] = by_cat.get(t.category, 0) + 1
        by_major[t.major] = by_major.get(t.major, 0) + 1

    report = Report(
        total=len(deduped),
        by_category=by_cat,
        by_major=by_major,
        empty_description=sum(1 for t in deduped if not t.description),
        subcategories_seen=len(seen_subcats),
        subcategories_in_toc=len(leaves),
        unmatched_headings=unmatched,
        topics_without_terms=topics_without_terms,
        duplicates=dup,
    )
    return deduped, report


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="input/syllabus_ap_ver7_2.docx", type=Path)
    ap.add_argument("--output", default="data/terms.json", type=Path)
    args = ap.parse_args()

    if not args.input.exists():
        print(f"入力が無い: {args.input}", file=sys.stderr)
        print("IPA のシラバスを .docx に変換して input/ に置くこと。", file=sys.stderr)
        return 1

    paragraphs = load_paragraphs(args.input)
    leaves = parse_toc(paragraphs)
    terms, rep = extract(paragraphs, leaves)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # 項目の内容文は別ファイルへ (用語ごとに持たせると重複で肥大するため)
    topics: "OrderedDict[str, str]" = OrderedDict()
    rows = []
    for t in terms:
        d = asdict(t)
        content = d.pop("syllabus_content", "")
        key = f"{t.category}\t{t.subcategory}\t{t.topic}"
        if content and key not in topics:
            topics[key] = content
        d["topic_key"] = key
        rows.append(d)

    with args.output.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    topics_path = args.output.parent / "topics.json"
    with topics_path.open("w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=1)

    w = max((len(c) for c in rep.by_category), default=10)
    print(f"総用語数: {rep.total}")
    print(f"小分類: 本文 {rep.subcategories_seen} / 目次 {rep.subcategories_in_toc}")
    print(f"重複除去: {rep.duplicates}")
    print(f"説明文が空: {rep.empty_description} (シラバスに用語ごとの説明は無いため全件)")
    print()
    print(f"=== カテゴリ別件数 (中分類 {len(rep.by_category)} 個) ===")
    for i, (cat, n) in enumerate(rep.by_category.items(), 1):
        bar = "#" * max(1, round(n / 25)) if n else ""
        print(f"{i:2}. {cat:<{w}}  {n:>5}  {bar}")
    print()
    print("=== 大分類別 ===")
    for maj, n in rep.by_major.items():
        print(f"    {maj:<20} {n:>5}")
    if rep.unmatched_headings:
        print(f"\n[!] 小分類に対応づかなかった見出し {len(rep.unmatched_headings)} 件:")
        for h in rep.unmatched_headings[:20]:
            print("    -", h)
    if rep.topics_without_terms:
        print(f"\n[i] 用語例を持たない項目 {len(rep.topics_without_terms)} 件 (シラバス上そもそも用語例が無い箇所):")
        for h in rep.topics_without_terms[:20]:
            print("    -", h)
    print(f"\n出力: {args.output} ({args.output.stat().st_size/1024:.0f} KB)")
    print(f"      {topics_path} ({topics_path.stat().st_size/1024:.0f} KB, 項目 {len(topics)} 件)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
