"""Shared agent status dataclass for real-time pipeline progress tracking."""

from dataclasses import dataclass, field


@dataclass
class AgentStatus:
    """Tracks the real-time state of a running pipeline agent."""

    agent: str = ""
    feature_slug: str = ""
    phase: str = ""
    started_at: float = 0.0
    lines: list[str] = field(default_factory=list)
    running: bool = False
