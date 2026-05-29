"""重複チェック。

判定方法 (CLAUDE.md):
  1. DOI 一致
  2. タイトルの正規化一致
  3. タイトル類似度が高い場合 (rapidfuzz による fuzzy 比較)

重複した候補には duplicate_of に元論文の id を記録し、screening_status を
"duplicate" にする。最初に出現したものを「原本」とみなす。
"""
from __future__ import annotations

from typing import Any, Dict, List

from common import normalize_title

try:
    from rapidfuzz.fuzz import token_sort_ratio
except ImportError:  # rapidfuzz が無い場合は完全一致のみにフォールバック
    token_sort_ratio = None

SIMILARITY_THRESHOLD = 92.0  # 0-100。これ以上を重複とみなす


def _title_similarity(a: str, b: str) -> float:
    if token_sort_ratio is None:
        return 100.0 if a == b else 0.0
    return token_sort_ratio(a, b)


def deduplicate(candidates: List[Dict[str, Any]],
                threshold: float = SIMILARITY_THRESHOLD) -> List[Dict[str, Any]]:
    """候補リストの重複に印を付ける。in-place で更新しつつ同じリストを返す。

    各候補には事前に "id" が割り当てられている前提。
    """
    seen_doi: Dict[str, str] = {}        # doi -> original id
    seen_title: Dict[str, str] = {}      # normalized title -> original id
    originals: List[Dict[str, Any]] = []  # fuzzy 比較用の原本候補

    for cand in candidates:
        cand.setdefault("duplicate_of", "")
        doi = (cand.get("doi") or "").strip().lower()
        norm = normalize_title(cand.get("title", ""))

        dup_of = ""

        # 1. DOI 一致
        if doi and doi in seen_doi:
            dup_of = seen_doi[doi]
        # 2. 正規化タイトル一致
        elif norm and norm in seen_title:
            dup_of = seen_title[norm]
        # 3. タイトル類似度
        elif norm:
            for orig in originals:
                if _title_similarity(norm, orig["_norm_title"]) >= threshold:
                    dup_of = orig["id"]
                    break

        if dup_of:
            cand["duplicate_of"] = dup_of
            cand["screening_status"] = "duplicate"
        else:
            if doi:
                seen_doi[doi] = cand["id"]
            if norm:
                seen_title[norm] = cand["id"]
            originals.append({"id": cand["id"], "_norm_title": norm})

    return candidates


if __name__ == "__main__":
    import json
    import sys

    data = json.load(sys.stdin)
    json.dump(deduplicate(data), sys.stdout, ensure_ascii=False, indent=2)
