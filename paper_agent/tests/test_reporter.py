"""確認レポートの区分けテスト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_agent.models import PaperRecord, ScreeningStatus  # noqa: E402
from paper_agent.reporter import build_markdown, group_records  # noqa: E402


def _r(pid, status, **kw):
    return PaperRecord(paper_id=pid, screening_status=status, **kw)


def _records():
    return [
        _r("A", ScreeningStatus.accepted, target_country="Thailand", category="category_5"),
        _r("B", ScreeningStatus.supplementary, rejection_reason="単一世代"),
        _r("C", ScreeningStatus.rejected, rejection_reason="対象国不一致"),
        _r("D", ScreeningStatus.duplicate, duplicate_of="A", duplicate_confidence=1.0),
        _r("E", ScreeningStatus.needs_review, same_dataset_warning=True, duplicate_of="A"),
        _r("F", ScreeningStatus.needs_review),  # 同一データ警告なしの要確認
        _r("G", ScreeningStatus.needs_review, original_language="th"),  # 翻訳要
    ]


def test_grouping_puts_records_in_right_buckets():
    g = group_records(_records())
    assert [r.paper_id for r in g.accepted] == ["A"]
    assert [r.paper_id for r in g.supplementary] == ["B"]
    assert [r.paper_id for r in g.rejected] == ["C"]
    assert [r.paper_id for r in g.exact_duplicate] == ["D"]
    # same_dataset_warning は「同一データの可能性あり」に入る
    assert [r.paper_id for r in g.same_dataset] == ["E"]
    # 残りの needs_review は要確認
    assert set(r.paper_id for r in g.needs_review) == {"F", "G"}


def test_markdown_has_all_sections():
    md = build_markdown(_records())
    for header in ("完全重複", "同一データの可能性あり", "主分析候補",
                   "補助文献", "除外", "要確認", "翻訳が必要な論文"):
        assert header in md
    # 翻訳が必要な論文一覧に th の G が載る
    assert "`G`" in md


def test_empty_report_does_not_crash():
    md = build_markdown([])
    assert "総件数: **0**" in md
