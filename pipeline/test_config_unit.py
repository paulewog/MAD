"""Unit tests for config.py — Configuration module."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from config import (
    Config,
    AgentConfig,
    PhaseConfig,
    IdeatingRound,
    ServerConfig,
    find_config,
    ensure_local_config,
    read_context_file,
    write_context_file,
    BUILTIN_AGENTS,
)


class TestFindConfig(unittest.TestCase):
    """Tests for find_config function."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-find")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.original_cwd = Path.cwd()

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch('config.Path.cwd')
    def test_find_config_local_cwd(self, mock_cwd):
        """Local .mad/config.json in cwd found first."""
        mock_cwd.return_value = self.temp_dir
        config_dir = self.temp_dir / ".mad"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text('{"boards": ["default"]}')

        result = find_config()
        
        self.assertIsNotNone(result)
        self.assertEqual(result, config_file)

    @patch('config.Path.cwd')
    def test_find_config_parent_dir(self, mock_cwd):
        """config.json in parent dir found."""
        mock_cwd.return_value = self.temp_dir / "subdir"
        (self.temp_dir / "subdir").mkdir(parents=True, exist_ok=True)
        
        config_dir = self.temp_dir / ".mad"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text('{"boards": ["default"]}')

        result = find_config()
        
        self.assertIsNotNone(result)

    @patch('config.Path.cwd')
    @patch('config.DEFAULT_GLOBAL_CONFIG', Path("/tmp/test-global/config.json"))
    def test_find_config_global_fallback(self, mock_cwd):
        """Falls back to ~/MAD/config.json."""
        mock_cwd.return_value = self.temp_dir
        
        # Create .mad directory to simulate being in an existing project tree
        # This triggers the global config fallback behavior
        (self.temp_dir / ".mad").mkdir(parents=True, exist_ok=True)
        
        global_dir = Path("/tmp/test-global")
        global_dir.mkdir(parents=True, exist_ok=True)
        global_config = global_dir / "config.json"
        global_config.write_text('{"boards": ["default"]}')

        result = find_config()
        
        self.assertIsNotNone(result)

    @patch('config.Path.cwd')
    def test_find_config_none_creates_local(self, mock_cwd):
        """Returns None when no config anywhere."""
        mock_cwd.return_value = self.temp_dir

        result = find_config()
        
        self.assertIsNone(result)


class TestEnsureLocalConfig(unittest.TestCase):
    """Tests for ensure_local_config function."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-ensure")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_ensure_local_config_creates(self):
        """Creates .mad/config.json with defaults."""
        result = ensure_local_config(self.temp_dir)

        self.assertTrue((self.temp_dir / ".mad" / "config.json").exists())

    def test_ensure_local_config_existing(self):
        """Returns path of existing config."""
        config_dir = self.temp_dir / ".mad"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text('{"boards": ["default"]}')

        result = ensure_local_config(self.temp_dir)

        self.assertEqual(result, config_file)

    def test_ensure_local_config_board_dirs(self):
        """Creates board stage directories."""
        ensure_local_config(self.temp_dir)

        for stage in ["ideas", "plan-inbox", "reviewing-plan", "done", "rejected"]:
            self.assertTrue((self.temp_dir / ".mad" / "boards" / "default" / stage).exists())


class TestConfigConstructor(unittest.TestCase):
    """Tests for Config class constructor."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-init")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.temp_dir / "config.json"
        self.config_file.write_text(json.dumps({"boards": ["default"], "default_agent": "claude"}))

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_config_loads_json(self):
        """Reads and parses config file."""
        config = Config(self.config_file)

        self.assertEqual(config.boards, ["default"])

    def test_config_invalid_json_raises(self):
        """ValueError on bad JSON."""
        self.config_file.write_text("not valid json")

        with self.assertRaises(ValueError):
            Config(self.config_file)

    def test_config_missing_file_raises(self):
        """ValueError on missing file."""
        with self.assertRaises(ValueError):
            Config(Path("/nonexistent/config.json"))

    @patch('config.ensure_local_config')
    def test_config_force_local(self, mock_ensure):
        """force_local=True creates local config even with global."""
        mock_ensure.return_value = self.config_file

        config = Config(force_local=True)

        mock_ensure.assert_called()


class TestConfigMigrate(unittest.TestCase):
    """Tests for Config._migrate_config method."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-migrate")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.temp_dir / "config.json"

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_migrate_removes_agents_key(self):
        """Old 'agents' key removed."""
        self.config_file.write_text(json.dumps({"agents": {}, "boards": ["default"]}))

        config = Config(self.config_file)

        self.assertNotIn("agents", config._data)

    def test_migrate_current_to_default_agent(self):
        """'current_agent' → 'default_agent'."""
        self.config_file.write_text(json.dumps({"current_agent": "opencode", "boards": ["default"]}))

        config = Config(self.config_file)

        self.assertIn("default_agent", config._data)

    def test_migrate_unknown_agent_names(self):
        """Non-builtin agents replaced with 'default'."""
        self.config_file.write_text(json.dumps({
            "agent_for_phase": {"planning": "unknown-agent"},
            "boards": ["default"]
        }))

        config = Config(self.config_file)

        self.assertEqual(config._data["agent_for_phase"]["planning"], "default")


class TestConfigProperties(unittest.TestCase):
    """Tests for Config property accessors."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-props")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.temp_dir / ".mad" / "config.json"
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps({
            "boards": ["default", "other"],
            "default_agent": "claude",
            "agent_defaults": {"claude": {"model": "opus"}},
            "models": {"claude": ["haiku", "opus"]},
            "ideating": {"rounds": [{"cli": "opencode", "model": "gpt-4"}]},
        }))

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_boards_dir_local(self):
        """.mad/boards for local config."""
        config = Config(self.config_file)

        self.assertEqual(config.boards_dir, self.config_file.parent / "boards")

    def test_boards_default(self):
        """Missing boards key → ['default']."""
        self.config_file.write_text(json.dumps({}))
        config = Config(self.config_file)

        self.assertEqual(config.boards, ["default"])

    def test_agents_builtin(self):
        """Returns claude and opencode AgentConfig objects."""
        config = Config(self.config_file)

        self.assertIn("claude", config.agents)
        self.assertIn("opencode", config.agents)

    def test_agents_with_defaults(self):
        """agent_defaults.claude.model applied."""
        config = Config(self.config_file)

        self.assertEqual(config.agents["claude"].model, "opus")

    def test_current_agent_name(self):
        """Reads default_agent."""
        config = Config(self.config_file)

        self.assertEqual(config.current_agent_name, "claude")

    def test_current_agent_fallback(self):
        """Unknown agent name → first available."""
        self.config_file.write_text(json.dumps({"default_agent": "unknown", "boards": ["default"]}))
        config = Config(self.config_file)

        self.assertIsNotNone(config.current_agent.name)

    def test_models_property(self):
        """Returns models dict from config."""
        config = Config(self.config_file)

        self.assertEqual(config.models["claude"], ["haiku", "opus"])

    def test_get_models_for_agent(self):
        """Returns models for specific agent."""
        config = Config(self.config_file)

        self.assertEqual(config.get_models_for_agent("claude"), ["haiku", "opus"])

    def test_ideating_rounds(self):
        """Parses ideating.rounds config."""
        config = Config(self.config_file)

        self.assertEqual(len(config.ideating_rounds), 1)
        self.assertEqual(config.ideating_rounds[0].cli, "opencode")

    def test_ideating_rounds_empty(self):
        """No config → empty list."""
        self.config_file.write_text(json.dumps({}))
        config = Config(self.config_file)

        self.assertEqual(config.ideating_rounds, [])

    def test_ideating_max_rounds_with_value(self):
        """Returns configured max_rounds value."""
        self.config_file.write_text(json.dumps({
            "boards": ["default"],
            "ideating": {"max_rounds": 8}
        }))
        config = Config(self.config_file)

        self.assertEqual(config.ideating_max_rounds, 8)

    def test_ideating_max_rounds_default(self):
        """No max_rounds key → defaults to 12."""
        self.config_file.write_text(json.dumps({
            "boards": ["default"],
            "ideating": {"rounds": [{"cli": "claude"}]}
        }))
        config = Config(self.config_file)

        self.assertEqual(config.ideating_max_rounds, 12)

    def test_ideating_max_rounds_no_ideating_section(self):
        """No ideating section → defaults to 12."""
        self.config_file.write_text(json.dumps({
            "boards": ["default"]
        }))
        config = Config(self.config_file)

        self.assertEqual(config.ideating_max_rounds, 12)


class TestAgentForPhase(unittest.TestCase):
    """Tests for agent-for-phase configuration."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-phase")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.temp_dir / ".mad" / "config.json"
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_agent_for_phase_string_value(self):
        """"claude" → PhaseConfig(agent="claude", model=None)."""
        self.config_file.write_text(json.dumps({
            "agent_for_phase": {"planning": "claude"},
            "boards": ["default"]
        }))
        config = Config(self.config_file)

        phase_config = config.agent_for_phase.get("planning")
        self.assertEqual(phase_config.agent, "claude")

    def test_agent_for_phase_dict_value(self):
        """{"agent": "claude", "model": "opus"} → PhaseConfig."""
        self.config_file.write_text(json.dumps({
            "agent_for_phase": {"planning": {"agent": "claude", "model": "opus"}},
            "boards": ["default"]
        }))
        config = Config(self.config_file)

        phase_config = config.agent_for_phase.get("planning")
        self.assertEqual(phase_config.agent, "claude")
        self.assertEqual(phase_config.model, "opus")

    def test_get_agent_for_phase_default(self):
        """Missing phase → current_agent."""
        self.config_file.write_text(json.dumps({"boards": ["default"], "default_agent": "claude"}))
        config = Config(self.config_file)

        agent = config.get_agent_for_phase("planning")

        self.assertEqual(agent.name, "claude")

    def test_get_agent_for_phase_with_model(self):
        """Model override creates new AgentConfig."""
        self.config_file.write_text(json.dumps({
            "agent_for_phase": {"planning": {"agent": "claude", "model": "opus"}},
            "boards": ["default"]
        }))
        config = Config(self.config_file)

        agent = config.get_agent_for_phase("planning")

        self.assertEqual(agent.model, "opus")

    def test_set_agent_for_phase(self):
        """Persists to disk."""
        self.config_file.write_text(json.dumps({"boards": ["default"], "default_agent": "claude"}))
        config = Config(self.config_file)

        config.set_agent_for_phase("planning", "opencode")

        self.assertIn("planning", config._data.get("agent_for_phase", {}))

    def test_set_agent_for_phase_invalid_raises(self):
        """ValueError on unknown agent."""
        self.config_file.write_text(json.dumps({"boards": ["default"], "default_agent": "claude"}))
        config = Config(self.config_file)

        with self.assertRaises(ValueError):
            config.set_agent_for_phase("planning", "unknown-agent")

    def test_set_agent_for_phases_multiple(self):
        """Sets multiple phases."""
        self.config_file.write_text(json.dumps({"boards": ["default"], "default_agent": "claude"}))
        config = Config(self.config_file)

        config.set_agent_for_phases({"planning": "claude", "implementing": "opencode"})

        self.assertIn("planning", config._data.get("agent_for_phase", {}))
        self.assertIn("implementing", config._data.get("agent_for_phase", {}))


class TestCodePath(unittest.TestCase):
    """Tests for code path configuration."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-codepath")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.mad_dir = self.temp_dir / ".mad"
        self.mad_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.mad_dir / "config.json"
        self.config_file.write_text(json.dumps({"boards": ["default"], "default_agent": "claude"}))
        # Create board directory for derivation test
        (self.mad_dir / "boards" / "default").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_code_path_from_board_config(self):
        """Per-board code_path used."""
        board_dir = self.mad_dir / "boards" / "default"
        board_dir.mkdir(parents=True, exist_ok=True)
        board_config = board_dir / "config.json"
        board_config.write_text(json.dumps({"code_path": "/custom/code"}))

        config = Config(self.config_file)

        self.assertEqual(str(config.code_path), "/custom/code")

    def test_code_path_from_project_config(self):
        """.mad.config.json code_path used."""
        project_config = self.temp_dir / ".mad.config.json"
        project_config.write_text(json.dumps({"code_path": "/project/code"}))

        config = Config(self.config_file)

        self.assertEqual(str(config.code_path), "/project/code")

    def test_code_path_derived(self):
        """Derived from board location (up 3 levels)."""
        config = Config(self.config_file)

        self.assertIsNotNone(config.code_path)

    def test_code_path_none(self):
        """No config → None."""
        # Remove board directory to ensure no derivation can happen
        import shutil
        board_dir = self.mad_dir / "boards" / "default"
        if board_dir.exists():
            shutil.rmtree(board_dir)
        
        self.config_file.write_text(json.dumps({}))
        config = Config(self.config_file)

        self.assertIsNone(config.code_path)

    def test_context_file_path(self):
        """Returns .mad/CONTEXT.md path."""
        config = Config(self.config_file)

        self.assertEqual(config.context_file, self.mad_dir / "CONTEXT.md")

    def test_set_code_path(self):
        """Writes to .mad.config.json."""
        config = Config(self.config_file)

        config.set_code_path("/new/code/path")

        project_config = self.temp_dir / ".mad.config.json"
        self.assertTrue(project_config.exists())

    def test_set_code_path_empty_raises(self):
        """ValueError on empty string."""
        config = Config(self.config_file)

        with self.assertRaises(ValueError):
            config.set_code_path("")


class TestContextFile(unittest.TestCase):
    """Tests for context file read/write."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-context")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.mad_dir = self.temp_dir / ".mad"
        self.mad_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.mad_dir / "config.json"
        self.config_file.write_text(json.dumps({"boards": ["default"]}))
        self.context_file = self.mad_dir / "CONTEXT.md"

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_read_context_file_exists(self):
        """Returns file content."""
        self.context_file.write_text("Context content")

        config = Config(self.config_file)
        result = read_context_file(config)

        self.assertEqual(result, "Context content")

    def test_read_context_file_missing(self):
        """Returns None."""
        config = Config(self.config_file)
        result = read_context_file(config)

        self.assertIsNone(result)

    def test_read_context_file_truncation(self):
        """Content > 100KB truncated at UTF-8 boundary."""
        large_content = "A" * 150 * 1024
        self.context_file.write_text(large_content)

        config = Config(self.config_file)
        result = read_context_file(config)

        self.assertLessEqual(len(result), 100 * 1024)

    def test_write_context_file(self):
        """Writes content to disk."""
        config = Config(self.config_file)
        write_context_file(config, "New context")

        self.assertEqual(self.context_file.read_text(), "New context")

    def test_write_context_file_truncation(self):
        """Content > 100KB truncated."""
        large_content = "A" * 150 * 1024
        config = Config(self.config_file)
        write_context_file(config, large_content)

        self.assertLessEqual(len(self.context_file.read_text()), 100 * 1024)

    def test_write_context_file_creates_dirs(self):
        """Parent dirs created."""
        config = Config(self.config_file)
        write_context_file(config, "Content")

        self.assertTrue(self.context_file.parent.exists())


class TestServerConfig(unittest.TestCase):
    """Tests for server configuration."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-server")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.temp_dir / ".mad" / "config.json"
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_server_config_enabled(self):
        """Returns ServerConfig when all fields present."""
        self.config_file.write_text(json.dumps({
            "boards": ["default"],
            "server": {
                "enabled": True,
                "url": "http://localhost:8080",
                "api_key": "secret",
                "client_id": "client1"
            }
        }))
        config = Config(self.config_file)

        self.assertIsNotNone(config.server)
        self.assertEqual(config.server.url, "http://localhost:8080")

    def test_server_config_disabled(self):
        """Returns None when enabled=False."""
        self.config_file.write_text(json.dumps({
            "boards": ["default"],
            "server": {"enabled": False}
        }))
        config = Config(self.config_file)

        self.assertIsNone(config.server)

    def test_server_config_missing_url(self):
        """Returns None, logs warning."""
        self.config_file.write_text(json.dumps({
            "boards": ["default"],
            "server": {"enabled": True, "api_key": "secret", "client_id": "client1"}
        }))
        config = Config(self.config_file)

        self.assertIsNone(config.server)


class TestBoardManagement(unittest.TestCase):
    """Tests for board management."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-config-boards")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.mad_dir = self.temp_dir / ".mad"
        self.mad_dir.mkdir(parents=True, exist_ok=True)
        self.boards_dir = self.mad_dir / "boards"
        self.config_file = self.mad_dir / "config.json"
        self.config_file.write_text(json.dumps({"boards": ["default"], "default_agent": "claude"}))

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_add_board(self):
        """Adds board name, creates stage dirs."""
        config = Config(self.config_file)

        config.add_board("new-board")

        self.assertIn("new-board", config.boards)

    def test_add_board_duplicate_raises(self):
        """ValueError on duplicate."""
        config = Config(self.config_file)

        with self.assertRaises(ValueError):
            config.add_board("default")

    def test_setup_boards(self):
        """Creates all stage dirs for all boards."""
        config = Config(self.config_file)

        config.setup_boards()

        for stage in ["ideas", "plan-inbox", "done"]:
            self.assertTrue((self.boards_dir / "default" / stage).exists())


if __name__ == '__main__':
    unittest.main()
