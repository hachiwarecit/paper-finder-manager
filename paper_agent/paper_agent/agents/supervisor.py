"""SupervisorAgent: 全エージェントを統括し autopilot ラウンドを回す。

エラーが出ても全体停止せずログに残して次へ進む。停止条件に達したら理由を出す。
dry-run では PDF を取得せず、候補収集・候補重複・スコアリング・search_history 保存まで。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ..analysis import is_analysis_n, summarize
from ..db import PaperDB
from ..ingest import ingest_file
from ..models import CandidateStatus
from ..utils import DATA_DIR, get_logger, now_iso, slugify
from .analysis_n_agent import AnalysisNAgent
from .candidate_screening import CandidateScreeningAgent, _has_multigen_signal
from .cleaner_agent import CleanerAgent
from .download_agent import DownloadAgent
from .duplicate_check import DuplicateCheckAgent
from .fulltext_screening import FullTextScreeningAgent
from .harvester_agent import HarvesterAgent
from .legal_access import LegalAccessAgent
from .metadata_extraction import MetadataExtractionAgent
from .quality_assurance import QualityAssuranceAgent, QAReport
from .search_planner import SearchPlannerAgent

logger = get_logger("paper_agent.agents")

LEGAL_NOTES_OK = ("open access", "official/repository")
EXPORTS = DATA_DIR / "10_exports"


@dataclass
class AutopilotConfig:
    target_n: int = 100
    countries: list[str] = field(default_factory=lambda: ["TH", "VN"])
    categories: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5, 6])
    per_query_limit: int = 50
    dry_run: bool = False
    qa_strict: bool = False
    max_rounds: int | None = None
    auto_approve_threshold: float = 0.6


@dataclass
class AutopilotResult:
    rounds: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    final_n: int = 0
    qa_reports: list[QAReport] = field(default_factory=list)
    settlement: dict = field(default_factory=dict)
    summary_path: Path | None = None
    qa_report_path: Path | None = None


class SupervisorAgent:
    name = "SupervisorAgent"

    def __init__(self, config: AutopilotConfig):
        self.config = config
        self.planner = SearchPlannerAgent(config.countries, config.categories)
        self.harvester = HarvesterAgent()
        self.cand_screen = CandidateScreeningAgent()
        self.dup = DuplicateCheckAgent()
        self.legal = LegalAccessAgent()
        self.downloader = DownloadAgent()
        self.fulltext = FullTextScreeningAgent()
        self.metadata = MetadataExtractionAgent()
        self.cleaner = CleanerAgent()
        self.analysis = AnalysisNAgent()
        self.qa = QualityAssuranceAgent()

    # ------------------------------------------------------------------
    # 自動承認 (section 5) — すべての条件を満たした候補だけ承認。
    # 承認されなかった候補には auto_approve_blockers を必ず記録する。
    # ------------------------------------------------------------------
    def _auto_approve_blockers(self, c) -> list[str]:
        blockers: list[str] = []
        if c.duplicate_status.value != "new_candidate":
            blockers.append(f"duplicate_status={c.duplicate_status.value}")
        if c.target_country not in ("Thailand", "Vietnam"):
            blockers.append("target_country_not_TH_VN")
        if c.target_country_source not in ("title", "abstract", "full_text", "metadata"):
            blockers.append(f"target_country_source={c.target_country_source}")
        if not (c.target_country_evidence or "").strip():
            blockers.append("target_country_evidence_empty")
        if not _has_multigen_signal(c):
            blockers.append("generation_evidence_insufficient")
        if not c.workplace_keywords:
            blockers.append("workplace_evidence_missing")
        if not c.pdf_url:
            blockers.append("pdf_url_missing")
        if c.legality_note not in LEGAL_NOTES_OK:
            blockers.append("legality_unknown")
        if c.document_type_guess in ("teaching_case", "conference_abstract"):
            blockers.append(f"document_type={c.document_type_guess}")
        if c.candidate_score < self.config.auto_approve_threshold:
            blockers.append(f"candidate_score<{self.config.auto_approve_threshold}")
        return blockers

    def _auto_approve(self, candidate_ids: list[str], db: PaperDB) -> list[str]:
        approved = []
        for cid in candidate_ids:
            c = db.get_candidate(cid)
            if c is None:
                continue
            blockers = self._auto_approve_blockers(c)
            if not blockers:
                c.candidate_status = CandidateStatus.approved_for_download
                c.auto_approve_reason = (
                    f"score={c.candidate_score} target={c.target_country}"
                    f"({c.target_country_source}) legal={c.legality_note}"
                )
                c.auto_approve_blockers = ""
                approved.append(cid)
            else:
                c.auto_approve_blockers = "; ".join(blockers)
                c.auto_approve_reason = ""
            db.upsert_candidate(c)
        return approved

    # ------------------------------------------------------------------
    # ダウンロード済み候補の取り込み
    # ------------------------------------------------------------------
    def _ingest_downloaded(self, db: PaperDB) -> int:
        n = 0
        for c in db.candidates_by_status(CandidateStatus.downloaded.value):
            country = (c.country or "unknown").upper()
            base = EXPORTS.parent / "02_downloaded" / country
            stem = slugify(c.candidate_id)
            files = list(base.glob(f"{stem}.*")) if base.exists() else []
            if not files:
                continue
            seed = {
                "doi": c.doi, "source_url": c.source_url, "pdf_url": c.pdf_url,
                "category": c.category, "target_country": c.target_country,
                "target_country_source": c.target_country_source,
                "target_country_evidence": c.target_country_evidence,
                "query_country": c.query_country,
                "title": c.title, "authors": c.authors, "candidate_id": c.candidate_id,
                "source_name": c.source_name,
            }
            rec = ingest_file(files[0], country, db, seed=seed)
            if rec is not None:
                c.candidate_status = CandidateStatus.screened
                db.upsert_candidate(c)
                n += 1
        return n

    # ------------------------------------------------------------------
    # ダウンロード後の論文処理 (ingest→dedupe→screen→metadata→clean)
    # ------------------------------------------------------------------
    def _process_after_download(self, db: PaperDB) -> int:
        ingested = self._ingest_downloaded(db)
        if db.count():
            self.dup.paper_stage(db)
            self.fulltext.run(db)
            self.metadata.run(db)
            self.cleaner.run(db)
        return ingested

    def _settle_pending(self, db: PaperDB) -> dict:
        """ループで処理されなかった approved_for_download を必ずダウンロード・処理する。

        前回 dry-run で承認しただけの候補や、検索空間を尽くして本ラウンドが回らなかった
        場合でも、承認済み候補を確実に DownloadAgent に通す。
        """
        pending = db.candidates_by_status(CandidateStatus.approved_for_download.value)
        dl_stats = self.downloader.run(db).info if pending else {}
        ingested = self._process_after_download(db)
        return {"pending_approved": len(pending), "ingested": ingested, "download": dl_stats}

    # ------------------------------------------------------------------
    # メインループ
    # ------------------------------------------------------------------
    def run(self, db: PaperDB, fetch=None) -> AutopilotResult:
        cfg = self.config
        result = AutopilotResult()
        total_queries = len(self.planner.plan())
        max_rounds = cfg.max_rounds or (total_queries + 1)
        prev_n = sum(1 for r in db.all() if is_analysis_n(r))
        consecutive_zero = 0

        for round_i in range(1, max_rounds + 1):
            qitem = self.planner.next_query(db)
            if qitem is None:
                result.stop_reason = "検索空間を尽くした (未実行クエリなし)"
                break

            rlog: dict = {"round": round_i, "query": qitem["query"],
                          "country": qitem["country"], "category": qitem["category"]}
            try:
                # Harvest (PDFは取得しない)
                _, cands = self.harvester.run(qitem, cfg.per_query_limit, db, fetch=fetch)
                cand_ids = [c.candidate_id for c in cands]
                rlog["harvested"] = len(cands)

                # Candidate screening (scoring)
                self.cand_screen.run(cands, db)
                # Legal access classification
                self.legal.run([db.get_candidate(cid) for cid in cand_ids if db.get_candidate(cid)], db)
                # 自動承認 (high-confidence のみ)
                approved = self._auto_approve(cand_ids, db)
                rlog["auto_approved"] = len(approved)

                if not cfg.dry_run:
                    dl = self.downloader.run(db).info
                    rlog["download_attempted"] = dl.get("attempted", 0)
                    rlog["download_success"] = dl.get("downloaded", 0)
                    rlog["download_failed"] = dl.get("failed", 0)
                    rlog["download_skipped"] = dl.get("skipped", 0)
                    rlog["ingested"] = self._process_after_download(db)
            except Exception as exc:  # noqa: BLE001
                logger.error("ラウンド %d でエラー: %s", round_i, exc)
                rlog["error"] = str(exc)

            # Analysis N + QA (各ラウンド後)
            qa = self.qa.run(db, require_artifacts=not cfg.dry_run)
            result.qa_reports.append(qa)
            n_after = qa.n_after
            rlog["qa_demoted"] = len(qa.demoted)
            rlog["n_after"] = n_after
            new_accepted = n_after - prev_n
            rlog["new_accepted"] = new_accepted
            prev_n = n_after
            result.rounds.append(rlog)

            # 停止判定
            if n_after >= cfg.target_n:
                result.stop_reason = f"target_N 到達 (N={n_after} >= {cfg.target_n})"
                break
            if not cfg.dry_run:
                consecutive_zero = consecutive_zero + 1 if new_accepted <= 0 else 0
                if consecutive_zero >= 3:
                    result.stop_reason = "3ラウンド連続で新規acceptedが0"
                    break
        else:
            result.stop_reason = f"max_rounds 到達 ({max_rounds})"

        # --- settlement: 非dry-runでは承認済み候補を必ず処理する ---
        if not cfg.dry_run:
            result.settlement = self._settle_pending(db)

        # --- 最終 QA を必ず1回記録 (rounds=0 でも qa_reports を空にしない) ---
        final_qa = self.qa.run(db, require_artifacts=not cfg.dry_run)
        final_qa.label = "final/settlement"
        result.qa_reports.append(final_qa)

        result.final_n = sum(1 for r in db.all() if is_analysis_n(r))
        self._write_outputs(db, result)
        return result

    # ------------------------------------------------------------------
    # 出力
    # ------------------------------------------------------------------
    def _write_outputs(self, db: PaperDB, result: AutopilotResult) -> None:
        EXPORTS.mkdir(parents=True, exist_ok=True)
        try:
            from ..exporter import export
            export("xlsx", db=db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("xlsx 出力失敗: %s", exc)
        try:
            from ..reporter import write_report
            write_report("md", db=db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("report.md 出力失敗: %s", exc)

        self._write_csvs(db)
        result.summary_path = self._write_summary(db, result)
        result.qa_report_path = self._write_qa_report(result)

    def _write_csvs(self, db: PaperDB) -> None:
        papers = db.all()
        # accepted_for_analysis.csv (N のみ)
        with (EXPORTS / "accepted_for_analysis.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["paper_id", "country", "category", "title", "authors", "year",
                        "doi", "target_country", "number_of_generations", "source_url"])
            for r in papers:
                if is_analysis_n(r):
                    w.writerow([r.paper_id, r.country, r.category, r.title, r.authors,
                                r.year or "", r.doi or "", r.target_country,
                                r.number_of_generations, r.source_url or ""])
        # rejected_reasons.csv
        with (EXPORTS / "rejected_reasons.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["paper_id", "screening_status", "rejection_reason", "title"])
            for r in papers:
                if r.screening_status.value in ("rejected", "supplementary", "needs_review"):
                    w.writerow([r.paper_id, r.screening_status.value, r.rejection_reason, r.title])
        # duplicate_groups.csv
        with (EXPORTS / "duplicate_groups.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["paper_id", "duplicate_of", "duplicate_group_id",
                        "duplicate_confidence", "same_dataset_warning", "title"])
            for r in papers:
                if r.duplicate_of or r.same_dataset_warning:
                    w.writerow([r.paper_id, r.duplicate_of or "", r.duplicate_group_id or "",
                                r.duplicate_confidence if r.duplicate_confidence is not None else "",
                                "YES" if r.same_dataset_warning else "", r.title])
        # search_history.csv
        with (EXPORTS / "search_history.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["query", "country", "category", "n_results", "last_run_at"])
            for h in db.all_searches():
                w.writerow([h.get("query"), h.get("country"), h.get("category"),
                            h.get("n_results"), h.get("last_run_at")])

    def _write_summary(self, db: PaperDB, result: AutopilotResult) -> Path:
        papers = db.all()
        s = summarize(papers)
        cands = db.all_candidates()
        harvest_total = len(cands)

        def _count_by(attr):
            d: dict = {}
            for c in cands:
                v = getattr(c, attr)
                v = v.value if hasattr(v, "value") else v
                d[v] = d.get(v, 0) + 1
            return d

        status_counts = _count_by("candidate_status")
        dlstatus_counts = _count_by("download_status")
        tcsource_counts = _count_by("target_country_source")
        dl_success = dlstatus_counts.get("success", 0)
        dl_failed = dlstatus_counts.get("failed", 0)
        dl_skipped = dlstatus_counts.get("skipped", 0)
        dl_attempted = dl_success + dl_failed + dlstatus_counts.get("attempted", 0)
        auto_approved = status_counts.get("approved_for_download", 0) + \
            sum(1 for c in cands if c.auto_approve_reason)

        # download_attempted=0 のときの理由内訳 (auto_approve_blockers を集計)
        blocker_counts: dict[str, int] = {}
        for c in cands:
            for b in (c.auto_approve_blockers or "").split("; "):
                b = b.strip()
                if b:
                    key = b.split("=")[0] if "=" in b else b
                    blocker_counts[key] = blocker_counts.get(key, 0) + 1
        # download skip 理由
        skip_reasons: dict[str, int] = {}
        for c in cands:
            if c.download_status == "skipped" and c.download_error:
                key = c.download_error.split(":")[0]
                skip_reasons[key] = skip_reasons.get(key, 0) + 1

        # ダウンロード診断 (なぜ download されなかったか)
        from ..downloader import LEGAL_NOTES_OK as _DLOK
        approved_ever = sum(1 for c in cands if c.auto_approve_reason)
        approved_now_list = [c for c in cands if c.candidate_status.value == "approved_for_download"]
        approved_now = len(approved_now_list)
        downloadable_now = [c for c in approved_now_list
                            if c.pdf_url and c.legality_note in _DLOK
                            and c.duplicate_status.value == "new_candidate"
                            and c.download_status in ("", "not_attempted")]
        dl_no_reason = {
            "approved_for_download が0だから試行対象なし": 1 if approved_now == 0 else 0,
            "approved はあるが pdf_url がない": sum(1 for c in approved_now_list if not c.pdf_url),
            "approved はあるが legality_note が不許可":
                sum(1 for c in approved_now_list if c.pdf_url and c.legality_note not in _DLOK),
            "approved はあるが duplicate_status が new_candidate ではない":
                sum(1 for c in approved_now_list if c.duplicate_status.value != "new_candidate"),
            "DownloadAgent未実行/バグ (ダウンロード可能なのに未試行=FAIL)": len(downloadable_now),
        }
        download_fail = bool(downloadable_now) and dl_attempted == 0
        # approved のまま not_attempted で残っているもの (規約違反)
        stuck_not_attempted = [c.candidate_id for c in approved_now_list
                               if c.download_status in ("", "not_attempted")]

        lines: list[str] = []
        lines.append("# autopilot summary")
        lines.append("")
        lines.append(f"- 生成日時: {now_iso()}")
        lines.append(f"- 目標 N (target_N): {self.config.target_n}")
        lines.append(f"- dry-run: {self.config.dry_run}")
        lines.append(f"- 停止理由: **{result.stop_reason}**")
        lines.append("")
        lines.append("## N (最終確定)")
        lines.append("")
        lines.append(f"- 最終 accepted N: **{s.analysis_n}**")
        lines.append(f"- TH accepted N: {s.th_n}")
        lines.append(f"- VN accepted N: {s.vn_n}")
        if self.config.target_n > s.analysis_n:
            lines.append("")
            lines.append(f"> ⚠ 目標 {self.config.target_n} に対し {s.analysis_n} 本 "
                         f"({self.config.target_n - s.analysis_n} 本不足)。"
                         "水増しはせず、条件に合う論文のみを N にしています。")
        lines.append("")
        lines.append("### 国×カテゴリ別 N")
        lines.append("")
        if s.country_category:
            lines.append("| 国 | カテゴリ | N |")
            lines.append("|----|----------|---|")
            for (country, cat), n in sorted(s.country_category.items()):
                lines.append(f"| {country} | {cat} | {n} |")
        else:
            lines.append("(N=0)")
        lines.append("")
        lines.append("## 集計")
        lines.append("")
        lines.append(f"- harvest 総数 (候補): {harvest_total}")
        lines.append(f"- candidate 総数: {harvest_total}")
        lines.append(f"- auto_approved_count: {auto_approved}")
        lines.append(f"- download_attempted_count: {dl_attempted}")
        lines.append(f"- download_success_count: {dl_success}")
        lines.append(f"- download_failed_count: {dl_failed}")
        lines.append(f"- download_skipped_count: {dl_skipped}")
        lines.append(f"- 重複除外数 (duplicate): {s.excluded_duplicates}")
        lines.append(f"- same_dataset_possible 数: {s.same_dataset_possible}")
        lines.append(f"- supplementary 数: {s.supplementary}")
        lines.append(f"- rejected 数: {s.rejected}")
        lines.append(f"- needs_review 数: {s.needs_review}")
        lines.append("")
        lines.append("### candidate_status 別の件数")
        lines.append("")
        for k, v in sorted(status_counts.items()):
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append("### download_status 別の件数")
        lines.append("")
        for k, v in sorted(dlstatus_counts.items()):
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append("### target_country_source 別の候補数")
        lines.append("")
        for k, v in sorted(tcsource_counts.items()):
            lines.append(f"- {k}: {v}")
        lines.append("")

        # ---- ダウンロード診断 (常に出す) ----
        lines.append("## ダウンロード診断")
        lines.append("")
        verdict = "FAIL" if download_fail else "OK"
        lines.append(f"- 判定: **{verdict}**")
        if not self.config.dry_run and download_fail:
            lines.append("- ⚠ **FAIL: ダウンロード可能な approved_for_download があるのに "
                         "download が一度も試行されていません (DownloadAgentのバグ)。**")
        lines.append(f"- approved (累計, auto_approve済み): {approved_ever}")
        lines.append(f"- approved_for_download (現在): {approved_now}")
        lines.append(f"- ダウンロード可能で未試行: {len(downloadable_now)}")
        lines.append(f"- download_attempted_count: {dl_attempted}")
        lines.append(f"- approved のまま not_attempted で残存 (規約違反): {len(stuck_not_attempted)}")
        lines.append("")
        if dl_attempted == 0:
            lines.append("### download_attempted=0 の理由")
            lines.append("")
            for k, v in dl_no_reason.items():
                if v:
                    lines.append(f"- {k}: {v} 件")
            if blocker_counts:
                lines.append("")
                lines.append("auto_approve がブロックされた内訳 (候補が承認に至らなかった理由):")
                for k, v in sorted(blocker_counts.items(), key=lambda kv: -kv[1]):
                    lines.append(f"- {k}: {v} 件")
            lines.append("")
        if skip_reasons:
            lines.append("### download_skipped_reasons")
            lines.append("")
            for k, v in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
                lines.append(f"- {k}: {v} 件")
            lines.append("")

        # ---- papers / N が 0 の理由 ----
        if s.analysis_n == 0 or len(papers) == 0:
            lines.append("## papers / N が 0 の理由")
            lines.append("")
            if self.config.dry_run:
                lines.append("- dry-run のため PDF を取得せず、論文 (papers) を作らないため N=0 です。")
            elif len(papers) == 0:
                if approved_now == 0 and approved_ever == 0:
                    lines.append("- 自動承認された候補が無く、ダウンロード対象がありませんでした。")
                    lines.append("  (auto_approve_blockers の内訳を参照)")
                elif dl_success == 0:
                    lines.append("- 承認候補はあったが PDF 取得に成功せず、論文が作られませんでした。")
                    lines.append("  (download_status / download_error を参照)")
                else:
                    lines.append("- PDF は取得したが、本文抽出・スクリーニングで accepted に至りませんでした。")
            else:
                lines.append("- 論文はあるが N 条件 (accepted・重複なし・証拠ベース対象国・2世代以上・"
                             "職場文脈・本文あり) を満たすものがありませんでした。")
            lines.append("")

        lines.append("## 実行済み検索クエリ")
        lines.append("")
        for h in db.all_searches():
            lines.append(f"- [{h.get('country')}/{h.get('category')}] {h.get('query')} "
                         f"→ {h.get('n_results')} 件")
        lines.append("")
        lines.append("## N に入った論文一覧")
        lines.append("")
        if s.n_records:
            for r in s.n_records:
                lines.append(f"- `{r.paper_id}` [{r.target_country}/{r.category}] {r.title}")
        else:
            lines.append("- (なし)")
        lines.append("")
        lines.append("## N に入らなかった主な理由")
        lines.append("")
        lines.append(f"- 重複 (duplicate): {s.excluded_duplicates}")
        lines.append(f"- 同一データ疑い (needs_review): {s.same_dataset_possible}")
        lines.append(f"- 補助文献 (supplementary): {s.supplementary}")
        lines.append(f"- 除外 (rejected): {s.rejected}")
        lines.append(f"- 要確認 (needs_review): {s.needs_review}")
        lines.append("")
        lines.append("## ラウンド履歴")
        lines.append("")
        lines.append("| round | query | harvested | auto_approved | new_accepted | N | QA降格 |")
        lines.append("|-------|-------|-----------|---------------|--------------|---|--------|")
        for r in result.rounds:
            lines.append(f"| {r.get('round')} | {r.get('query','')[:40]} | "
                         f"{r.get('harvested',0)} | {r.get('auto_approved',0)} | "
                         f"{r.get('new_accepted',0)} | {r.get('n_after',0)} | {r.get('qa_demoted',0)} |")
        out = EXPORTS / "autopilot_summary.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        return out

    def _write_qa_report(self, result: AutopilotResult) -> Path:
        lines: list[str] = []
        lines.append("# QA report (QualityAssuranceAgent)")
        lines.append("")
        lines.append(f"- 生成日時: {now_iso()}")
        lines.append(f"- QA実行回数 (ラウンド数): {len(result.qa_reports)}")
        any_fail = any(not c.passed for qa in result.qa_reports for c in qa.checks)
        lines.append(f"- 総合判定: **{'要対応あり' if any_fail else 'OK'}**")
        if result.settlement:
            st = result.settlement
            lines.append(f"- settlement: pending_approved={st.get('pending_approved', 0)} / "
                         f"ingested={st.get('ingested', 0)} / download={st.get('download', {})}")
        lines.append("")

        # 各ラウンドのメトリクス
        if result.rounds:
            lines.append("## ラウンド別メトリクス")
            lines.append("")
            lines.append("| round | harvested | auto_approved | dl_attempted | dl_success | dl_failed | dl_skipped | N | QA降格 |")
            lines.append("|-------|-----------|---------------|--------------|-----------|-----------|------------|---|--------|")
            for r in result.rounds:
                lines.append(f"| {r.get('round')} | {r.get('harvested',0)} | {r.get('auto_approved',0)} | "
                             f"{r.get('download_attempted',0)} | {r.get('download_success',0)} | "
                             f"{r.get('download_failed',0)} | {r.get('download_skipped',0)} | "
                             f"{r.get('n_after',0)} | {r.get('qa_demoted',0)} |")
            lines.append("")

        for i, qa in enumerate(result.qa_reports, 1):
            tag = f" [{qa.label}]" if qa.label else ""
            lines.append(f"## QA #{i}{tag}  (N {qa.n_before} → {qa.n_after}, 降格 {len(qa.demoted)} 件)")
            lines.append("")
            lines.append("| # | 検査項目 | 結果 | 該当 | 対応 |")
            lines.append("|---|----------|------|------|------|")
            for c in qa.checks:
                status = "OK" if c.passed else "要対応"
                offending = ", ".join(c.offending[:5]) + (" ..." if len(c.offending) > 5 else "")
                lines.append(f"| {c.num} | {c.name} | {status} | {offending} | {c.action} |")
            if qa.demoted:
                lines.append("")
                lines.append("降格 (needs_review / N から除外):")
                for pid, reason in qa.demoted:
                    lines.append(f"- `{pid}`: {reason}")
            lines.append("")
        out = EXPORTS / "qa_report.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        return out
