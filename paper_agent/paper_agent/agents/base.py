"""エージェント共通の基底。

各エージェントは役割を明確に分離した薄いクラスで、既存のロジック
(harvester / pipeline / screener / cleaner / analysis 等) を組み合わせて使う。
Claude の別インスタンスを生成するわけではなく、コード設計上の役割分担。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..utils import get_logger

logger = get_logger("paper_agent.agents")


@dataclass
class AgentResult:
    name: str
    ok: bool = True
    info: dict = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.messages.append(msg)
