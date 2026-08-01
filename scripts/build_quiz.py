#!/usr/bin/env python3
"""data/terms.json から 4 択問題を生成して data/quiz.json に出力する。

出題は 2 方向。どちらも用語ごとの説明文を必要としない
(IPA シラバスには用語ごとの説明が無く、説明文を新規生成しないため)。

  odd  仲間はずれ探し
       「『システムの評価指標』に属さない用語はどれか」
       誤答 3 語は指定した小分類の中から取る。正解は別の小分類の 1 語。
       同じ箱の中の語が並ぶので、箱の中身を覚えていないと消去法が効かない。

  cat  分類当て
       「『スループット』はどの分類の用語か」
       誤答 3 つは同じ中分類の別の小分類。近い箱ほど紛らわしい。

誤答選択肢は必ず「同じ箱」から取る。全体からランダムに選ぶと 1 つだけ
明らかに正解になってしまい、練習にならないため。箱は狭い順に
subtopic → topic → subcategory → category と緩める。

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
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

N_OPTIONS = 4


def build_indexes(terms: list[dict]):
    """カテゴリ名などを ID 化して、埋め込みサイズを抑える。"""
    cats: "OrderedDict[str, int]" = OrderedDict()
    subs: "OrderedDict[tuple[str, str], int]" = OrderedDict()
    topics: "OrderedDict[str, int]" = OrderedDict()

    for t in terms:
        cats.setdefault(t["category"], len(cats))
        subs.setdefault((t["category"], t["subcategory"]), len(subs))
        topics.setdefault(t["topic_key"], len(topics))
    return cats, subs, topics


def pick_others(pool: list[int], exclude: set[int], k: int, rng: random.Random) -> list[int]:
    cand = [i for i in pool if i not in exclude]
    if len(cand) < k:
        return []
    return rng.sample(cand, k)


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

    cats, subs, topics = build_indexes(terms)

    # 用語 -> 各種の箱
    by_sub: dict[int, list[int]] = defaultdict(list)
    by_topic: dict[int, list[int]] = defaultdict(list)
    by_subtopic: dict[tuple[int, str], list[int]] = defaultdict(list)
    by_cat: dict[int, list[int]] = defaultdict(list)
    subs_of_cat: dict[int, list[int]] = defaultdict(list)

    for i, t in enumerate(terms):
        ci = cats[t["category"]]
        si = subs[(t["category"], t["subcategory"])]
        ti = topics[t["topic_key"]]
        by_cat[ci].append(i)
        by_sub[si].append(i)
        by_topic[ti].append(i)
        by_subtopic[(ti, t["subtopic"])].append(i)
        if si not in subs_of_cat[ci]:
            subs_of_cat[ci].append(si)

    # 同じ用語名が複数の小分類にまたがるものは「分類当て」から外す。
    # 正解が一意に決まらないため。仲間はずれ探しには使う。
    name_subs: dict[str, set[int]] = defaultdict(set)
    for i, t in enumerate(terms):
        name_subs[t["term"]].add(subs[(t["category"], t["subcategory"])])
    ambiguous = {n for n, s in name_subs.items() if len(s) > 1}

    sub_list = list(subs.keys())
    all_subs = list(range(len(sub_list)))

    questions: list[dict] = []
    skipped = Counter()

    for i, t in enumerate(terms):
        ci = cats[t["category"]]
        si = subs[(t["category"], t["subcategory"])]
        ti = topics[t["topic_key"]]

        # --- odd: この用語は「別の小分類」の側。誤答 3 語は 1 つの小分類から。
        # 同じ中分類の中で、自分とは別の小分類を選ぶ (近い箱ほど紛らわしい)
        sibling_subs = [s for s in subs_of_cat[ci] if s != si and len(by_sub[s]) >= N_OPTIONS - 1]
        if not sibling_subs:
            # 中分類に他の小分類が無い場合だけ全体に広げる
            sibling_subs = [s for s in all_subs if s != si and len(by_sub[s]) >= N_OPTIONS - 1]
        if sibling_subs:
            host = rng.choice(sibling_subs)
            others = pick_others(by_sub[host], {i}, N_OPTIONS - 1, rng)
            if others:
                questions.append({"k": "odd", "a": i, "o": others, "c": host})
            else:
                skipped["odd"] += 1
        else:
            skipped["odd"] += 1

        # --- cat: この用語の小分類を当てる
        if t["term"] in ambiguous:
            skipped["cat_ambiguous"] += 1
            continue
        pool = [s for s in subs_of_cat[ci] if s != si]
        if len(pool) < N_OPTIONS - 1:
            # 中分類内で足りなければ同じ大分類、それでも足りなければ全体
            wider = [
                s for s in all_subs
                if s != si and sub_list[s][0] != sub_list[si][0]
            ]
            pool = pool + [s for s in wider if s not in pool]
        if len(pool) < N_OPTIONS - 1:
            skipped["cat"] += 1
            continue
        distractors = rng.sample(pool, N_OPTIONS - 1)
        questions.append({"k": "cat", "a": i, "o": distractors, "c": si})

    rng.shuffle(questions)

    # 埋め込み用のコンパクトな形へ
    term_rows = []
    for t in terms:
        term_rows.append([
            t["term"],
            subs[(t["category"], t["subcategory"])],
            topics[t["topic_key"]],
            t["subtopic"],
            t["abbr_full"],
        ])

    payload = {
        "meta": {
            "source": "IPA 応用情報技術者試験（レベル３）シラバス Ver.7.2",
            "note": "用語ごとの説明文はシラバスに存在しないため、本アプリは分類の識別に限定した出題を行う。説明文の生成は行っていない。",
            "terms": len(terms),
            "questions": len(questions),
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
    print(f"用語: {len(terms)}")
    print(f"問題: {len(questions)}   (仲間はずれ {kinds['odd']} / 分類当て {kinds['cat']})")
    print(f"分類当てから除外した曖昧な用語: {len(ambiguous)} 語")
    for k, v in skipped.items():
        print(f"  生成できなかった: {k} = {v}")

    # 誤答が同じ箱から取れているかの確認
    same_cat = sum(
        1 for q in questions if q["k"] == "cat"
        and all(sub_list[o][0] == sub_list[q["c"]][0] for o in q["o"])
    )
    print(f"分類当てのうち誤答が全て同じ中分類: {same_cat}/{kinds['cat']}")
    print(f"\n出力: {args.output} ({args.output.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
