"""QualityAssuranceAgent: autopilot 各ラウンド後の品質検査 (最重要)。

N に不適合な論文が accepted のまま残っていないかを 20 項目で検査し、
見つかった不適合 accepted は needs_review に落として **N から除外** する。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis import is_analysis_n
from ..artifacts import has_cleaned_text, has_metadata_json
from ..models import DocumentType, PaperRecord, ScreeningStatus
from .base import AgentResult, logger


@dataclass
class QACheck:
    num: int
    name: str
    passed: bool
    offending: list[str] = field(default_factory=list)
    action: str = ""


@dataclass
class QAReport:
    checks: list[QACheck] = field(default_factory=list)
    demoted: list[tuple[str, str]] = field(default_factory=list)  # (paper_id, reason)
    n_before: int = 0
    n_after: int = 0

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[QACheck]:
        return [c for c in self.checks if not c.passed]


def _is_th_vn(rec: PaperRecord) -> bool:
    return (rec.target_country or "").strip().lower() in ("thailand", "vietnam")


class QualityAssuranceAgent:
    name = "QualityAssuranceAgent"

    def run(self, db, *, require_artifacts: bool = False) -> QAReport:
        report = QAReport()
        papers = db.all()
        report.n_before = sum(1 for r in papers if is_analysis_n(r))

        def demote(rec: PaperRecord, reason: str, *, same_dataset: bool = False) -> None:
            rec.screening_status = ScreeningStatus.needs_review
            if same_dataset:
                rec.same_dataset_warning = True
            rec.rejection_reason = reason
            rec.notes = (rec.notes + " | " if rec.notes else "") + f"[QA] {reason}"
            db.upsert(rec)
            report.demoted.append((rec.paper_id, reason))

        accepted = [r for r in papers if r.screening_status == ScreeningStatus.accepted]
        n_ids = {r.paper_id for r in papers if is_analysis_n(r)}
        cand_ids = {c.candidate_id for c in db.all_candidates()}

        # 1,2: harvest/download(候補) が N に混入していないか (テーブル分離で構造的に保証)
        leak = sorted(n_ids & cand_ids)
        report.checks.append(QACheck(1, "harvest件数がNに混入していないか", not leak, leak))
        report.checks.append(QACheck(2, "download成功件数がNに混入していないか", not leak, leak))

        # 3-14: accepted のうち N 条件を満たさないものを検出して降格
        def collect(pred):
            return [r for r in accepted
                    if r.screening_status == ScreeningStatus.accepted and pred(r)]

        rules = [
            (3, "duplicateがNに入っていないか", lambda r: bool((r.duplicate_of or "").strip()),
             "accepted だが duplicate_of がある"),
            (4, "same_dataset_possibleがNに入っていないか", lambda r: r.same_dataset_warning,
             "accepted だが same_dataset_warning=true", True),
            (8, "teaching_caseがNに入っていないか",
             lambda r: r.document_type == DocumentType.teaching_case, "accepted だが teaching_case"),
            (9, "conference_abstractがNに入っていないか",
             lambda r: r.document_type == DocumentType.conference_abstract,
             "accepted だが conference_abstract"),
            (10, "単一世代研究がNに入っていないか",
             lambda r: (r.number_of_generations or 0) < 2, "accepted だが単一世代 (<2)"),
            (11, "対象国がThailand/Vietnam以外がNに入っていないか",
             lambda r: not _is_th_vn(r), "accepted だが対象国が TH/VN でない"),
            (12, "職場文脈なしがNに入っていないか", lambda r: not r.workplace_fit,
             "accepted だが workplace_fit=false"),
            (13, "full_text_available=falseがNに入っていないか",
             lambda r: not r.full_text_available, "accepted だが full_text_available=false"),
            (14, "number_of_generations<2がNに入っていないか",
             lambda r: (r.number_of_generations or 0) < 2, "accepted だが世代数<2"),
        ]
        for rule in rules:
            num, cname, pred, reason = rule[0], rule[1], rule[2], rule[3]
            same_ds = len(rule) > 4 and rule[4]
            offending = collect(pred)
            for r in offending:
                demote(r, reason, same_dataset=same_ds)
            report.checks.append(
                QACheck(num, cname, not offending, [r.paper_id for r in offending],
                        "needs_review に降格" if offending else "")
            )

        # 5,6,7: needs_review/supplementary/rejected が N に入っていないか (構造的に不可)
        for num, cname, st in [(5, "needs_reviewがNに入っていないか", ScreeningStatus.needs_review),
                               (6, "supplementaryがNに入っていないか", ScreeningStatus.supplementary),
                               (7, "rejectedがNに入っていないか", ScreeningStatus.rejected)]:
            bad = [r.paper_id for r in papers if r.paper_id in n_ids and r.screening_status == st]
            report.checks.append(QACheck(num, cname, not bad, bad))

        # accepted を再取得 (降格反映)
        accepted = [r for r in db.all() if r.screening_status == ScreeningStatus.accepted]

        # 15,16: accepted に cleaned text / metadata json が存在するか
        if require_artifacts:
            no_clean = [r for r in accepted if not has_cleaned_text(r)
                        and r.original_language not in ("th", "vi")]
            for r in no_clean:
                demote(r, "accepted だが cleaned text が無い")
            report.checks.append(QACheck(15, "accepted論文にcleaned textが存在するか",
                                         not no_clean, [r.paper_id for r in no_clean],
                                         "needs_review に降格" if no_clean else ""))
            accepted = [r for r in db.all() if r.screening_status == ScreeningStatus.accepted]
            no_meta = [r for r in accepted if not has_metadata_json(r)]
            for r in no_meta:
                demote(r, "accepted だが metadata json が無い")
            report.checks.append(QACheck(16, "accepted論文にmetadata jsonが存在するか",
                                         not no_meta, [r.paper_id for r in no_meta],
                                         "needs_review に降格" if no_meta else ""))
            accepted = [r for r in db.all() if r.screening_status == ScreeningStatus.accepted]
        else:
            report.checks.append(QACheck(15, "accepted論文にcleaned textが存在するか (dry-runでは検査せず)", True))
            report.checks.append(QACheck(16, "accepted論文にmetadata jsonが存在するか (dry-runでは検査せず)", True))

        # 17: DOI または source_url が保存されているか
        no_src = [r for r in accepted if not ((r.doi or "").strip() or (r.source_url or "").strip())]
        for r in no_src:
            demote(r, "accepted だが DOI も source_url も無い")
        report.checks.append(QACheck(17, "DOIまたはsource_urlが保存されているか",
                                     not no_src, [r.paper_id for r in no_src],
                                     "needs_review に降格" if no_src else ""))
        accepted = [r for r in db.all() if r.screening_status == ScreeningStatus.accepted]

        # 18,19,20: accepted 内の重複 (DOI / normalized_title / データ署名)
        def dedup_group(key_fn, num, cname, same_ds):
            seen: dict[str, str] = {}
            offending = []
            for r in sorted(accepted, key=lambda x: x.paper_id):
                key = key_fn(r)
                if not key:
                    continue
                if key in seen:
                    demote(r, f"{cname}: '{seen[key]}' と重複", same_dataset=same_ds)
                    offending.append(r.paper_id)
                else:
                    seen[key] = r.paper_id
            report.checks.append(QACheck(num, cname, not offending, offending,
                                         "needs_review に降格" if offending else ""))

        dedup_group(lambda r: (r.doi or "").strip().lower(),
                    18, "同じDOIが複数acceptedになっていないか", False)
        accepted = [r for r in db.all() if r.screening_status == ScreeningStatus.accepted]
        dedup_group(lambda r: (r.normalized_title or "").strip().lower(),
                    19, "同じnormalized_titleが複数acceptedになっていないか", False)
        accepted = [r for r in db.all() if r.screening_status == ScreeningStatus.accepted]
        dedup_group(lambda r: "|".join([
                        (r.authors or "").lower(), str(r.sample_size or ""),
                        (r.target_country or "").lower(), (r.generation_groups or "").lower()])
                        if (r.sample_size and r.authors) else "",
                    20, "同一データ署名(authors/sample/country/gens)が複数acceptedでないか", True)

        report.n_after = sum(1 for r in db.all() if is_analysis_n(r))
        logger.info("QA: N %d -> %d (降格 %d 件)", report.n_before, report.n_after,
                    len(report.demoted))
        return report
