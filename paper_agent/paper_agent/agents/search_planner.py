"""SearchPlannerAgent: 国×カテゴリごとに検索クエリを生成する。

同じクエリを繰り返さないよう、実行済みは search_history に記録される。
"""
from __future__ import annotations

from .base import AgentResult, logger

COUNTRY_NAME = {"TH": "Thailand", "VN": "Vietnam"}

# カテゴリごとのクエリ語パターン (複数パターンで検索空間を広げる)
CATEGORY_TERMS: dict[int, list[str]] = {
    1: ["workplace stereotypes ageism", "age discrimination employees",
        "generational stereotypes organization"],
    2: ["work values employees", "work ethic motivation job satisfaction",
        "organizational commitment generations"],
    3: ["knowledge transfer workforce", "reverse mentoring tacit knowledge",
        "succession founder successor firm"],
    4: ["leadership styles employees", "communication power distance workplace",
        "psychological safety supervisor subordinate"],
    5: ["technology adoption digital transformation", "AI reskilling workforce",
        "technology acceptance employees"],
    6: ["resistance to change organization", "turnover intention retention employees",
        "status quo bias organizational change"],
}

# 各クエリに共通で足す世代アンカー
GENERATION_ANCHORS = [
    "multigenerational workforce",
    "Gen X Gen Y Gen Z employees",
    "generational differences workplace",
]


class SearchPlannerAgent:
    name = "SearchPlannerAgent"

    def __init__(self, countries: list[str], categories: list[int]):
        self.countries = [c.upper() for c in countries]
        self.categories = categories

    def plan(self) -> list[dict]:
        """(country, category, query) の決定論的リストを返す。"""
        out: list[dict] = []
        for country in self.countries:
            name = COUNTRY_NAME.get(country, country)
            for cat in self.categories:
                terms = CATEGORY_TERMS.get(cat, [])
                anchors = GENERATION_ANCHORS
                # cat 語 × anchor を組み合わせる (パターンを増やす)
                for i, term in enumerate(terms):
                    anchor = anchors[i % len(anchors)]
                    query = f"{name} {anchor} {term}"
                    out.append({"country": country, "category": f"category_{cat}",
                                "query": query})
        return out

    def next_query(self, db) -> dict | None:
        """search_history に無い未実行クエリを1つ返す。無ければ None。"""
        for item in self.plan():
            if not db.search_seen(item["query"]):
                return item
        return None

    def remaining(self, db) -> int:
        return sum(1 for item in self.plan() if not db.search_seen(item["query"]))

    def run(self, db) -> AgentResult:
        res = AgentResult(self.name)
        item = self.next_query(db)
        res.info["next"] = item
        res.info["remaining"] = self.remaining(db)
        if item is None:
            res.add("検索空間を尽くしました (未実行クエリなし)。")
        else:
            res.add(f"次のクエリ: {item['query']}")
        return res
