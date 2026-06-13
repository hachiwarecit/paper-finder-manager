"""スクリーニング判定のテスト (ルールベース)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_agent.models import DocumentType, PaperRecord, ScreeningStatus  # noqa: E402
from paper_agent.screener import screen  # noqa: E402


def _rec(pid="P1", **kw):
    return PaperRecord(paper_id=pid, **kw)


# 2世代比較・職場・タイ・カテゴリありの十分な本文
ACCEPTED_TEXT = (
    "Abstract\nThis study examines generational differences in the Thai workplace.\n\n"
    "Introduction\nThailand has a multigenerational workforce. Companies in Thailand employ "
    "Baby Boomers, Generation X, Generation Y and Generation Z employees. This study compares "
    "work values and technology adoption across these generations among employees in Thai "
    "organizations and firms.\n\n"
    "Literature Review\nPrior research on generational differences, technology acceptance and "
    "digital transformation in the workplace is reviewed. Reskilling and innovation are discussed.\n\n"
    "Discussion\nThe results suggest that Generation Z employees adopt new technology faster than "
    "Baby Boomers in Thai companies. Work values differ across generations.\n\n"
    "Conclusion\nGenerational diversity shapes technology adoption in the Thai workplace.\n"
) * 3


def test_accepted_case():
    res = screen(_rec(), ACCEPTED_TEXT)
    assert res.country_fit is True
    assert res.workplace_fit is True
    assert res.generation_fit is True
    assert res.fulltext_fit is True
    assert res.category_fit != "unknown"
    assert res.decision in (ScreeningStatus.accepted, ScreeningStatus.needs_review)


def test_single_generation_not_accepted():
    text = (
        "Abstract\nThis study focuses only on Generation Z employees in Vietnamese companies.\n\n"
        "Introduction\nGeneration Z workers in Vietnam organizations are studied. Vietnam firms "
        "employ many young workers. We study job satisfaction and engagement of Generation Z only.\n\n"
        "Literature Review\nWork values and motivation literature is reviewed for the workplace.\n\n"
        "Discussion\nGeneration Z employees value engagement in Vietnam enterprises.\n\n"
        "Conclusion\nGeneration Z engagement matters.\n"
    ) * 3
    res = screen(_rec(), text)
    assert res.generation_fit is False
    assert res.decision != ScreeningStatus.accepted


def test_abstract_only_not_accepted():
    text = (
        "Abstract\nThis short abstract compares Generation X and Generation Y employees in "
        "Thailand workplaces regarding work values. No full text is available."
    )
    res = screen(_rec(full_text_available=False), text)
    assert res.fulltext_fit is False
    assert res.decision in (ScreeningStatus.supplementary, ScreeningStatus.rejected)


def test_teaching_case_is_supplementary():
    text = ACCEPTED_TEXT + "\nThis teaching case was prepared for classroom discussion. Teaching note included.\n"
    res = screen(_rec(), text)
    assert res.document_type == DocumentType.teaching_case
    assert res.decision == ScreeningStatus.supplementary


def test_non_target_country_rejected():
    text = (
        "Abstract\nThis study examines generational differences in Japanese and Korean workplaces.\n\n"
        "Introduction\nJapan and Korea have multigenerational workforces. Companies employ Baby "
        "Boomers, Generation X and Generation Y. We compare work values across generations.\n\n"
        "Discussion\nGenerational diversity matters in these companies.\n\n"
        "Conclusion\nGenerations differ.\n"
    ) * 3
    res = screen(_rec(), text)
    assert res.country_fit is False
    assert res.decision == ScreeningStatus.rejected


def test_thai_language_requires_translation():
    thai_text = (
        "บทคัดย่อ การศึกษานี้เปรียบเทียบความแตกต่างระหว่างเจเนอเรชันในที่ทำงานของไทย "
        "พนักงานในองค์กรไทยมีหลายเจเนอเรชัน เบบี้บูมเมอร์ เจเนอเรชันเอ็กซ์ และเจเนอเรชันวาย "
        "บทนำ ประเทศไทยมีแรงงานหลายเจเนอเรชันในบริษัทและองค์กร "
    ) * 10
    res = screen(_rec(), thai_text)
    assert res.translation_required is True
    assert any("translation is required" in w for w in res.warnings)
