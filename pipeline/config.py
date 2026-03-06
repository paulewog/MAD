"""Configuration loader for MAD pipeline."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_GLOBAL_CONFIG = Path("~/MAD/config.json").expanduser()
LOCAL_DIR = ".mad"
LOCAL_CONFIG = "config.json"


def find_config() -> Optional[Path]:
    """Find config file - prefers local .mad/config.json, falls back to ~/MAD/config.json.
    
    Searches:
    1. .mad/config.json in current directory
    2. Walk up from current dir to find .mad/config.json
    3. ~/MAD/config.json as global fallback (only if no local .mad in cwd tree)
    """
    cwd = Path.cwd()
    found_local = False
    
    # Check current directory and parents for local config
    for dir_path in [cwd] + list(cwd.parents):
        local_path = dir_path / LOCAL_DIR / LOCAL_CONFIG
        if local_path.exists():
            return local_path
        # Track if we hit a .mad dir (even without config)
        if (dir_path / LOCAL_DIR).exists():
            found_local = True
        # Stop if we hit home or root
        if dir_path == Path.home() or dir_path == Path("/"):
            break
    
    # If we're in a directory tree with no .mad/ at all, create local one
    if not found_local and not (cwd / LOCAL_DIR).exists():
        return None  # Signal to create local
    
    # Fall back to global config
    if DEFAULT_GLOBAL_CONFIG.exists():
        return DEFAULT_GLOBAL_CONFIG
    
    return None


def is_local_mode() -> bool:
    """Check if we're using local mode (config found in .mad/)."""
    config_path = find_config()
    if config_path is None:
        return False
    return LOCAL_DIR in str(config_path)


def get_mad_dir() -> Path:
    """Get the .mad directory path for current config."""
    config_path = find_config()
    if config_path is None:
        # Default to global
        return Path("~/MAD")
    return config_path.parent


def ensure_local_config(cwd: Path) -> Path:
    """Ensure .mad/config.json exists in cwd or parents, creating if needed.
    
    Returns the path to the config file.
    """
    local_dir = cwd / LOCAL_DIR
    config_path = local_dir / LOCAL_CONFIG
    
    if config_path.exists():
        return config_path
    
    # Create default config
    local_dir.mkdir(parents=True, exist_ok=True)
    default_config = {
        "current_agent": "claude",
        "agents": {
            "claude": {
                "command": "claude",
                "headless_flag": "-p",
                "headless_extra_flags": ["--dangerously-skip-permissions"]
            }
        },
        "boards": ["default"],
    }
    with open(config_path, "w") as f:
        json.dump(default_config, f, indent=2)
        f.write("\n")
    
    # Create board structure
    stages = [
        "ideas", "plan-inbox", "reviewing-plan", "requested-input", "approved", "spec-writing",
        "implementing", "testing", "review", "final-human-approval", "done", "rejected",
    ]
    for stage in stages:
        (local_dir / "boards" / "default" / stage).mkdir(parents=True, exist_ok=True)
    
    return config_path


@dataclass
class AgentConfig:
    """Configuration for a single AI agent."""

    name: str
    command: str
    headless_flag: str
    headless_extra_flags: List[str]


@dataclass
class ServerConfig:
    """Configuration for server push."""
    enabled: bool
    url: str
    api_key: str
    client_id: str
    push_interval_seconds: float = 10.0


class Config:
    """Loads and manages config.json from .mad/ or ~/MAD/."""

    def __init__(self, path: Optional[Path] = None, force_local: bool = False):
        if path is None:
            path = find_config()
            if path is None:
                # No config found - create local one in cwd
                path = ensure_local_config(Path.cwd())
            elif force_local:
                # Force local mode even if global config exists
                path = ensure_local_config(Path.cwd())
        
        self._path = path
        self._is_local = LOCAL_DIR in str(path)
        self._data = self._load()

    @property
    def is_local(self) -> bool:
        """True if using local .mad/ config."""
        return self._is_local

    @property
    def mad_dir(self) -> Path:
        """The .mad or ~/MAD directory."""
        return self._path.parent

    def _load(self) -> dict:
        with open(self._path) as f:
            return json.load(f)

    def _save(self) -> None:
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")

    @property
    def boards_dir(self) -> Path:
        if self._is_local:
            return self.mad_dir / "boards"
        # For global config, use configured boards_dir or default
        return Path(self._data.get("boards_dir", str(Path("~/MAD/boards")))).expanduser()

    @property
    def boards(self) -> List[str]:
        return self._data.get("boards", ["default"])

    @property
    def agents(self) -> Dict[str, AgentConfig]:
        result = {}
        for name, cfg in self._data.get("agents", {}).items():
            result[name] = AgentConfig(
                name=name,
                command=cfg["command"],
                headless_flag=cfg["headless_flag"],
                headless_extra_flags=cfg.get("headless_extra_flags", []),
            )
        return result

    @property
    def current_agent(self) -> AgentConfig:
        name = self._data.get("current_agent", "claude")
        agents = self.agents
        if name not in agents:
            # Fallback to first available
            name = list(agents.keys())[0] if agents else "claude"
        return agents.get(name, AgentConfig(
            name="claude",
            command="claude",
            headless_flag="-p",
            headless_extra_flags=["--dangerously-skip-permissions"]
        ))

    @property
    def current_agent_name(self) -> str:
        return self._data.get("current_agent", "claude")

    @property
    def server(self) -> Optional["ServerConfig"]:
        """Get server configuration if present."""
        data = self._data.get("server", {})
        if not data.get("enabled", False):
            return None
        url = data.get("url", "")
        api_key = data.get("api_key", "")
        client_id = data.get("client_id", "")
        if not url or not api_key:
            import logging
            logging.warning("server.url or server.api_key not configured, skipping server connection")
            return None
        if not client_id:
            import logging
            logging.warning("server.client_id not configured, skipping server connection")
            return None
        return ServerConfig(
            enabled=True,
            url=url,
            api_key=api_key,
            client_id=client_id,
            push_interval_seconds=data.get("push_interval_seconds", 10.0),
        )

    @property
    def agent_for_phase(self) -> Dict[str, str]:
        """Get agent name for a given phase."""
        return self._data.get("agent_for_phase", {})

    def get_agent_for_phase(self, phase: str) -> AgentConfig:
        """Get the agent config for a specific phase."""
        agent_name = self.agent_for_phase.get(phase, self.current_agent_name)
        # Handle "default" option - use the current default agent
        if agent_name == "default":
            agent_name = self.current_agent_name
        agents = self.agents
        if agent_name in agents:
            return agents[agent_name]
        # Fallback to default
        return self.current_agent

    def set_agent_for_phase(self, phase: str, agent_name: str) -> None:
        """Set which agent to use for a specific phase."""
        if "agent_for_phase" not in self._data:
            self._data["agent_for_phase"] = {}
        # Allow "default" as a value (means use the default agent)
        if agent_name != "default" and agent_name not in self._data["agents"]:
            raise ValueError(f"Unknown agent: {agent_name}")
        self._data["agent_for_phase"][phase] = agent_name
        self._save()

    def set_agent_for_phases(self, phase_mapping: dict[str, str]) -> None:
        """Set which agent to use for multiple phases at once."""
        if "agent_for_phase" not in self._data:
            self._data["agent_for_phase"] = {}
        for phase, agent_name in phase_mapping.items():
            # Allow "default" as a value (means use the default agent)
            if agent_name != "default" and agent_name not in self._data["agents"]:
                raise ValueError(f"Unknown agent: {agent_name}")
            self._data["agent_for_phase"][phase] = agent_name
        self._save()

    def set_current_agent(self, name: str) -> None:
        if "agents" not in self._data:
            self._data["agents"] = {}
        if name not in self._data["agents"]:
            raise ValueError(f"Unknown agent: {name}. Available: {list(self._data['agents'].keys())}")
        self._data["current_agent"] = name
        self._save()

    def setup_boards(self) -> None:
        """Create all board/stage directories if they don't exist."""
        stages = [
            "ideas", "plan-inbox", "reviewing-plan", "requested-input", "approved", "spec-writing",
            "implementing", "testing", "review", "final-human-approval", "done", "rejected",
        ]
        for board in self.boards:
            for stage in stages:
                (self.boards_dir / board / stage).mkdir(parents=True, exist_ok=True)

    def add_board(self, name: str) -> None:
        """Add a new board to config and create its stage directories."""
        name = name.strip().lower()
        boards = self._data.get("boards", [])
        if name in boards:
            raise ValueError(f"Board '{name}' already exists")
        boards.append(name)
        self._data["boards"] = boards
        self._save()
        for stage in ["ideas", "plan-inbox", "reviewing-plan", "requested-input", "approved", "spec-writing",
                      "implementing", "testing", "review", "final-human-approval", "done", "rejected"]:
            (self.boards_dir / name / stage).mkdir(parents=True, exist_ok=True)
