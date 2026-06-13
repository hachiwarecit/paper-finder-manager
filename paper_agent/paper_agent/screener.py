"""ルールベースの採否判定 (screener)。

入力 : PaperRecord + 抽出テキスト
出力 : ScreeningResult

観点:
  1. Country fit     - 調査対象国が Thailand / Vietnam か (著者所属ではなく文脈)
  2. Workplace fit   - 職場・組織文脈があるか
  3. Generation fit  - 2世代以上を実際に比較しているか
  4. Full text fit   - Abstract だけでなく本文があるか
  5. Category fit     - 6カテゴリのどれに最も近いか
  6. Document type   - journal/thesis/conference/teaching_case など
  7. Language        - タイ語/ベトナム語なら translation_required=True

ANTHROPIC_API_KEY があれば LLM 補助も可能だが、無くてもルールベースで完結する。
"""
from __future__ import annotations

from .extractor import select_analysis_sections
from .models import DocumentType, PaperRecord, ScreeningResult, ScreeningStatus
from .utils import detect_language, get_logger, load_config

logger = get_logger()

_RULES = None
_CATEGORIES = None


def _rules() -> dict:
    global _RULES
    if _RULES is None:
        _RULES = load_config("screening_rules.yaml")
    return _RULES


def _categories() -> dict:
    global _CATEGORIES
    if _CATEGORIES is None:
        _CATEGORIES = load_config("categories.yaml")
    return _CATEGORIES


def _count_hits(text_low: str, keywords) -> int:
    return sum(1 for kw in (keywords or []) if kw and kw.lower() in text_low)


# ---------------------------------------------------------------------------
# 各観点の判定
# ---------------------------------------------------------------------------
def assess_country(rec: PaperRecord, text_low: str) -> tuple[bool, str, list[str]]:
    rules = _rules().get("country", {})
    reasons: list[str] = []
    detected = None
    # 既にレコードに target_country があればそれを優先
    if rec.target_country and rec.target_country.lower() in ("thailand", "vietnam", "th", "vn"):
        detected = "thailand" if rec.target_country.lower() in ("thailand", "th") else "vietnam"

    th_hits = _count_hits(text_low, rules.get("thailand", {}).get("keywords", []))
    vn_hits = _count_hits(text_low, rules.get("vietnam", {}).get("keywords", []))
    if detected is None:
        if th_hits == 0 and vn_hits == 0:
            return False, "unknown", ["対象国 (Thailand/Vietnam) を本文中に確認できない。"]
        detected = "thailand" if th_hits >= vn_hits else "vietnam"

    reasons.append(f"{detected.capitalize()} が研究対象国の文脈として検出された。")
    return True, detected, reasons


def assess_workplace(text_low: str) -> tuple[bool, list[str]]:
    kws = _rules().get("workplace", {}).get("keywords", [])
    hits = _count_hits(text_low, kws)
    if hits >= 1:
        return True, [f"職場・組織文脈の語が {hits} 種類検出された。"]
    return False, ["職場・組織文脈を示す語が見つからない。"]


def assess_generations(text_low: str) -> tuple[bool, int, list[str], list[str]]:
    rules = _rules()
    gens = rules.get("generations", {})
    found_groups: list[str] = []
    for group_name, kws in gens.items():
        if _count_hits(text_low, kws) >= 1:
            found_groups.append(group_name)

    markers = rules.get("multigenerational_markers", [])
    marker_hit = _count_hits(text_low, markers) >= 1

    # "founder" と "successor" の両方があれば先代-後継の2世代比較とみなす
    succession_pair = ("succession" in found_groups)

    n = len(found_groups)
    reasons: list[str] = []
    warnings: list[str] = []

    generation_fit = False
    if n >= 2:
        generation_fit = True
        reasons.append(f"2世代以上を比較 (検出: {', '.join(found_groups)})。")
    elif marker_hit:
        generation_fit = True
        reasons.append("多世代/世代差を示すマーカー語が検出された。")
    elif succession_pair:
        generation_fit = True
        reasons.append("先代-後継 (founder/successor) の世代間関係が検出された。")
    elif n == 1:
        warnings.append(f"単一世代研究の可能性 (検出: {found_groups[0]})。主分析には不十分。")
        reasons.append("世代が1つしか検出されず、2世代比較が確認できない。")
    else:
        reasons.append("世代区分を示す語が検出されない。")

    effective_n = max(n, 2 if (marker_hit and n < 2) else n)
    return generation_fit, effective_n, reasons, warnings


def assess_fulltext(rec: PaperRecord, text: str) -> tuple[bool, list[str], list[str]]:
    cfg = _rules().get("fulltext", {})
    min_chars = int(cfg.get("min_chars_for_fulltext", 3000))
    min_sections = int(cfg.get("min_sections_beyond_abstract", 1))
    # 明らかに要旨のみと判断する下限 (1ページ Abstract 相当)
    abstract_only_floor = 800

    reasons: list[str] = []
    evidence: list[str] = []

    if not text or len(text) < abstract_only_floor:
        reasons.append(f"本文が短い ({len(text or '')} 文字)。要旨のみの可能性。")
        return False, reasons, evidence

    # 主判定: Abstract 以外の分析対象章を実体のある分量で抽出できるか
    st = select_analysis_sections(text)
    kept = [s.heading for s in st.sections if s.keep and s.body and len(s.body) > 150]
    beyond_abstract = [h for h in kept if "abstract" not in h.lower()]
    evidence = kept
    if len(beyond_abstract) >= min_sections:
        reasons.append(f"Abstract 以外の分析対象章を {len(beyond_abstract)} 個抽出できる。")
        return True, reasons, evidence

    # 章分割に失敗しても、十分長い本文なら full text とみなす
    if len(text) >= min_chars:
        reasons.append(f"章分割は不十分だが本文が長い ({len(text)} 文字) ため本文ありと判断。")
        return True, reasons, evidence

    reasons.append("Abstract 以外の本文章を十分に抽出できない。")
    return False, reasons, evidence


def assess_category(text_low: str) -> tuple[str, str, list[str]]:
    cats = _categories().get("categories", {})
    scores: dict[str, int] = {}
    for cat_id, cat in cats.items():
        scores[cat_id] = _count_hits(text_low, cat.get("keywords", []))
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked or ranked[0][1] == 0:
        return "unknown", "unknown", ["6カテゴリのキーワードに該当しない。"]
    primary = ranked[0][0]
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] > 0 else "unknown"
    name = cats[primary].get("name", primary)
    return primary, secondary, [f"最も近いカテゴリ: {primary} ({name})。"]


def assess_document_type(text_low: str) -> DocumentType:
    dt_rules = _rules().get("document_type", {})
    scores: dict[str, int] = {}
    for dt_name, kws in dt_rules.items():
        scores[dt_name] = _count_hits(text_low, kws)
    # teaching_case / thesis / conference_abstract を優先的に判定
    for special in ("teaching_case", "conference_abstract", "thesis", "conference_paper"):
        if scores.get(special, 0) >= 1:
            try:
                return DocumentType(special)
            except ValueError:
                pass
    if scores.get("report", 0) >= 1:
        return DocumentType.report
    if scores.get("journal_article", 0) >= 1:
        return DocumentType.journal_article
    return DocumentType.unknown


def assess_language(rec: PaperRecord, text: str) -> tuple[str, bool, list[str]]:
    lang = detect_language(text)
    translation_required = lang in _rules().get("language", {}).get(
        "translation_required_langs", ["th", "vi"]
    )
    warnings: list[str] = []
    if translation_required:
        lang_name = {"th": "Thai", "vi": "Vietnamese"}.get(lang, lang)
        warnings.append(
            f"Original full text is {lang_name}; English translation is required "
            "before KH Coder analysis."
        )
    return lang, translation_required, warnings


# ---------------------------------------------------------------------------
# 統合判定
# ---------------------------------------------------------------------------
def screen(rec: PaperRecord, text: str, *, results_discussion_mixed: bool | None = None) -> ScreeningResult:
    """PaperRecord と本文から ScreeningResult を作る。"""
    text = text or ""
    text_low = text.lower()

    country_fit, detected_country, country_reasons = assess_country(rec, text_low)
    workplace_fit, workplace_reasons = assess_workplace(text_low)
    generation_fit, n_gen, gen_reasons, gen_warnings = assess_generations(text_low)
    fulltext_fit, ft_reasons, evidence = assess_fulltext(rec, text)
    category_fit, secondary, cat_reasons = assess_category(text_low)
    doc_type = assess_document_type(text_low)
    lang, translation_required, lang_warnings = assess_language(rec, text)

    reasons = country_reasons + workplace_reasons + gen_reasons + ft_reasons + cat_reasons
    warnings = gen_warnings + lang_warnings

    # Results/Discussion 混在の検出
    if results_discussion_mixed is None:
        st = select_analysis_sections(text)
        results_discussion_mixed = st.needs_review
    if results_discussion_mixed:
        warnings.append("Results と Discussion が混在する章があり、抽出範囲の確認が必要。")

    # ----- 判定ツリー -----
    decision = ScreeningStatus.accepted
    reject_reason = ""

    if not country_fit:
        decision = ScreeningStatus.rejected
        reject_reason = "対象国が Thailand / Vietnam ではない、または確認できない。"
    elif doc_type == DocumentType.teaching_case:
        decision = ScreeningStatus.supplementary
        reject_reason = "Teaching Case のため主分析には含めず補助文献とする。"
    elif doc_type == DocumentType.conference_abstract or not fulltext_fit:
        # 要旨のみ
        if not fulltext_fit:
            decision = ScreeningStatus.supplementary
            reject_reason = "本文が取得できず要旨のみ。主分析には不十分。"
        else:
            decision = ScreeningStatus.supplementary
            reject_reason = "学会要旨のため補助文献とする。"
    elif not workplace_fit:
        decision = ScreeningStatus.rejected
        reject_reason = "職場・組織文脈が確認できない。"
    elif not generation_fit:
        decision = ScreeningStatus.supplementary
        reject_reason = "2世代以上の比較が確認できない (単一世代/世代比較なし)。"
    elif category_fit == "unknown":
        decision = ScreeningStatus.supplementary
        reject_reason = "6カテゴリのいずれにも明確に該当しない。"
    else:
        decision = ScreeningStatus.accepted

    if results_discussion_mixed and decision == ScreeningStatus.accepted:
        # 採用相当でも章混在があれば人間確認に回す
        decision = ScreeningStatus.needs_review
        reject_reason = "採用候補だが Results/Discussion 混在のため要確認。"

    # 信頼度: 満たした観点の割合をベースに
    fits = [country_fit, workplace_fit, generation_fit, fulltext_fit, category_fit != "unknown"]
    confidence = round(sum(fits) / len(fits), 3)
    if decision == ScreeningStatus.accepted:
        confidence = round(min(0.99, 0.5 + 0.1 * sum(fits)), 3)

    return ScreeningResult(
        decision=decision,
        confidence=confidence,
        country_fit=country_fit,
        workplace_fit=workplace_fit,
        generation_fit=generation_fit,
        fulltext_fit=fulltext_fit,
        category_fit=category_fit,
        secondary_category=secondary,
        duplicate_fit=True,  # 重複は duplicate_checker が別途判定
        document_type=doc_type,
        translation_required=translation_required,
        reasons=reasons,
        evidence_sections=evidence,
        warnings=warnings,
    )
