#!/usr/bin/env python3
"""data/terms.json から 4 択問題を生成して data/quiz.json に出力する。

方針: 「見覚えがあるか」ではなく「意味を言えるか」を問う。

分類だけを問う形式（この用語はどの小分類か／仲間はずれはどれか）は再認課題で、
消去法と見覚えで正解できてしまい、意味の想起を要求しない。シラバスから
意味そのものを取り出せる用語に限って、次の 5 形式を作る。

  abbr2full   略語 → 正式名称      「WDM が表すものは？」→ 波長分割多重
  term2gloss  用語 → 言い換え      「オーバーフローの別名は？」→ あふれ
  gloss2term  言い換え → 用語      「『あふれ』とも呼ばれる用語は？」
  members     下位概念 → 用語      「蒸留・量子化・プルーニングをまとめた用語は？」
  term2member 用語 → 下位概念      「色の 3 原色に含まれるものは？」

逆向きの「正式名称 → 略語」と、意味が英語のままの略語展開は作らない。
頭文字を照合するだけで解けてしまい、理解を測れないため
(実測で英語のものは 87% が頭文字の一致だけで一意に絞れた)。

これらは括弧の中身という「意味」を答えさせるので、用語を知らなければ
消去法が効かない。素材は IPA シラバスの原文で、解説の生成はしていない。

  cat        分類当て（補助）    意味素材を持たない用語のための埋め合わせ

誤答は必ず同じ箱かつ同じ形式から取る。箱は狭い順に
subtopic → topic → subcategory → category → 大分類 と緩める。
形式を揃えないと、選択肢の見た目だけで正解が割れてしまう。

選択肢の並び順は固定しない。出題時にビューア側でシャッフルする。

使い方:
    python3 scripts/build_quiz.py [--terms data/terms.json]
                                  [--topics data/topics.json]
                                  [--output data/quiz.json]
                                  [--seed 20260801]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

N_OPTIONS = 4
# 略語らしさ: 英数字だけで短いもの
RE_ABBREV = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-./+ ]{1,12}$")
RE_JA = re.compile(r"[ぁ-んァ-ヴ一-龥]")


def is_abbrev(s: str) -> bool:
    return bool(RE_ABBREV.match(s.strip()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--terms", default="data/terms.json", type=Path)
    ap.add_argument("--topics", default="data/topics.json", type=Path)
    ap.add_argument("--output", default="data/quiz.json", type=Path)
    ap.add_argument("--seed", default=20260801, type=int)
    args = ap.parse_args()

    if not args.terms.exists():
        print(f"{args.terms} が無い。先に scripts/extract.py を実行すること。", file=sys.stderr)
        return 1

    terms = json.loads(args.terms.read_text(encoding="utf-8"))
    topic_texts = json.loads(args.topics.read_text(encoding="utf-8")) if args.topics.exists() else {}
    rng = random.Random(args.seed)

    cats: "OrderedDict[str, int]" = OrderedDict()
    subs: "OrderedDict[tuple[str, str], int]" = OrderedDict()
    topics: "OrderedDict[str, int]" = OrderedDict()
    for t in terms:
        cats.setdefault(t["category"], len(cats))
        subs.setdefault((t["category"], t["subcategory"]), len(subs))
        topics.setdefault(t["topic_key"], len(topics))
    sub_list = list(subs.keys())

    # --- 略語と正式名称の向きを正規化する ---
    # シラバスの書き方は 3 通りあり、どちらが略語かが一定しない。
    #   BCD（Binary Coded Decimal：2 進化 10 進）  term が略語
    #   サポートベクトルマシン（SVM）               括弧が略語
    #   暗号化消去（CE：Cryptographic Erase）      括弧に略語と英名の両方
    # ここで (略語, 正式名称) に揃えておかないと、出題の向きが用語ごとに裏返る。
    # 意味は日本語を優先する。「SVG → Scalable Vector Graphics」は頭文字を
    # 照合するだけで解けてしまい、中身を知っているかを測れないため、
    # 和訳や和名が取れるものはそちらを答えにする。
    def split_abbr(term: str, inner: str) -> tuple[str, str]:
        term_ja = bool(RE_JA.search(term))
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9\-./+ ]{0,11})[：:]\s*(.+)$", inner)
        if m:
            a, rest = m.group(1).strip(), m.group(2).strip()
            # 暗号化消去（CE：Cryptographic Erase） -> 意味は和名の方
            return a, term if term_ja else rest
        if is_abbrev(inner):
            return inner.strip(), term          # サポートベクトルマシン（SVM）
        if is_abbrev(term):
            # BCD（Binary Coded Decimal：2 進化 10 進） -> 和訳があればそちら
            parts = re.split(r"[：:]", inner, 1)
            if len(parts) == 2 and RE_JA.search(parts[1]):
                return term, parts[1].strip()
            return term, inner
        return "", ""

    abbrev: dict[int, str] = {}
    fullname: dict[int, str] = {}

    # role: "ab"    略語 ⇔ 正式名称
    #       "gloss" 別名・言い換え
    #       "mem"   下位概念の列挙
    role: dict[int, str] = {}
    for i, t in enumerate(terms):
        if t["members"]:
            role[i] = "mem"
        elif t["abbr_full"]:
            a, f = split_abbr(t["term"], t["abbr_full"])
            if a and f and a != f:
                abbrev[i], fullname[i] = a, f
                # 意味が英語のものと和語のものを混ぜない。混ぜると
                # 「英語の選択肢が 1 つだけ」で中身を読まずに当てられる。
                # 意味が英語のままのものは出題しない。「SPOC → Single Point
                # Of Contact」は頭文字を拾えば解けてしまい、理解を測れない。
                # 実測で 87% が頭文字の一致だけで一意に絞れた。
                if RE_JA.search(f):
                    role[i] = "ab_ja"
                else:
                    del abbrev[i], fullname[i]
        elif t["gloss"]:
            role[i] = "gloss"

    # --- 箱を狭い順に用意する ---
    boxes: list[dict] = [defaultdict(list) for _ in range(5)]  # subtopic/topic/sub/cat/major
    key_of: list[tuple] = []
    for i, t in enumerate(terms):
        ci = cats[t["category"]]
        si = subs[(t["category"], t["subcategory"])]
        ti = topics[t["topic_key"]]
        keys = ((ti, t["subtopic"]), ti, si, ci, t["major"])
        key_of.append(keys)
        r = role.get(i)
        for lv, k in enumerate(keys):
            boxes[lv][(k, r)].append(i)

    def conflicts(i: int, x: int) -> bool:
        """x を i の誤答にすると、選択肢として成立しない場合に True。

        - 選択肢の文字列が正解と同じになる (同名の用語が別の箱にいる)
        - 下位概念を共有していて、誤答のはずが正解になってしまう
          (「色の 3 原色」と「光の 3 原色」で Green を共有する類)
        """
        if terms[x]["term"] == terms[i]["term"]:
            return True
        r = role.get(i)
        if r == "mem":
            if set(terms[x]["members"]) & set(terms[i]["members"]):
                return True
            return False
        if r == "gloss":
            return terms[x]["gloss"] == terms[i]["gloss"]
        if r == "ab_ja":
            return fullname.get(x) == fullname.get(i)
        return False

    def distractors(i: int, k: int) -> list[int]:
        """同じ形式の用語を、できるだけ狭い箱から k 件。"""
        r = role.get(i)
        for lv in range(5):
            pool = [x for x in boxes[lv][(key_of[i][lv], r)] if x != i and not conflicts(i, x)]
            if len(pool) >= k:
                return rng.sample(pool, k)
        allsame = [x for x in role if role[x] == r and x != i and not conflicts(i, x)]
        return rng.sample(allsame, k) if len(allsame) >= k else []

    questions: list[dict] = []
    skipped = Counter()
    widened = Counter()

    def box_level(i: int, opts: list[int]) -> int:
        for lv in range(5):
            pool = set(boxes[lv][(key_of[i][lv], role.get(i))])
            if all(o in pool for o in opts):
                return lv
        return 5

    for i, t in enumerate(terms):
        r = role.get(i)
        if not r:
            continue
        o = distractors(i, N_OPTIONS - 1)
        if not o:
            skipped[r] += 1
            continue
        widened[box_level(i, o)] += 1
        if r == "ab_ja":
            # 逆向き (正式名称 -> 略語) は作らない。頭文字を拾えば
            # 中身を知らなくても解けてしまい、練習にならない。
            questions.append({"k": "abbr2full", "a": i, "o": o})
        elif r == "gloss":
            questions.append({"k": "term2gloss", "a": i, "o": o})
            questions.append({"k": "gloss2term", "a": i, "o": o})
        elif r == "mem":
            questions.append({"k": "members", "a": i, "o": o})
            # 逆向き: この用語に含まれるものはどれか。誤答は同じ箱の
            # 別の用語が抱える下位概念なので、範囲を知らないと選べない。
            questions.append({"k": "term2member", "a": i, "o": o})

    n_meaning = len(questions)

    # --- 補助: 意味素材を持たない用語のための分類当て ---
    subs_of_cat: dict[int, list[int]] = defaultdict(list)
    for (cat, name), si in subs.items():
        subs_of_cat[cats[cat]].append(si)
    name_subs: dict[str, set[int]] = defaultdict(set)
    for t in terms:
        name_subs[t["term"]].add(subs[(t["category"], t["subcategory"])])
    ambiguous = {n for n, s in name_subs.items() if len(s) > 1}

    for i, t in enumerate(terms):
        if i in role or t["term"] in ambiguous:
            continue
        ci = cats[t["category"]]
        si = subs[(t["category"], t["subcategory"])]
        pool = [s for s in subs_of_cat[ci] if s != si]
        if len(pool) < N_OPTIONS - 1:
            pool += [s for s in range(len(sub_list)) if s != si and s not in pool]
        if len(pool) < N_OPTIONS - 1:
            skipped["cat"] += 1
            continue
        questions.append({"k": "cat", "a": i, "o": rng.sample(pool, N_OPTIONS - 1), "c": si})

    rng.shuffle(questions)

    # 略語/正式名称は正規化済みのものを渡す。ビューアは向きを判断しない。
    term_rows = [
        [t["term"], subs[(t["category"], t["subcategory"])], topics[t["topic_key"]],
         abbrev.get(i, ""), fullname.get(i, ""), t["gloss"], t["members"], t["raw"]]
        for i, t in enumerate(terms)
    ]

    payload = {
        "meta": {
            "source": "IPA 応用情報技術者試験（レベル３）シラバス Ver.7.2",
            "note": "シラバスには用語ごとの定義文が無い。意味を問う問題は、原文の括弧が持つ"
                    "正式名称・別名・下位概念からのみ作っている。解説の生成はしていない。",
            "terms": len(terms),
            "questions": len(questions),
            "meaning_questions": n_meaning,
            "seed": args.seed,
        },
        "categories": list(cats.keys()),
        "subcategories": [[name, cats[cat]] for (cat, name) in sub_list],
        "topics": [k.split("\t")[2] for k in topics.keys()],
        "topic_texts": [topic_texts.get(k, "") for k in topics.keys()],
        "terms_data": term_rows,
        "questions": questions,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    kinds = Counter(q["k"] for q in questions)
    print(f"用語 {len(terms)}   うち意味素材あり {len(role)} ({len(role)/len(terms)*100:.0f}%)")
    print(f"問題 {len(questions)}   意味を問う {n_meaning} / 分類のみ {kinds['cat']}")
    for k in ("abbr2full", "term2gloss", "gloss2term", "members", "term2member", "cat"):
        if kinds[k]:
            print(f"   {k:<11}{kinds[k]:>6}")
    names = ["subtopic", "topic", "小分類", "中分類", "大分類", "全体"]
    print("\n誤答を取った箱の広さ:")
    for lv, n in sorted(widened.items()):
        print(f"   {names[lv]:<10}{n:>6}")
    if skipped:
        print("\n生成できなかった:", dict(skipped))
    print(f"\n出力: {args.output} ({args.output.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
