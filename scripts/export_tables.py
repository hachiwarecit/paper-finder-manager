"""候補表とレポートの出力。

出力 (CLAUDE.md):
  1. data/candidates.csv
  2. data/candidates.json
  3. reports/candidate_table.csv
  4. reports/missing_slots.csv
  5. reports/duplicates.csv

再実行時は既存 candidates.json をロードし、id でマージする。
人間が編集したフィールド (screening_status, slot, notes 等) は失わないよう、
既存行の非空フィールドを優先し、新規メタデータで空欄のみを補う。
"""
from __future__ import annotations

import csv
import json
from typing import Any, Dict, List

from common import (
    ADOPTED_STATUSES,
    CANDIDATES_CSV,
    CANDIDATES_JSON,
    CATEGORIES,
    COUNTRIES,
    CSV_COLUMNS,
    PAPERS_PER_SLOT,
    REPORTS_DIR,
    now_iso,
    slot_name,
)

# 人間の判断を表すステータス（マージ時に上書きしない）
_HUMAN_LOCKED = {
    "adopted", "rejected", "needs_review",
    "exclude_country_mismatch", "exclude_no_workplace_context",
    "exclude_no_abstract_or_pdf", "manual_download_needed",
}


def load_existing() -> List[Dict[str, Any]]:
    if CANDIDATES_JSON.exists():
        with open(CANDIDATES_JSON, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def merge(existing: List[Dict[str, Any]],
          new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """id をキーに既存と新規をマージする。"""
    by_id: Dict[str, Dict[str, Any]] = {row["id"]: dict(row) for row in existing}

    for cand in new:
        cid = cand["id"]
        if cid not in by_id:
            cand.setdefault("created_at", now_iso())
            cand["updated_at"] = now_iso()
            by_id[cid] = cand
            continue

        row = by_id[cid]
        # 人間がロックしたステータスは保持
        locked = row.get("screening_status") in _HUMAN_LOCKED
        for key, value in cand.items():
            if key in ("created_at",):
                continue
            if key == "screening_status" and locked:
                continue
            # slot / notes など、既存に値があれば尊重し空欄のみ補完
            if key in ("slot", "notes") and row.get(key):
                continue
            if value:  # 新しい非空メタデータで補強
                row[key] = value
        row["updated_at"] = now_iso()

    return list(by_id.values())


def _row_for_csv(cand: Dict[str, Any]) -> Dict[str, Any]:
    return {col: cand.get(col, "") for col in CSV_COLUMNS}


def write_candidates(candidates: List[Dict[str, Any]]) -> None:
    CANDIDATES_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATES_JSON, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)

    with open(CANDIDATES_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for cand in candidates:
            writer.writerow(_row_for_csv(cand))


def write_candidate_table(candidates: List[Dict[str, Any]]) -> None:
    """reports/candidate_table.csv: 重複・除外を除いた人間レビュー用の要約表。"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cols = [
        "id", "country", "category", "slot", "title", "year",
        "screening_status", "oa_status", "pdf_url", "local_pdf_path",
        "doi", "related_category", "reason_for_inclusion",
    ]
    rows = [
        c for c in candidates
        if c.get("screening_status") not in ("duplicate",)
        and not str(c.get("screening_status", "")).startswith("exclude")
    ]
    rows.sort(key=lambda c: (c.get("country", ""), c.get("slot", "")))
    with open(REPORTS_DIR / "candidate_table.csv", "w",
              encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for c in rows:
            writer.writerow({k: c.get(k, "") for k in cols})


def write_missing_slots(candidates: List[Dict[str, Any]]) -> None:
    """reports/missing_slots.csv: 採用論文がまだ無い枠を出力。

    3か国 × 6カテゴリ × 2本 の枠を走査する。
    """
    # 採用済み枠を集計
    filled: Dict[str, int] = {}
    for c in candidates:
        if c.get("screening_status") in ADOPTED_STATUSES and c.get("slot"):
            filled[c["slot"]] = filled.get(c["slot"], 0) + 1

    out_rows = []
    for country in COUNTRIES:
        for category in CATEGORIES:
            for paper_no in range(1, PAPERS_PER_SLOT + 1):
                slot = slot_name(country, paper_no, category)
                if filled.get(slot, 0) >= 1:
                    continue
                action = ("find first paper" if paper_no == 1
                          else "find second paper")
                out_rows.append({
                    "country": country,
                    "category": category,
                    "slot": slot,
                    "status": "missing",
                    "needed_action": action,
                })

    with open(REPORTS_DIR / "missing_slots.csv", "w",
              encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["country", "category", "slot", "status", "needed_action"])
        writer.writeheader()
        writer.writerows(out_rows)


def write_duplicates(candidates: List[Dict[str, Any]]) -> None:
    """reports/duplicates.csv: 重複と判定された候補の一覧。"""
    cols = ["id", "duplicate_of", "country", "category", "title", "doi"]
    rows = [c for c in candidates if c.get("duplicate_of")]
    with open(REPORTS_DIR / "duplicates.csv", "w",
              encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for c in rows:
            writer.writerow({k: c.get(k, "") for k in cols})


def export_all(candidates: List[Dict[str, Any]]) -> None:
    write_candidates(candidates)
    write_candidate_table(candidates)
    write_missing_slots(candidates)
    write_duplicates(candidates)


if __name__ == "__main__":
    existing = load_existing()
    export_all(existing)
    print(f"{len(existing)} 件の候補からレポートを再生成しました。")
