"""Pydantic データモデル。

- PaperRecord     : 1論文の管理レコード (SQLite/Excel の1行)
- ScreeningResult : 採否判定の結果
- DuplicateResult : 重複判定の結果

不明な値は推測せず "unknown" / None のままにする方針。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .utils import now_iso


class ScreeningStatus(str, Enum):
    candidate = "candidate"
    accepted = "accepted"
    supplementary = "supplementary"
    rejected = "rejected"
    duplicate = "duplicate"
    needs_review = "needs_review"


class DocumentType(str, Enum):
    journal_article = "journal_article"
    thesis = "thesis"
    conference_paper = "conference_paper"
    conference_abstract = "conference_abstract"
    teaching_case = "teaching_case"
    report = "report"
    unknown = "unknown"


class DuplicateType(str, Enum):
    exact_duplicate = "exact_duplicate"
    probable_duplicate = "probable_duplicate"
    same_dataset_possible = "same_dataset_possible"
    not_duplicate = "not_duplicate"


class PaperRecord(BaseModel):
    """論文1件の管理レコード。"""

    paper_id: str
    country: str = "unknown"                 # 整理上の国コード (TH/VN/JP など)
    category: str = "unknown"                # primary category (例: category_5)

    title: str = ""
    normalized_title: str = ""
    authors: str = ""                        # ";" 区切り文字列で保持
    year: Optional[int] = None
    doi: Optional[str] = None

    source_name: str = "unknown"
    source_url: Optional[str] = None
    pdf_url: Optional[str] = None

    local_pdf_path: Optional[str] = None
    local_text_path: Optional[str] = None

    original_language: str = "unknown"
    analysis_language: str = "en"
    full_text_available: bool = False

    target_country: str = "unknown"          # 調査対象国 (著者所属ではない)
    organization_context: str = "unknown"    # 職場文脈の説明
    workplace_fit: bool = False              # 職場・組織文脈の有無 (N判定に使用)
    generation_groups: str = ""              # 検出された世代グループ (";" 区切り)
    number_of_generations: int = 0
    sample_size: Optional[int] = None
    research_method: str = "unknown"
    main_topic: str = "unknown"

    peer_reviewed: Optional[bool] = None
    document_type: DocumentType = DocumentType.unknown
    license_status: str = "unknown"
    legality_note: str = ""

    screening_status: ScreeningStatus = ScreeningStatus.candidate
    rejection_reason: str = ""

    duplicate_group_id: Optional[str] = None
    duplicate_of: Optional[str] = None
    duplicate_confidence: Optional[float] = None
    same_dataset_warning: bool = False

    notes: str = ""

    # ハッシュ (重複判定の高速化に使う。Excel には出さない)
    pdf_sha256: Optional[str] = None
    text_sha256: Optional[str] = None

    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def touch(self) -> None:
        self.updated_at = now_iso()


class ScreeningResult(BaseModel):
    """採否判定 (screener.py の出力)。"""

    decision: ScreeningStatus
    confidence: float = 0.0
    country_fit: bool = False
    workplace_fit: bool = False
    generation_fit: bool = False
    fulltext_fit: bool = False
    category_fit: str = "unknown"            # 例: category_5 / unknown
    secondary_category: str = "unknown"
    duplicate_fit: bool = True               # True=重複問題なし
    document_type: DocumentType = DocumentType.unknown
    translation_required: bool = False
    primary_reason: str = ""                  # 判定を決めた主要因 (台帳の rejection_reason 用)
    reasons: list[str] = Field(default_factory=list)
    evidence_sections: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DuplicateResult(BaseModel):
    """重複判定 (duplicate_checker.py の出力)。"""

    candidate_paper_id: str
    matched_paper_id: Optional[str] = None
    duplicate_type: DuplicateType = DuplicateType.not_duplicate
    confidence: float = 0.0
    matched_fields: list[str] = Field(default_factory=list)
    explanation: str = ""
    recommended_action: str = ""


# ===========================================================================
# 候補 (harvest 段階)
# ===========================================================================
class CandidateStatus(str, Enum):
    pending = "pending"
    approved_for_download = "approved_for_download"
    rejected = "rejected"
    duplicate = "duplicate"
    needs_review = "needs_review"
    downloaded = "downloaded"
    screened = "screened"


class CandidateDuplicateStatus(str, Enum):
    exact_duplicate = "exact_duplicate"
    probable_duplicate = "probable_duplicate"
    same_dataset_possible = "same_dataset_possible"
    new_candidate = "new_candidate"
    needs_review = "needs_review"


class Candidate(BaseModel):
    """検索 (harvest) で見つけた候補。PDF 取得前のメタデータ段階のレコード。

    重要: 候補は N (analysis_N) に数えない。最終的に download → ingest → screen を
    通って PaperRecord として accepted になった論文だけが N に数えられる。
    """

    candidate_id: str
    title: str = ""
    normalized_title: str = ""
    authors: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: str = ""

    source_name: str = "unknown"
    source_url: Optional[str] = None
    pdf_url: Optional[str] = None

    country: str = "unknown"                 # 整理用ラベル (TH/VN)
    category: str = "unknown"                # 指定カテゴリ (category_1..6)
    target_country: str = "unknown"          # 本文/抄録から推定した調査対象国

    generation_keywords: str = ""            # 検出した世代関連語 (";" 区切り)
    workplace_keywords: str = ""             # 検出した職場文脈語
    category_keywords: str = ""              # 検出したカテゴリ語
    document_type_guess: str = "unknown"
    open_access_flag: bool = False
    legality_note: str = "unknown"

    candidate_score: float = 0.0             # 人間確認の優先順位付け用 (自動採用しない)
    candidate_status: CandidateStatus = CandidateStatus.pending
    duplicate_status: CandidateDuplicateStatus = CandidateDuplicateStatus.new_candidate
    duplicate_of: Optional[str] = None
    same_dataset_warning: bool = False
    notes: str = ""

    # ダウンロード試行の記録
    download_status: str = ""                # "" / success / failed / skipped
    download_error: str = ""
    attempted_url: str = ""
    timestamp: str = ""

    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def touch(self) -> None:
        self.updated_at = now_iso()
