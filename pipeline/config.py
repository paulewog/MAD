"""Configuration loader for MAD pipeline."""

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


logger = logging.getLogger("pipeline")


DEFAULT_GLOBAL_CONFIG = Path("~/MAD/config.json").expanduser()
LOCAL_DIR = ".mad"
LOCAL_CONFIG = "config.json"
PROJECT_CONFIG = ".mad.config.json"
CONTEXT_FILENAME = "CONTEXT.md"
MAX_CONTEXT_SIZE = 100 * 1024  # 100KB
BOARD_CONTEXT_FILENAME = "BOARD_CONTEXT.md"
MAX_BOARD_CONTEXT_SIZE = 30 * 1024  # 30KB

BUILTIN_AGENTS: Dict[str, dict] = {
    "claude": {
        "command": "claude",
        "headless_flag": "-p",
        "headless_extra_flags": ["--dangerously-skip-permissions"],
        "model_flag": "--model",
    },
    "opencode": {
        "command": "opencode",
        "headless_flag": "",
        "headless_extra_flags": ["run"],
        "model_flag": "-m",
    },
}

PIPELINE_PHASES = [
    ("planning", "Planning"),
    ("reviewing_plan", "Plan Review"),
    ("spec_writing", "Spec Writing"),
    ("implementing", "Implementing"),
    ("fix_feedback", "Fix Feedback"),
    ("testing", "Testing"),
    ("review", "Review"),
]

VALID_TERMINAL_STAGES = {"plan", "spec"}


def find_config() -> Optional[Path]:
    """Find config file - prefers local .mad/config.json, falls back to ~/MAD/config.json.
    
    Searches:
    1. .mad/config.json in current directory
    2. Walk up from current dir to find .mad/config.json
    3. Create local config if .mad/ dir exists (without config.json)
    4. ~/MAD/config.json as global fallback (only if no local .mad in cwd tree)
    """
    cwd = Path.cwd()
    found_local_dir = None
    
    # Check current directory and parents for local config
    for dir_path in [cwd] + list(cwd.parents):
        local_path = dir_path / LOCAL_DIR / LOCAL_CONFIG
        if local_path.exists():
            return local_path
        # Track the first (closest to cwd) .mad dir found
        if (dir_path / LOCAL_DIR).exists() and found_local_dir is None:
            found_local_dir = dir_path
        # Stop if we hit home or root
        if dir_path == Path.home() or dir_path == Path("/"):
            break
    
    # If no .mad/ anywhere in tree, signal to create local in cwd
    if found_local_dir is None and not (cwd / LOCAL_DIR).exists():
        return None  # Signal to create local in cwd
    
    # .mad/ dir exists but has no config.json - create one there
    if found_local_dir is not None:
        return ensure_local_config(found_local_dir)
    
    # Fall back to global config only when no .mad/ exists anywhere
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


def socket_path_for_project() -> str:
    """Derive a unique Unix socket path for the current project.

    Uses the .mad directory's absolute path to generate a short hash,
    so each project gets its own socket and multiple instances don't collide.
    """
    import hashlib
    mad_dir = get_mad_dir()
    abs_path = str(mad_dir.resolve())
    hash_value = hashlib.sha256(abs_path.encode()).hexdigest()[:12]
    return f"/tmp/mad-{hash_value}.sock"


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
        "default_agent": "claude",
        "boards": ["default"],
    }
    with open(config_path, "w") as f:
        json.dump(default_config, f, indent=2)
        f.write("\n")
    
    # Create board structure
    stages = [
        "ideas", "plan-inbox", "reviewing-plan", "requested-input", "awaiting-human-approval", "approved", "spec-writing",
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
    model_flag: str = "--model"
    model: Optional[str] = None


@dataclass
class PhaseConfig:
    """Configuration for a single phase's agent assignment."""

    agent: str
    model: Optional[str] = None


@dataclass
class IdeatingRound:
    """Configuration for a single round of ideation debate."""

    cli: str
    model: Optional[str] = None


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
        self._migrate_config()

    @property
    def is_local(self) -> bool:
        """True if using local .mad/ config."""
        return self._is_local

    @property
    def mad_dir(self) -> Path:
        """The .mad or ~/MAD directory."""
        return self._path.parent

    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid config at {self._path}: {e}")
        except FileNotFoundError:
            raise ValueError(f"Config file not found: {self._path}")

    def _migrate_config(self):
        """Migrate old config format to new format."""
        try:
            changed = False

            if "agents" in self._data:
                del self._data["agents"]
                changed = True

            if "current_agent" in self._data:
                if "default_agent" not in self._data:
                    self._data["default_agent"] = self._data["current_agent"]
                del self._data["current_agent"]
                changed = True

            afp = self._data.get("agent_for_phase", {})
            for phase, value in list(afp.items()):
                if isinstance(value, str):
                    agent_name = value
                elif isinstance(value, dict):
                    agent_name = value.get("agent", "")
                else:
                    agent_name = ""

                if agent_name and agent_name != "default" and agent_name not in BUILTIN_AGENTS:
                    if isinstance(value, dict) and value.get("model"):
                        afp[phase] = {"agent": "default", "model": value["model"]}
                    else:
                        afp[phase] = "default"
                    changed = True

            if changed:
                self._save()
        except Exception as e:
            logger.error(f"Config migration failed: {e}")

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
        agent_defaults = self._data.get("agent_defaults", {})
        result = {}
        for name, cfg in BUILTIN_AGENTS.items():
            defaults = agent_defaults.get(name, {})
            command = defaults.get("command")
            if not command:
                command = cfg["command"]
            if command and not command.startswith("/"):
                command = os.path.expanduser(command)
            result[name] = AgentConfig(
                name=name,
                command=command,
                headless_flag=cfg["headless_flag"],
                headless_extra_flags=cfg.get("headless_extra_flags", []),
                model_flag=cfg.get("model_flag", "--model"),
                model=defaults.get("model"),
            )
        return result

    @property
    def models(self) -> Dict[str, List[str]]:
        """Get models configuration from config.json."""
        return self._data.get("models", {})

    def get_models_for_agent(self, agent_name: str) -> List[str]:
        """Get available models for a specific agent."""
        if agent_name == "default":
            return []
        return self.models.get(agent_name, [])

    @property
    def ideating_rounds(self) -> List[IdeatingRound]:
        """Get ideating rounds configuration from config.json."""
        raw = self._data.get("ideating", {})
        rounds = raw.get("rounds", [])
        result = []
        for r in rounds:
            if isinstance(r, dict):
                result.append(IdeatingRound(
                    cli=r.get("cli", "opencode"),
                    model=r.get("model"),
                ))
            else:
                result.append(IdeatingRound(cli=r))
        return result

    @property
    def ideating_max_rounds(self) -> int:
        """Get max total ideation rounds across all cycles. Default 12."""
        raw = self._data.get("ideating", {})
        return raw.get("max_rounds", 12)

    @property
    def current_agent(self) -> AgentConfig:
        name = self.current_agent_name
        agents = self.agents
        if name not in agents:
            name = list(agents.keys())[0] if agents else "claude"
        return agents.get(name, AgentConfig(
            name="claude",
            command="claude",
            headless_flag="-p",
            headless_extra_flags=["--dangerously-skip-permissions"]
        ))

    @property
    def current_agent_name(self) -> str:
        return self._data.get("default_agent", self._data.get("current_agent", "claude"))

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

    def _get_project_root(self) -> Path:
        """Get the project root directory (where .mad.config.json lives)."""
        return self.mad_dir.parent

    def _get_project_config_path(self) -> Path:
        """Get path to the project config file."""
        return self._get_project_root() / PROJECT_CONFIG

    def _get_per_board_config(self, board_name: str) -> Optional[dict]:
        """Load per-board config if it exists."""
        board_config_path = self.boards_dir / board_name / LOCAL_CONFIG
        if board_config_path.exists():
            try:
                with open(board_config_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def get_terminal_stage(self, board_name: str) -> Optional[str]:
        """Get the terminal_stage for a board, or None for full pipeline."""
        board_config = self._get_per_board_config(board_name)
        if not board_config:
            return None
        value = board_config.get("terminal_stage")
        if not value or not isinstance(value, str):
            return None
        value = value.strip().lower()
        if not value:
            return None
        if value not in VALID_TERMINAL_STAGES:
            logger.warning(
                f"Board '{board_name}' has invalid terminal_stage '{value}'. "
                f"Valid values: {VALID_TERMINAL_STAGES}. Running full pipeline."
            )
            return None
        return value

    @property
    def code_path(self) -> Optional[Path]:
        """Get code_path from per-board config, project config, or derive from board location."""
        project_config_path = self._get_project_config_path()
        code_path = None

        if self.boards:
            for board in self.boards:
                board_config = self._get_per_board_config(board)
                if board_config and "code_path" in board_config:
                    code_path = board_config["code_path"]
                    break

        if not code_path and project_config_path.exists():
            try:
                with open(project_config_path) as f:
                    config_data = json.load(f)
                    code_path = config_data.get("code_path")
            except (json.JSONDecodeError, OSError):
                pass

        if code_path:
            resolved = os.path.realpath(code_path)
            return Path(resolved)

        derived = self._derive_code_path()
        if derived:
            return derived
        return None

    def _derive_code_path(self) -> Optional[Path]:
        """Derive code_path from board location.
        
        Board is at: <project_root>/.mad/boards/<boardname>
        So we need to go up 3 levels to get to project root.
        """
        if self.boards:
            board_name = self.boards[0]
            board_path = self.boards_dir / board_name
            if board_path.exists():
                derived = board_path.parent.parent.parent
                resolved = os.path.realpath(derived)
                return Path(resolved)
        return None

    @property
    def context_file(self) -> Path:
        """Get path to CONTEXT.md file."""
        project_root = self._get_project_root()
        return project_root / LOCAL_DIR / CONTEXT_FILENAME

    def set_code_path(self, path: str) -> None:
        """Set code_path in project config."""
        if not path or not path.strip():
            raise ValueError("code_path cannot be empty")
        
        project_config_path = self._get_project_config_path()
        project_config_path.parent.mkdir(parents=True, exist_ok=True)

        config_data = {}
        if project_config_path.exists():
            try:
                with open(project_config_path) as f:
                    config_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        config_data["code_path"] = path

        try:
            with open(project_config_path, "w") as f:
                json.dump(config_data, f, indent=2)
                f.write("\n")
        except OSError as e:
            raise RuntimeError(f"Failed to write config: {e}")

    def get_code_path_value(self) -> Optional[str]:
        """Get raw code_path value without resolving symlinks."""
        project_config_path = self._get_project_config_path()

        if project_config_path.exists():
            try:
                with open(project_config_path) as f:
                    config_data = json.load(f)
                    return config_data.get("code_path")
            except (json.JSONDecodeError, OSError):
                pass

        for board in self.boards:
            board_config = self._get_per_board_config(board)
            if board_config and "code_path" in board_config:
                return board_config["code_path"]

        return None

    @property
    def agent_for_phase(self) -> Dict[str, PhaseConfig]:
        """Get agent and optional model for each phase."""
        raw = self._data.get("agent_for_phase", {})
        result = {}
        for phase, value in raw.items():
            if isinstance(value, str):
                result[phase] = PhaseConfig(agent=value, model=None)
            elif isinstance(value, dict):
                result[phase] = PhaseConfig(
                    agent=value.get("agent", self.current_agent_name),
                    model=value.get("model") or None,  # empty string -> None
                )
            else:
                result[phase] = PhaseConfig(agent=self.current_agent_name, model=None)
        return result

    def get_agent_for_phase(self, phase: str) -> AgentConfig:
        """Get the agent config for a specific phase."""
        phase_cfg = self.agent_for_phase.get(phase)
        if phase_cfg is None:
            return self.current_agent
        agent_name = phase_cfg.agent
        if agent_name == "default":
            agent_name = self.current_agent_name
        agents = self.agents
        if agent_name not in agents:
            return self.current_agent
        agent_config = agents[agent_name]
        if phase_cfg.model:
            agent_config = AgentConfig(
                name=agent_config.name,
                command=agent_config.command,
                headless_flag=agent_config.headless_flag,
                headless_extra_flags=agent_config.headless_extra_flags,
                model_flag=agent_config.model_flag,
                model=phase_cfg.model,
            )
        return agent_config

    def set_agent_for_phase(self, phase: str, agent_name: str, model: Optional[str] = None) -> None:
        """Set which agent to use for a specific phase."""
        if "agent_for_phase" not in self._data:
            self._data["agent_for_phase"] = {}
        if agent_name != "default" and agent_name not in BUILTIN_AGENTS:
            raise ValueError(f"Unknown agent: {agent_name}. Available: {list(BUILTIN_AGENTS.keys())}")
        if model:
            self._data["agent_for_phase"][phase] = {"agent": agent_name, "model": model}
        else:
            self._data["agent_for_phase"][phase] = agent_name
        self._save()

    def set_agent_for_phases(self, phase_mapping: dict) -> None:
        """Set agent (and optional model) for multiple phases.
        
        Values can be strings (agent name only) or dicts with 'agent' and optional 'model' keys.
        """
        if "agent_for_phase" not in self._data:
            self._data["agent_for_phase"] = {}
        for phase, value in phase_mapping.items():
            if isinstance(value, str):
                agent_name = value
                model = None
            else:
                agent_name = value["agent"]
                model = value.get("model") or None
            if agent_name != "default" and agent_name not in BUILTIN_AGENTS:
                raise ValueError(f"Unknown agent: {agent_name}. Available: {list(BUILTIN_AGENTS.keys())}")
            if model:
                self._data["agent_for_phase"][phase] = {"agent": agent_name, "model": model}
            else:
                self._data["agent_for_phase"][phase] = agent_name
        self._save()

    def set_current_agent(self, name: str) -> None:
        if name not in BUILTIN_AGENTS:
            raise ValueError(f"Unknown agent: {name}. Available: {list(BUILTIN_AGENTS.keys())}")
        self._data["default_agent"] = name
        self._data.pop("current_agent", None)
        self._save()

    def setup_boards(self) -> None:
        """Create all board/stage directories if they don't exist."""
        stages = [
            "ideas", "plan-inbox", "reviewing-plan", "requested-input", "awaiting-human-approval", "approved", "spec-writing",
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
        for stage in ["ideas", "plan-inbox", "reviewing-plan", "requested-input", "awaiting-human-approval", "approved", "spec-writing",
                      "implementing", "testing", "review", "final-human-approval", "done", "rejected"]:
            (self.boards_dir / name / stage).mkdir(parents=True, exist_ok=True)


def read_context_file(config: Config) -> Optional[str]:
    """Read CONTEXT.md file with size limit and error handling.
    
    Returns None if file doesn't exist or cannot be read.
    Truncates at 100KB byte boundary if file is too large.
    """
    context_path = config.context_file
    if not context_path.exists():
        return None
    
    try:
        with open(context_path, "rb") as f:
            content_bytes = f.read()
        
        if len(content_bytes) > MAX_CONTEXT_SIZE:
            truncated_bytes = content_bytes[:MAX_CONTEXT_SIZE]
            while truncated_bytes:
                try:
                    return truncated_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    truncated_bytes = truncated_bytes[:-1]
            return ""
        
        return content_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def read_board_context_file(config: Config, board: str) -> Optional[str]:
    """Read BOARD_CONTEXT.md for a specific board with 30KB size limit.
    
    Returns None if file doesn't exist or cannot be read.
    Truncates at 30KB byte boundary if file is too large.
    """
    context_path = config.boards_dir / board / BOARD_CONTEXT_FILENAME
    if not context_path.exists():
        return None
    
    try:
        with open(context_path, "rb") as f:
            content_bytes = f.read()
        
        if len(content_bytes) > MAX_BOARD_CONTEXT_SIZE:
            logger.warning(
                f"Board context file for '{board}' exceeds 30KB "
                f"({len(content_bytes)} bytes) — consider curating "
                f".mad/boards/{board}/{BOARD_CONTEXT_FILENAME}"
            )
            truncated_bytes = content_bytes[:MAX_BOARD_CONTEXT_SIZE]
            while truncated_bytes:
                try:
                    return truncated_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    truncated_bytes = truncated_bytes[:-1]
            return ""
        
        return content_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def write_context_file(config: Config, content: str) -> None:
    """Write content to CONTEXT.md file.
    
    Truncates content to 100KB at byte boundary if necessary.
    """
    context_path = config.context_file
    context_path.parent.mkdir(parents=True, exist_ok=True)
    
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_CONTEXT_SIZE:
        content_bytes = content_bytes[:MAX_CONTEXT_SIZE]
        while content_bytes:
            try:
                content_bytes.decode("utf-8")
                break
            except UnicodeDecodeError:
                content_bytes = content_bytes[:-1]
    
    with open(context_path, "wb") as f:
        f.write(content_bytes)


def view_context_file(config: Config) -> None:
    """Display CONTEXT.md content to stdout."""
    content = read_context_file(config)
    if content is None:
        print("No CONTEXT.md file found.")
        return
    print(content)


def edit_context_file(config: Config) -> None:
    """Edit CONTEXT.md using $EDITOR or fallback editors."""
    context_path = config.context_file
    context_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not context_path.exists():
        context_path.write_text("")
    
    editor = os.environ.get("EDITOR")
    if not editor:
        for fallback in ["vim", "nano", "vi"]:
            result = subprocess.run(["which", fallback], capture_output=True)
            if result.returncode == 0:
                editor = fallback
                break
    
    if not editor:
        raise RuntimeError("No editor found. Set $EDITOR or install vim/nano.")
    
    result = subprocess.run([editor, str(context_path)])
    if result.returncode != 0:
        raise RuntimeError(f"Editor exited with code {result.returncode}")
