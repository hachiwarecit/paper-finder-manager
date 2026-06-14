"""役割別エージェント群。

実際に Claude の別インスタンスを生成するのではなく、コード設計上の役割分担。
SupervisorAgent が全体を統括して autopilot ラウンドを回す。
"""
from .analysis_n_agent import AnalysisNAgent
from .base import AgentResult
from .candidate_screening import CandidateScreeningAgent
from .cleaner_agent import CleanerAgent
from .download_agent import DownloadAgent
from .duplicate_check import DuplicateCheckAgent
from .fulltext_screening import FullTextScreeningAgent
from .harvester_agent import HarvesterAgent
from .legal_access import LegalAccessAgent
from .metadata_extraction import MetadataExtractionAgent
from .quality_assurance import QAReport, QualityAssuranceAgent
from .search_planner import SearchPlannerAgent
from .supervisor import AutopilotConfig, AutopilotResult, SupervisorAgent

__all__ = [
    "AgentResult",
    "SearchPlannerAgent",
    "HarvesterAgent",
    "CandidateScreeningAgent",
    "DuplicateCheckAgent",
    "LegalAccessAgent",
    "DownloadAgent",
    "FullTextScreeningAgent",
    "MetadataExtractionAgent",
    "CleanerAgent",
    "AnalysisNAgent",
    "QualityAssuranceAgent",
    "QAReport",
    "SupervisorAgent",
    "AutopilotConfig",
    "AutopilotResult",
]
