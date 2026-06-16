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
    res = screen(_rec(country="TH"), thai_text)
    assert res.translation_required is True
    assert any("translation is required" in w for w in res.warnings)
    # タイ語原文は誤って除外せず、翻訳後に再判定するため needs_review にする
    assert res.decision == ScreeningStatus.needs_review


def test_query_country_label_alone_does_not_set_country_fit():
    # 本文に Thailand/Vietnam の証拠が無ければ、検索国ラベル(TH)だけで country_fit にしない
    text = (
        "Abstract\nThis paper compares Generation X and Generation Y employees in companies.\n\n"
        "Introduction\nA multigenerational workforce in organizations and firms is studied. "
        "Baby Boomers, Generation X and Generation Y employees work together. Work values and "
        "motivation across generations in the workplace are compared among employees.\n\n"
        "Discussion\nGenerations differ in work values across companies and organizations.\n\n"
        "Conclusion\nGenerational diversity matters in the workplace.\n"
    ) * 3
    # 検索国ラベル TH があっても、本文に証拠が無いので country_fit=False
    res_label = screen(_rec(country="TH", query_country="TH"), text)
    assert res_label.country_fit is False
    assert res_label.target_country_source in ("unknown", "query_only")


def test_country_fit_requires_document_evidence():
    # 本文に Thailand があれば country_fit=True, source=full_text
    text = (
        "Abstract\nThis study compares Generation X and Generation Y employees in Thailand companies.\n\n"
        "Introduction\nThailand workplaces and organizations employ a multigenerational workforce. "
        "Baby Boomers, Generation X and Generation Y employees work together in Thai firms. "
        "Work values and motivation across generations in the workplace are compared.\n\n"
        "Discussion\nGenerations differ in work values across Thai companies.\n\n"
        "Conclusion\nGenerational diversity matters in the Thailand workplace.\n"
    ) * 3
    res = screen(_rec(country="TH"), text)
    assert res.country_fit is True
    assert res.target_country == "Thailand"
    assert res.target_country_source == "full_text"
    assert res.target_country_evidence
