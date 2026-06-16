"""SearchPlannerAgent: 国×カテゴリごとに多数の検索クエリを生成する。

母数を最大化するため、各 (国, カテゴリ) について 20 パターン以上のクエリを作る。
同じクエリを繰り返さないよう、実行済みは search_history に記録される。
"""
from __future__ import annotations

from .base import AgentResult, logger

COUNTRY_NAME = {"TH": "Thailand", "VN": "Vietnam"}

# 国非依存の検索アングル (世代・職場の切り口を変えて母数を増やす) — 20個
BASE_ANGLES = [
    "multigenerational workforce",
    "generational differences employees",
    "Gen X Gen Y Gen Z workplace",
    "age diversity workplace",
    "intergenerational workplace",
    "older younger workers organization",
    "work values generations employees",
    "knowledge transfer older younger employees",
    "reverse mentoring workplace generations",
    "workplace communication generations",
    "leadership generations workplace",
    "digital transformation employees generations",
    "technology acceptance generations employees",
    "change resistance employees generations",
    "employee retention generations",
    "job satisfaction Gen X Gen Y Gen Z",
    "organizational commitment generations",
    "multigenerational management",
    "age discrimination workplace",
    "intergenerational collaboration workplace",
]

# カテゴリごとに掛け合わせる語 (検索をカテゴリ寄りにする)
CATEGORY_TERMS: dict[int, str] = {
    1: "ageism stereotype age discrimination",
    2: "work values work ethic motivation job satisfaction",
    3: "knowledge transfer reverse mentoring tacit knowledge succession",
    4: "communication leadership power distance psychological safety",
    5: "technology adoption digital transformation AI reskilling",
    6: "resistance to change status quo bias turnover intention retention",
}


class SearchPlannerAgent:
    name = "SearchPlannerAgent"

    def __init__(self, countries: list[str], categories: list[int]):
        self.countries = [c.upper() for c in countries]
        self.categories = categories

    def plan(self) -> list[dict]:
        """(country, category, query) の決定論的リストを返す。各国×カテゴリ20件以上。"""
        out: list[dict] = []
        seen: set[str] = set()
        for country in self.countries:
            name = COUNTRY_NAME.get(country, country)
            for cat in self.categories:
                cat_term = CATEGORY_TERMS.get(cat, "")
                for angle in BASE_ANGLES:
                    query = f"{name} {angle} {cat_term}".strip()
                    if query in seen:
                        continue
                    seen.add(query)
                    out.append({"country": country, "category": f"category_{cat}",
                                "query": query})
        return out

    def next_query(self, db) -> dict | None:
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
        res.info["total_planned"] = len(self.plan())
        if item is None:
            res.add("検索空間を尽くしました (未実行クエリなし)。")
        else:
            res.add(f"次のクエリ: {item['query']}")
        return res
