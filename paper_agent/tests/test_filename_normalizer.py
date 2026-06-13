"""タイトル正規化・ファイル名スラッグのテスト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_agent.utils import normalize_title, slugify, title_case_slug  # noqa: E402


def test_normalize_lowercases_and_collapses_spaces():
    assert normalize_title("  Hello   WORLD  ") == "hello world"


def test_normalize_absorbs_colon_hyphen_quote_differences():
    a = "Leadership styles and generational effects: examples of US companies in Vietnam"
    b = "Leadership styles and generational effects - examples of US companies in Vietnam"
    c = "Leadership styles and generational effects examples of US companies in Vietnam"
    assert normalize_title(a) == normalize_title(b) == normalize_title(c)


def test_normalize_keeps_key_words():
    nt = normalize_title("Work Values in the Thai Workplace (Vietnam comparison)")
    for w in ("thai", "workplace", "vietnam"):
        assert w in nt


def test_normalize_handles_curly_quotes():
    a = normalize_title("Founder’s Apprehension in Small Family Business Succession in Thailand")
    b = normalize_title("Founder's Apprehension in Small Family Business Succession in Thailand")
    assert a == b


def test_normalize_empty():
    assert normalize_title("") == ""
    assert normalize_title(None) == ""


def test_slugify_basic():
    assert slugify("Navigating Generational Diversity!") == "navigating-generational-diversity"


def test_slugify_non_ascii_falls_back():
    # 日本語のみ -> ascii 化で消えるので untitled
    assert slugify("タイ論文") == "untitled"


def test_title_case_slug():
    assert title_case_slug("navigating generational diversity") == "Navigating-Generational-Diversity"
