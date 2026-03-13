#!/usr/bin/env python3
"""Tests for the move-agent-definitions-to-mad-built-in feature."""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Generator
import pytest

# Add pipeline to path
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    BUILTIN_AGENTS,
    AgentConfig,
    PhaseConfig,
    Config,
)


class TestBuiltinAgents:
    """Test BUILTIN_AGENTS constant."""

    def test_builtin_agents_has_two_entries(self):
        assert len(BUILTIN_AGENTS) == 2
        assert "claude" in BUILTIN_AGENTS
        assert "opencode" in BUILTIN_AGENTS

    def test_claude_entry(self):
        claude = BUILTIN_AGENTS["claude"]
        assert claude["command"] == "claude"
        assert claude["headless_flag"] == "-p"
        assert claude["headless_extra_flags"] == ["--dangerously-skip-permissions"]
        assert claude["model_flag"] == "--model"

    def test_opencode_entry(self):
        opencode = BUILTIN_AGENTS["opencode"]
        assert opencode["command"] == "opencode"
        assert opencode["headless_flag"] == ""
        assert opencode["headless_extra_flags"] == ["run"]
        assert opencode["model_flag"] == "-m"


class TestAgentConfig:
    """Test AgentConfig dataclass."""

    def test_agent_config_with_defaults(self):
        config = AgentConfig(
            name="claude",
            command="claude",
            headless_flag="-p",
            headless_extra_flags=["--dangerously-skip-permissions"],
        )
        assert config.model_flag == "--model"
        assert config.model is None

    def test_agent_config_with_model(self):
        config = AgentConfig(
            name="claude",
            command="claude",
            headless_flag="-p",
            headless_extra_flags=[],
            model_flag="--model",
            model="claude-sonnet-4-20250514",
        )
        assert config.model == "claude-sonnet-4-20250514"


class TestPhaseConfig:
    """Test PhaseConfig dataclass."""

    def test_phase_config_agent_only(self):
        cfg = PhaseConfig(agent="claude")
        assert cfg.agent == "claude"
        assert cfg.model is None

    def test_phase_config_with_model(self):
        cfg = PhaseConfig(agent="claude", model="sonnet")
        assert cfg.agent == "claude"
        assert cfg.model == "sonnet"


class TestConfig:
    """Test Config class with temp files."""

    @pytest.fixture
    def temp_config(self) -> Generator[Path, None, None]:
        """Create a temporary config file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "default_agent": "claude",
                    "boards": ["default"],
                    "agent_for_phase": {
                        "planning": "claude",
                        "implementing": "opencode",
                    },
                },
                f,
            )
            temp_path = Path(f.name)
        yield temp_path
        os.unlink(temp_path)

    @pytest.fixture
    def old_config(self) -> Generator[Path, None, None]:
        """Create a temporary config with old format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "current_agent": "opencode",
                    "agents": {
                        "claude": {
                            "command": "claude",
                            "headless_flag": "-p",
                            "headless_extra_flags": ["--dangerously-skip-permissions"],
                        }
                    },
                    "boards": ["default"],
                    "agent_for_phase": {
                        "planning": "claude",
                    },
                },
                f,
            )
            temp_path = Path(f.name)
        yield temp_path
        os.unlink(temp_path)

    def test_agents_from_builtin(self, temp_config: Path):
        """Config.agents returns agents from BUILTIN_AGENTS, not from JSON."""
        config = Config(path=temp_config)
        agents = config.agents
        assert "claude" in agents
        assert "opencode" in agents
        assert agents["claude"].model_flag == "--model"
        assert agents["opencode"].model_flag == "-m"

    def test_current_agent_name_reads_default_agent(self, temp_config: Path):
        """current_agent_name reads default_agent from config."""
        config = Config(path=temp_config)
        assert config.current_agent_name == "claude"

    def test_current_agent_name_fallback_to_current_agent(self, old_config: Path):
        """current_agent_name falls back to current_agent for backward compat."""
        config = Config(path=old_config)
        assert config.current_agent_name == "opencode"

    def test_set_current_agent_writes_default_agent(self, temp_config: Path):
        """set_current_agent writes default_agent key."""
        config = Config(path=temp_config)
        config.set_current_agent("opencode")
        with open(temp_config) as f:
            data = json.load(f)
        assert "default_agent" in data
        assert data["default_agent"] == "opencode"
        assert "current_agent" not in data

    def test_set_current_agent_validates_against_builtin(self, temp_config: Path):
        """set_current_agent raises ValueError for unknown agents."""
        config = Config(path=temp_config)
        with pytest.raises(ValueError) as exc:
            config.set_current_agent("unknown_agent")
        assert "Unknown agent" in str(exc.value)

    def test_agent_for_phase_string_format(self, temp_config: Path):
        """agent_for_phase handles string format."""
        config = Config(path=temp_config)
        planning = config.agent_for_phase["planning"]
        assert isinstance(planning, PhaseConfig)
        assert planning.agent == "claude"
        assert planning.model is None

    def test_agent_for_phase_dict_format(self, temp_config: Path):
        """agent_for_phase handles dict format with model."""
        # Manually add dict format to config
        with open(temp_config, "w") as f:
            json.dump(
                {
                    "default_agent": "claude",
                    "boards": ["default"],
                    "agent_for_phase": {
                        "planning": {"agent": "claude", "model": "sonnet"},
                    },
                },
                f,
            )
        config = Config(path=temp_config)
        planning = config.agent_for_phase["planning"]
        assert planning.agent == "claude"
        assert planning.model == "sonnet"

    def test_agent_for_phase_empty_string_model(self, temp_config: Path):
        """agent_for_phase treats empty string model as None."""
        with open(temp_config, "w") as f:
            json.dump(
                {
                    "default_agent": "claude",
                    "boards": ["default"],
                    "agent_for_phase": {
                        "planning": {"agent": "claude", "model": ""},
                    },
                },
                f,
            )
        config = Config(path=temp_config)
        planning = config.agent_for_phase["planning"]
        assert planning.model is None

    def test_get_agent_for_phase_with_model(self, temp_config: Path):
        """get_agent_for_phase returns AgentConfig with model set."""
        with open(temp_config, "w") as f:
            json.dump(
                {
                    "default_agent": "claude",
                    "boards": ["default"],
                    "agent_for_phase": {
                        "planning": {"agent": "claude", "model": "sonnet"},
                    },
                },
                f,
            )
        config = Config(path=temp_config)
        agent_cfg = config.get_agent_for_phase("planning")
        assert agent_cfg.name == "claude"
        assert agent_cfg.model == "sonnet"

    def test_get_agent_for_phase_unknown_agent_fallback(self, temp_config: Path):
        """get_agent_for_phase falls back to current_agent for unknown agents."""
        with open(temp_config, "w") as f:
            json.dump(
                {
                    "default_agent": "claude",
                    "boards": ["default"],
                    "agent_for_phase": {
                        "planning": "unknown_agent",
                    },
                },
                f,
            )
        config = Config(path=temp_config)
        agent_cfg = config.get_agent_for_phase("planning")
        assert agent_cfg.name == "claude"  # falls back to current

    def test_set_agent_for_phase_with_model(self, temp_config: Path):
        """set_agent_for_phase writes dict format when model provided."""
        config = Config(path=temp_config)
        config.set_agent_for_phase("planning", "claude", model="sonnet")
        with open(temp_config) as f:
            data = json.load(f)
        assert data["agent_for_phase"]["planning"] == {"agent": "claude", "model": "sonnet"}

    def test_set_agent_for_phase_without_model(self, temp_config: Path):
        """set_agent_for_phase writes string format when no model."""
        config = Config(path=temp_config)
        config.set_agent_for_phase("planning", "claude")
        with open(temp_config) as f:
            data = json.load(f)
        assert data["agent_for_phase"]["planning"] == "claude"

    def test_set_agent_for_phase_allows_default(self, temp_config: Path):
        """set_agent_for_phase allows 'default' as agent_name."""
        config = Config(path=temp_config)
        config.set_agent_for_phase("planning", "default")
        with open(temp_config) as f:
            data = json.load(f)
        assert data["agent_for_phase"]["planning"] == "default"

    def test_migration_removes_agents_block(self, old_config: Path):
        """Migration removes agents key from config."""
        config = Config(path=old_config)
        with open(old_config) as f:
            data = json.load(f)
        assert "agents" not in data

    def test_migration_renames_current_agent(self, old_config: Path):
        """Migration renames current_agent to default_agent."""
        config = Config(path=old_config)
        with open(old_config) as f:
            data = json.load(f)
        assert "default_agent" in data
        assert data["default_agent"] == "opencode"
        assert "current_agent" not in data

    def test_migration_handles_unknown_agent(self, temp_config: Path):
        """Migration resets unknown agent to default."""
        with open(temp_config, "w") as f:
            json.dump(
                {
                    "current_agent": "unknown_agent",
                    "agent_for_phase": {"planning": "unknown_agent"},
                    "boards": ["default"],
                },
                f,
            )
        config = Config(path=temp_config)
        with open(temp_config) as f:
            data = json.load(f)
        assert data["agent_for_phase"]["planning"] == "default"

    def test_migration_preserves_model(self, temp_config: Path):
        """Migration preserves model when resetting unknown agent."""
        with open(temp_config, "w") as f:
            json.dump(
                {
                    "current_agent": "unknown",
                    "agent_for_phase": {
                        "planning": {"agent": "unknown", "model": "test-model"}
                    },
                    "boards": ["default"],
                },
                f,
            )
        config = Config(path=temp_config)
        with open(temp_config) as f:
            data = json.load(f)
        assert data["agent_for_phase"]["planning"] == {
            "agent": "default",
            "model": "test-model",
        }


class TestRunner:
    """Test AgentRunner model flag injection."""

    def test_model_flag_injected_at_end(self):
        """Model flag is added at end of command."""
        from runner import AgentRunner
        from config import Config, AgentConfig

        config = Config()
        runner = AgentRunner(config)

        # Create agent config with model
        agent_cfg = AgentConfig(
            name="claude",
            command="claude",
            headless_flag="-p",
            headless_extra_flags=["--dangerously-skip-permissions"],
            model_flag="--model",
            model="claude-sonnet-4-20250514",
        )
        runner._agent_config_override = agent_cfg

        # Build command (simplified)
        cmd = [runner.agent.command]
        if runner.agent.headless_flag:
            cmd.append(runner.agent.headless_flag)
        cmd.append("Read instructions")
        cmd.extend(runner.agent.headless_extra_flags)
        if runner.agent.model:
            cmd.extend([runner.agent.model_flag, runner.agent.model])

        assert cmd == [
            "claude",
            "-p",
            "Read instructions",
            "--dangerously-skip-permissions",
            "--model",
            "claude-sonnet-4-20250514",
        ]

    def test_for_phase_uses_agent_config_override(self):
        """for_phase returns runner with agent_config_override set."""
        from runner import AgentRunner
        from config import Config

        # Create config with model for planning
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "default_agent": "claude",
                    "boards": ["default"],
                    "agent_for_phase": {
                        "planning": {"agent": "claude", "model": "sonnet"},
                    },
                },
                f,
            )
            temp_path = Path(f.name)

        try:
            config = Config(path=temp_path)
            runner = AgentRunner(config)
            phase_runner = runner.for_phase("planning")

            assert phase_runner.agent.name == "claude"
            assert phase_runner.agent.model == "sonnet"
        finally:
            os.unlink(temp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
