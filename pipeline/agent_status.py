"""Shared agent status dataclass for real-time pipeline progress tracking."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentStatus:
    """Tracks the real-time state of a running pipeline agent."""

    agent: str = ""
    feature_slug: str = ""
    phase: str = ""
    started_at: float = 0.0
    lines: list[str] = field(default_factory=list)
    running: bool = False
    pid: Optional[int] = None
    last_activity_at: Optional[float] = None
    kill_requested: bool = False
    restart_requested: bool = False
