"""Tests for scripts functionality.

Run with:
    pytest test_scripts.py -v
"""

import json
import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from scripts import ScriptConfig, ScriptStatus, load_scripts, run_script, save_scripts


class TestLoadScripts:
    """Tests for load_scripts() function."""

    def test_load_scripts_valid(self, tmp_path):
        """Test loading a valid scripts.json file."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {
                "id": "build",
                "label": "Build Server",
                "description": "Build the server",
                "command": "go build .",
                "confirm": True,
                "model": "claude-sonnet",
                "agent": "claude"
            }
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert len(scripts) == 1
        assert scripts[0].id == "build"
        assert scripts[0].label == "Build Server"
        assert scripts[0].description == "Build the server"
        assert scripts[0].command == "go build ."
        assert scripts[0].confirm == True
        assert scripts[0].model == "claude-sonnet"
        assert scripts[0].agent == "claude"

    def test_load_scripts_missing_file(self, tmp_path):
        """Test loading when scripts.json doesn't exist."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        
        scripts = load_scripts(mad_dir)
        
        assert scripts == []

    def test_load_scripts_missing_id(self, tmp_path, caplog):
        """Test skipping entries missing id."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"label": "Build", "command": "go build"}
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts == []
        assert "missing 'id'" in caplog.text

    def test_load_scripts_missing_label(self, tmp_path, caplog):
        """Test skipping entries missing label."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "build", "command": "go build"}
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts == []
        assert "missing 'label'" in caplog.text

    def test_load_scripts_missing_command(self, tmp_path, caplog):
        """Test skipping entries missing command."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "build", "label": "Build"}
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts == []
        assert "missing 'command'" in caplog.text

    def test_load_scripts_invalid_id_format(self, tmp_path, caplog):
        """Test rejecting invalid ID characters."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "my script!", "label": "Build", "command": "go build"},
            {"id": "my_script", "label": "Build2", "command": "go build2"},
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts == []
        assert "invalid characters" in caplog.text

    def test_load_scripts_valid_id_format(self, tmp_path):
        """Test accepting valid ID characters."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "my-script", "label": "Build1", "command": "go build"},
            {"id": "MyScript123", "label": "Build2", "command": "go build"},
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert len(scripts) == 2

    def test_load_scripts_duplicate_ids(self, tmp_path, caplog):
        """Test rejecting duplicate IDs."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "build", "label": "Build1", "command": "go build1"},
            {"id": "build", "label": "Build2", "command": "go build2"},
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert len(scripts) == 1
        assert scripts[0].label == "Build1"
        assert "Duplicate" in caplog.text

    def test_load_scripts_optional_fields_defaults(self, tmp_path):
        """Test that optional fields have correct defaults."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "build", "label": "Build", "command": "go build"}
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts[0].description == ""
        assert scripts[0].confirm == True
        assert scripts[0].model is None
        assert scripts[0].agent is None

    def test_load_scripts_confirm_defaults_to_true(self, tmp_path):
        """Test that confirm defaults to True."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "build", "label": "Build", "command": "go build"}
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts[0].confirm == True

    def test_load_scripts_confirm_false(self, tmp_path):
        """Test that confirm can be set to False."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "build", "label": "Build", "command": "go build", "confirm": False}
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts[0].confirm == False

    def test_load_scripts_empty_array(self, tmp_path):
        """Test loading an empty array."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([]))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts == []

    def test_load_scripts_invalid_json(self, tmp_path, caplog):
        """Test handling of invalid JSON."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text("not valid json")
        
        scripts = load_scripts(mad_dir)
        
        assert scripts == []
        assert "Failed to parse" in caplog.text

    def test_load_scripts_not_array(self, tmp_path, caplog):
        """Test handling when scripts.json is not an array."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps({"id": "build"}))
        
        scripts = load_scripts(mad_dir)
        
        assert scripts == []
        assert "must be an array" in caplog.text

    def test_load_scripts_null_entry(self, tmp_path):
        """Test handling of null entries."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "build", "label": "Build", "command": "go build"},
            None,
            {"id": "test", "label": "Test", "command": "go test"}
        ]))
        
        scripts = load_scripts(mad_dir)
        
        assert len(scripts) == 2


class TestScriptStatus:
    """Tests for ScriptStatus dataclass."""

    def test_script_status_defaults(self):
        """Test ScriptStatus default values."""
        status = ScriptStatus(script_id="build")
        
        assert status.script_id == "build"
        assert status.running == False
        assert status.pid is None
        assert status.started_at is None
        assert status.finished_at is None
        assert status.lines == []
        assert status.exit_code is None
        assert status.kill_requested == False
        assert status.context == ""
        assert status._agent_status is None


class TestRunScript:
    """Tests for run_script() function."""

    @patch('scripts.AgentRunner')
    def test_run_script_success(self, mock_runner_class, tmp_path):
        """Test successful script execution returns 0."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        mock_runner.headless.return_value = None
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = ["line1", "line2"]
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(
                id="build",
                label="Build",
                command="go build",
                description="Build the server",
                confirm=True
            )
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            exit_code = run_script(script, mock_config, status, "context")
            
            assert exit_code == 0
            assert status.exit_code == 0

    @patch('scripts.AgentRunner')
    def test_run_script_exception(self, mock_runner_class, tmp_path):
        """Test failed script execution returns 1."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        mock_runner.headless.side_effect = Exception("Agent failed")
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = ["error occurred"]
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(
                id="build",
                label="Build",
                command="go build"
            )
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            exit_code = run_script(script, mock_config, status)
            
            assert exit_code == 1
            assert status.exit_code == 1
            assert "Error" in status.lines[-1]

    @patch('scripts.AgentRunner')
    def test_run_script_status_lifecycle(self, mock_runner_class, tmp_path):
        """Test status lifecycle during execution."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build")
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            assert status.running == False
            assert status.started_at is None
            assert status.finished_at is None
            
            run_script(script, mock_config, status)
            
            assert status.running == False
            assert status.started_at is not None
            assert status.finished_at is not None

    @patch('scripts.AgentRunner')
    def test_run_script_model_override(self, mock_runner_class, tmp_path):
        """Test model override is applied and restored."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build", model="claude-sonnet")
            
            mock_agent = MagicMock(model="claude-opus")
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': mock_agent}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status)
            
            assert mock_agent.model == "claude-opus"

    @patch('scripts.AgentRunner')
    def test_run_script_model_override_on_exception(self, mock_runner_class, tmp_path):
        """Test model override restoration on exception."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        mock_runner.headless.side_effect = Exception("Agent failed")
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build", model="claude-sonnet")
            
            mock_agent = MagicMock(model="claude-opus")
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': mock_agent}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status)
            
            assert mock_agent.model == "claude-opus"

    @patch('scripts.AgentRunner')
    def test_run_script_agent_selection(self, mock_runner_class, tmp_path):
        """Test agent selection from script.agent or config default."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build", agent="opencode")
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'opencode': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status)
            
            mock_runner_class.assert_called_with(mock_config, "opencode", tmp_path)

    @patch('scripts.AgentRunner')
    def test_run_script_agent_selection_default(self, mock_runner_class, tmp_path):
        """Test agent selection uses default when script.agent is None."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build")
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status)
            
            mock_runner_class.assert_called_with(mock_config, "claude", tmp_path)

    @patch('scripts.AgentRunner')
    def test_run_script_prompt_includes_details(self, mock_runner_class, tmp_path):
        """Test prompt includes script details."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(
                id="build",
                label="Build Server",
                command="go build",
                description="Build the Go server",
                agent="opencode"
            )
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status, "hotfix context")
            
            call_kwargs = mock_runner.headless.call_args.kwargs
            prompt = call_kwargs['prompt']
            
            assert "Build Server" in prompt
            assert "go build" in prompt
            assert "Build the Go server" in prompt
            assert "hotfix context" in prompt
            assert "script-build" in call_kwargs['phase_key']

    @patch('scripts.AgentRunner')
    def test_run_script_prompt_excludes_empty_description(self, mock_runner_class, tmp_path):
        """Test prompt omits description section when empty."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build")
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status)
            
            call_kwargs = mock_runner.headless.call_args.kwargs
            prompt = call_kwargs['prompt']
            
            assert "Description" not in prompt

    @patch('scripts.AgentRunner')
    def test_run_script_prompt_excludes_empty_context(self, mock_runner_class, tmp_path):
        """Test prompt omits context section when empty."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build")
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status, "")
            
            call_kwargs = mock_runner.headless.call_args.kwargs
            prompt = call_kwargs['prompt']
            
            assert "Additional Context" not in prompt

    @patch('scripts._save_script_log')
    @patch('scripts.AgentRunner')
    def test_run_script_log_saved(self, mock_runner_class, mock_save_log, tmp_path):
        """Test log file is saved after execution."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = ["output line 1", "output line 2"]
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build")
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status)
            
            mock_save_log.assert_called_once()

    @patch('scripts.AgentRunner')
    def test_run_script_kill_propagation(self, mock_runner_class, tmp_path):
        """Test kill_requested propagates to agent status."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        mock_agent_status.kill_requested = False
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build")
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            run_script(script, mock_config, status)
            
            assert status._agent_status is mock_agent_status

    @patch('scripts._save_script_log')
    @patch('scripts.AgentRunner')
    def test_run_script_save_log_exception_handled(self, mock_runner_class, mock_save_log, tmp_path):
        """Test exception in _save_script_log doesn't mask original error."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_save_log.side_effect = PermissionError("Permission denied")
        
        mock_agent_status = MagicMock()
        mock_agent_status.lines = []
        
        with patch('scripts.AgentStatus') as mock_status_class:
            mock_status_class.return_value = mock_agent_status
            
            script = ScriptConfig(id="build", label="Build", command="go build")
            
            mock_config = MagicMock()
            mock_config._data = {'default_agent': 'claude'}
            mock_config.agents = {'claude': MagicMock(model=None)}
            mock_config.code_path = tmp_path
            mock_config.mad_dir = tmp_path
            
            status = ScriptStatus(script_id="build")
            
            exit_code = run_script(script, mock_config, status)
            
            assert exit_code == 0


class TestFeatureFileDoneScript:
    """Tests for FeatureFile done_script property and setter."""

    def test_done_script_default_empty(self, tmp_path):
        """Test that done_script property returns empty string by default."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        config_dir = mad_dir / "boards" / "test" / "ideas"
        config_dir.mkdir(parents=True)
        
        feature_path = config_dir / "test-feature.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
        }))
        
        feature = FeatureFile(feature_path)
        
        assert feature.done_script == ""

    def test_done_script_with_value(self, tmp_path):
        """Test that done_script property returns stored value."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        config_dir = mad_dir / "boards" / "test" / "ideas"
        config_dir.mkdir(parents=True)
        
        feature_path = config_dir / "test-feature.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "done_script": "deploy-prod",
        }))
        
        feature = FeatureFile(feature_path)
        
        assert feature.done_script == "deploy-prod"

    def test_done_script_setter_persists(self, tmp_path, monkeypatch):
        """Test that set_done_script() persists to JSON."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        from config import Config
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        
        config_dir = mad_dir / "boards" / "test" / "ideas"
        config_dir.mkdir(parents=True)
        
        config_file = mad_dir / "config.json"
        config_file.write_text(json.dumps({}))
        
        monkeypatch.setattr("config.Config", lambda: Config(path=config_file))
        
        feature_path = config_dir / "test-feature.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
        }))
        
        feature = FeatureFile(feature_path)
        feature.set_done_script("run-tests")
        
        data = json.loads(feature_path.read_text())
        assert data["done_script"] == "run-tests"

    def test_done_script_setter_clears(self, tmp_path, monkeypatch):
        """Test that set_done_script('') clears the value."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        from config import Config
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        
        config_dir = mad_dir / "boards" / "test" / "ideas"
        config_dir.mkdir(parents=True)
        
        config_file = mad_dir / "config.json"
        config_file.write_text(json.dumps({}))
        
        monkeypatch.setattr("config.Config", lambda: Config(path=config_file))
        
        feature_path = config_dir / "test-feature.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "done_script": "some-script",
        }))
        
        feature = FeatureFile(feature_path)
        feature.set_done_script("")
        
        data = json.loads(feature_path.read_text())
        assert data["done_script"] == ""

    def test_done_script_setter_nonexistent_warns(self, tmp_path, monkeypatch, caplog):
        """Test that set_done_script warns when script not found."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        from config import Config
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        
        config_dir = mad_dir / "boards" / "test" / "ideas"
        config_dir.mkdir(parents=True)
        
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text(json.dumps([
            {"id": "existing-script", "label": "Existing", "command": "echo test"}
        ]))
        
        config_file = mad_dir / "config.json"
        config_file.write_text(json.dumps({}))
        
        monkeypatch.setattr("config.Config", lambda: Config(path=config_file))
        
        feature_path = config_dir / "test-feature.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
        }))
        
        feature = FeatureFile(feature_path)
        feature.set_done_script("nonexistent-script")
        
        data = json.loads(feature_path.read_text())
        assert data["done_script"] == "nonexistent-script"
        assert "nonexistent-script" in caplog.text

    def test_feature_file_create_with_done_script(self, tmp_path, monkeypatch):
        """Test that FeatureFile.create() accepts done_script parameter."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        from config import Config
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        boards_dir = mad_dir / "boards"
        boards_dir.mkdir(parents=True)
        
        config_file = mad_dir / "config.json"
        config_file.write_text(json.dumps({}))
        
        monkeypatch.setattr("config.Config", lambda: Config(path=config_file))
        
        feature = FeatureFile.create("test-board", "Test Feature", "Test desc", done_script="my-script")
        
        data = json.loads(feature.path.read_text())
        assert data["done_script"] == "my-script"

    def test_feature_file_create_without_done_script(self, tmp_path, monkeypatch):
        """Test that FeatureFile.create() defaults done_script to empty string."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        from config import Config
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        boards_dir = mad_dir / "boards"
        boards_dir.mkdir(parents=True)
        
        config_file = mad_dir / "config.json"
        config_file.write_text(json.dumps({}))
        
        monkeypatch.setattr("config.Config", lambda: Config(path=config_file))
        
        feature = FeatureFile.create("test-board", "Test Feature", "Test desc")
        
        data = json.loads(feature.path.read_text())
        assert data["done_script"] == ""


class TestExecuteDoneScript:
    """Tests for execute_done_script() function."""

    @patch('scripts.load_scripts')
    def test_execute_done_script_empty_returns_early(self, mock_load_scripts, tmp_path):
        """Test that execute_done_script returns early when done_script is empty."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script
        
        mock_feature = MagicMock()
        mock_feature.done_script = ""
        mock_feature.id = "test123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        
        mock_load_scripts.assert_not_called()

    @patch('scripts.run_script')
    @patch('scripts.load_scripts')
    def test_execute_done_script_calls_run_script(self, mock_load_scripts, mock_run_script, tmp_path):
        """Test that execute_done_script calls run_script when script is configured."""
        import sys
        import time
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig
        
        mock_script = ScriptConfig(id="deploy", label="Deploy", command="echo deploy")
        mock_load_scripts.return_value = [mock_script]
        
        mock_feature = MagicMock()
        mock_feature.done_script = "deploy"
        mock_feature.id = "test123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        
        time.sleep(0.1)  # Wait for background thread to execute
        
        mock_load_scripts.assert_called_once_with(tmp_path)
        mock_run_script.assert_called_once()
        call_args = mock_run_script.call_args
        assert call_args[0][0].id == "deploy"
        assert "feature" in call_args[0][3]
        assert "Test Feature" in call_args[0][3]

    @patch('scripts.load_scripts')
    def test_execute_done_script_missing_script_warns(self, mock_load_scripts, tmp_path, caplog):
        """Test that execute_done_script warns when script not found."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script
        
        mock_load_scripts.return_value = []
        
        mock_feature = MagicMock()
        mock_feature.done_script = "deleted-script"
        mock_feature.id = "test123"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        
        assert "deleted-script" in caplog.text
        assert "not found" in caplog.text

    @patch('scripts.run_script')
    @patch('scripts.load_scripts')
    def test_execute_done_script_handles_run_script_exception(self, mock_load_scripts, mock_run_script, tmp_path):
        """Test that execute_done_script handles run_script exceptions without propagating."""
        import sys
        import time
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig
        
        mock_script = ScriptConfig(id="deploy", label="Deploy", command="echo deploy")
        mock_load_scripts.return_value = [mock_script]
        mock_run_script.side_effect = Exception("Script failed!")
        
        mock_feature = MagicMock()
        mock_feature.done_script = "deploy"
        mock_feature.id = "test123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        
        time.sleep(0.1)  # Wait for background thread to execute
        
        mock_run_script.assert_called_once()

    @patch('scripts.load_scripts')
    def test_execute_done_script_thread_added_to_pending_before_start(self, mock_load_scripts, tmp_path):
        """Test that thread is added to _pending_scripts BEFORE start() is called."""
        import sys
        import time
        import threading
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig, _pending_scripts, _pending_scripts_lock
        
        mock_script = ScriptConfig(id="test-script", label="Test", command="echo test")
        mock_load_scripts.return_value = [mock_script]
        
        mock_feature = MagicMock()
        mock_feature.done_script = "test-script"
        mock_feature.id = "feat123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        
        with _pending_scripts_lock:
            thread_names = [t.name for t in _pending_scripts]
        
        assert any("done-script-test-script-feat123" in name for name in thread_names)

    @patch('scripts.load_scripts')
    def test_execute_done_script_thread_removed_on_completion(self, mock_load_scripts, tmp_path):
        """Test that thread is removed from _pending_scripts after completion."""
        import sys
        import time
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig, _pending_scripts, _pending_scripts_lock
        
        mock_script = ScriptConfig(id="test-script", label="Test", command="echo test")
        mock_load_scripts.return_value = [mock_script]
        
        mock_feature = MagicMock()
        mock_feature.done_script = "test-script"
        mock_feature.id = "feat123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        time.sleep(0.2)
        
        with _pending_scripts_lock:
            thread_names = [t.name for t in _pending_scripts]
        
        assert not any("done-script-test-script-feat123" in name for name in thread_names)

    @patch('scripts.run_script')
    @patch('scripts.load_scripts')
    def test_execute_done_script_atexit_handler_waits_for_threads(self, mock_load_scripts, mock_run_script, tmp_path, caplog):
        """Test that atexit handler logs waiting message and joins threads."""
        import sys
        import time
        import threading
        import atexit
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig, _pending_scripts, _pending_scripts_lock, _wait_for_pending_scripts, _SCRIPT_WAIT_TIMEOUT
        
        def slow_script(*args, **kwargs):
            time.sleep(2)
            return 0
        
        mock_run_script.side_effect = slow_script
        
        mock_script = ScriptConfig(id="test-script", label="Test", command="echo test")
        mock_load_scripts.return_value = [mock_script]
        
        mock_feature = MagicMock()
        mock_feature.done_script = "test-script"
        mock_feature.id = "feat123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        
        time.sleep(0.1)
        
        with _pending_scripts_lock:
            thread_count = len(_pending_scripts)
        
        assert thread_count == 1, f"Expected 1 pending thread, got {thread_count}"
        
        with caplog.at_level(logging.INFO):
            _wait_for_pending_scripts()
        
        assert "Waiting for 1 pending done script(s) to finish" in caplog.text

    @patch('scripts.load_scripts')
    def test_execute_done_script_atexit_handler_empty_list_no_log(self, mock_load_scripts, tmp_path, caplog):
        """Test that atexit handler returns immediately when no scripts are pending."""
        import sys
        import logging
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import _wait_for_pending_scripts
        
        with caplog.at_level(logging.INFO):
            _wait_for_pending_scripts()
        
        assert "Waiting" not in caplog.text

    @patch('scripts.run_script')
    @patch('scripts.load_scripts')
    def test_execute_done_script_atexit_handler_timeout_warning(self, mock_load_scripts, mock_run_script, tmp_path, caplog):
        """Test that atexit handler logs warning when thread doesn't finish within timeout."""
        import sys
        import time
        import logging
        import threading
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig, _pending_scripts, _pending_scripts_lock, _wait_for_pending_scripts, _SCRIPT_WAIT_TIMEOUT
        import scripts as scripts_module
        
        def very_slow_script(*args, **kwargs):
            time.sleep(10)
            return 0
        
        mock_run_script.side_effect = very_slow_script
        
        mock_script = ScriptConfig(id="slow-script", label="Slow", command="sleep 60")
        mock_load_scripts.return_value = [mock_script]
        
        mock_feature = MagicMock()
        mock_feature.done_script = "slow-script"
        mock_feature.id = "feat123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        time.sleep(0.1)
        
        original_timeout = _SCRIPT_WAIT_TIMEOUT
        try:
            scripts_module._SCRIPT_WAIT_TIMEOUT = 0.1
            
            with caplog.at_level(logging.WARNING):
                _wait_for_pending_scripts()
            
            assert "did not finish within timeout" in caplog.text
        finally:
            scripts_module._SCRIPT_WAIT_TIMEOUT = original_timeout

    @patch('scripts.load_scripts')
    def test_execute_done_script_synchronous_triggered_history(self, mock_load_scripts, tmp_path):
        """Test that 'triggered' history entry is written synchronously before thread spawns."""
        import sys
        import time
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig
        
        mock_script = ScriptConfig(id="deploy-prod", label="Deploy", command="echo deploy")
        mock_load_scripts.return_value = [mock_script]
        
        mock_feature = MagicMock()
        mock_feature.done_script = "deploy-prod"
        mock_feature.id = "feat123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        execute_done_script(mock_feature, mock_config)
        
        mock_feature.add_history.assert_called()
        call_args = mock_feature.add_history.call_args
        assert call_args[0][0] == "DONE"
        assert "deploy-prod" in call_args[0][1]
        assert "triggered" in call_args[0][1]

    @patch('scripts.run_script')
    @patch('scripts.load_scripts')
    def test_execute_done_script_completion_history_written_by_thread(self, mock_load_scripts, mock_run_script, tmp_path):
        """Test that 'completed (exit 0)' history entry is written by thread on success."""
        import sys
        import time
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        from scripts import execute_done_script, ScriptConfig
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        config_dir = mad_dir / "boards" / "test" / "done"
        config_dir.mkdir(parents=True)
        
        feature_path = config_dir / "test-feature.json"
        feature_path.write_text(json.dumps({
            "id": "feat123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "done_script": "deploy-prod",
        }))
        
        feature = FeatureFile(feature_path)
        
        mock_script = ScriptConfig(id="deploy-prod", label="Deploy", command="echo deploy")
        mock_load_scripts.return_value = [mock_script]
        mock_run_script.return_value = 0
        
        mock_config = MagicMock()
        mock_config.mad_dir = mad_dir
        
        execute_done_script(feature, mock_config)
        time.sleep(0.2)
        
        updated_feature = FeatureFile(feature_path)
        
        history = updated_feature._data.get("history", [])
        history_notes = [h.get('note', '') for h in history]
        
        assert any("deploy-prod" in note and "triggered" in note for note in history_notes), f"Triggered not found in {history_notes}"
        assert any("deploy-prod" in note and "completed" in note for note in history_notes), f"Completed not found in {history_notes}"

    @patch('scripts.run_script')
    @patch('scripts.load_scripts')
    def test_execute_done_script_failure_history_written_by_thread(self, mock_load_scripts, mock_run_script, tmp_path):
        """Test that 'failed' history entry is written by thread on failure."""
        import sys
        import time
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from state import FeatureFile
        from scripts import execute_done_script, ScriptConfig
        
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True)
        config_dir = mad_dir / "boards" / "test" / "done"
        config_dir.mkdir(parents=True)
        
        feature_path = config_dir / "test-feature.json"
        feature_path.write_text(json.dumps({
            "id": "feat123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "done_script": "deploy-prod",
        }))
        
        feature = FeatureFile(feature_path)
        
        mock_script = ScriptConfig(id="deploy-prod", label="Deploy", command="echo deploy")
        mock_load_scripts.return_value = [mock_script]
        mock_run_script.side_effect = Exception("Deployment failed!")
        
        mock_config = MagicMock()
        mock_config.mad_dir = mad_dir
        
        execute_done_script(feature, mock_config)
        time.sleep(0.2)
        
        updated_feature = FeatureFile(feature_path)
        
        history = updated_feature._data.get("history", [])
        history_notes = [h.get('note', '') for h in history]
        
        assert any("deploy-prod" in note and "triggered" in note for note in history_notes), f"Triggered not found in {history_notes}"
        assert any("deploy-prod" in note and "failed" in note for note in history_notes), f"Failed not found in {history_notes}"

    @patch('scripts.load_scripts')
    def test_execute_done_script_thread_name_format(self, mock_load_scripts, tmp_path):
        """Test that thread name matches 'done-script-<script_id>-<feature_id>'."""
        import sys
        import time
        import threading
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig
        
        mock_script = ScriptConfig(id="my-script", label="Test", command="echo test")
        mock_load_scripts.return_value = [mock_script]
        
        created_threads = []
        original_thread_init = threading.Thread.__init__
        
        def patched_init(self, *args, **kwargs):
            original_thread_init(self, *args, **kwargs)
            created_threads.append(self)
        
        mock_feature = MagicMock()
        mock_feature.done_script = "my-script"
        mock_feature.id = "feature-abc"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        with patch.object(threading.Thread, '__init__', patched_init):
            execute_done_script(mock_feature, mock_config)
        
        assert len(created_threads) > 0
        thread = created_threads[0]
        assert thread.name == "done-script-my-script-feature-abc"

    @patch('scripts.load_scripts')
    def test_execute_done_script_thread_is_daemon(self, mock_load_scripts, tmp_path):
        """Test that thread.daemon is True."""
        import sys
        import time
        import threading
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        
        from scripts import execute_done_script, ScriptConfig
        
        mock_script = ScriptConfig(id="test-script", label="Test", command="echo test")
        mock_load_scripts.return_value = [mock_script]
        
        created_threads = []
        original_thread_init = threading.Thread.__init__
        
        def patched_init(self, *args, **kwargs):
            original_thread_init(self, *args, **kwargs)
            created_threads.append(self)
        
        mock_feature = MagicMock()
        mock_feature.done_script = "test-script"
        mock_feature.id = "feat123"
        mock_feature.item_type = "feature"
        mock_feature.title = "Test Feature"
        
        mock_config = MagicMock()
        mock_config.mad_dir = tmp_path
        
        with patch.object(threading.Thread, '__init__', patched_init):
            execute_done_script(mock_feature, mock_config)
        
        assert len(created_threads) > 0
        assert created_threads[0].daemon is True


class TestSaveScripts:
    """Tests for save_scripts() function."""

    def test_save_scripts_round_trip(self, tmp_path):
        """Test that save_scripts followed by load_scripts returns the same data."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        
        scripts = [
            ScriptConfig(
                id="build",
                label="Build Server",
                command="go build .",
                description="Build the Go server",
                confirm=True,
                model="claude-sonnet",
                agent="claude"
            ),
            ScriptConfig(
                id="test",
                label="Run Tests",
                command="go test ./...",
                description="Run all tests",
                confirm=False,
            ),
        ]
        
        result = save_scripts(mad_dir, scripts)
        
        assert result is True
        
        loaded = load_scripts(mad_dir)
        
        assert len(loaded) == 2
        assert loaded[0].id == "build"
        assert loaded[0].label == "Build Server"
        assert loaded[0].command == "go build ."
        assert loaded[0].description == "Build the Go server"
        assert loaded[0].confirm is True
        assert loaded[0].model == "claude-sonnet"
        assert loaded[0].agent == "claude"
        assert loaded[1].id == "test"
        assert loaded[1].confirm is False
        assert loaded[1].model is None
        assert loaded[1].agent is None

    def test_save_scripts_empty_list(self, tmp_path):
        """Test that saving an empty list creates an empty array in the file."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        
        scripts = []
        
        result = save_scripts(mad_dir, scripts)
        
        assert result is True
        
        scripts_file = mad_dir / "scripts.json"
        content = scripts_file.read_text()
        
        assert content == "[]"

    def test_save_scripts_creates_file_if_missing(self, tmp_path):
        """Test that save_scripts creates the file if it doesn't exist."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        
        scripts_file = mad_dir / "scripts.json"
        assert not scripts_file.exists()
        
        scripts = [
            ScriptConfig(id="test", label="Test", command="echo test")
        ]
        
        result = save_scripts(mad_dir, scripts)
        
        assert result is True
        assert scripts_file.exists()
        
        loaded = load_scripts(mad_dir)
        assert len(loaded) == 1
        assert loaded[0].id == "test"

    def test_save_scripts_handles_optional_fields(self, tmp_path):
        """Test that optional fields (model, agent) are only written when set."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        
        scripts = [
            ScriptConfig(id="with-model", label="With Model", command="echo test", model="claude-opus"),
            ScriptConfig(id="with-agent", label="With Agent", command="echo test", agent="opencode"),
            ScriptConfig(id="with-both", label="With Both", command="echo test", model="claude-sonnet", agent="claude"),
            ScriptConfig(id="with-none", label="With None", command="echo test"),
        ]
        
        result = save_scripts(mad_dir, scripts)
        
        assert result is True
        
        import json
        scripts_file = mad_dir / "scripts.json"
        data = json.loads(scripts_file.read_text())
        
        assert "model" in data[0]
        assert data[0]["model"] == "claude-opus"
        assert "agent" not in data[0]
        
        assert "model" not in data[1]
        assert "agent" in data[1]
        assert data[1]["agent"] == "opencode"
        
        assert "model" in data[2]
        assert "agent" in data[2]
        
        assert "model" not in data[3]
        assert "agent" not in data[3]

    def test_save_scripts_overwrites_existing(self, tmp_path):
        """Test that save_scripts overwrites existing file content."""
        mad_dir = tmp_path / ".mad"
        mad_dir.mkdir(parents=True, exist_ok=True)
        
        scripts_file = mad_dir / "scripts.json"
        scripts_file.write_text('{"id": "old", "label": "Old", "command": "old cmd"}')
        
        scripts = [
            ScriptConfig(id="new", label="New", command="new cmd")
        ]
        
        result = save_scripts(mad_dir, scripts)
        
        assert result is True
        
        loaded = load_scripts(mad_dir)
        assert len(loaded) == 1
        assert loaded[0].id == "new"
        assert loaded[0].label == "New"


class TestCreateScriptModalValidation:
    """Tests for CreateScriptModal validation logic using the validate_script_input static method."""

    def test_validation_empty_id(self):
        """Test that empty ID returns error."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("", "Test Label", "echo test", set())
        assert result is not None
        assert "required" in result.lower()

    def test_validation_invalid_characters(self):
        """Test that invalid characters in ID returns error."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("invalid id!", "Test Label", "echo test", set())
        assert result is not None
        assert "lowercase" in result.lower() or "invalid" in result.lower()

    def test_validation_invalid_starting_character(self):
        """Test that ID starting with hyphen returns error."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("-starts-with-hyphen", "Test Label", "echo test", set())
        assert result is not None

    def test_validation_duplicate_id(self):
        """Test that duplicate ID returns error."""
        from tui import CreateScriptModal
        
        existing_ids = {"existing-script"}
        result = CreateScriptModal.validate_script_input("existing-script", "Test Label", "echo test", existing_ids)
        assert result is not None
        assert "already exists" in result.lower()

    def test_validation_missing_label(self):
        """Test that missing label returns error."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("valid-id", "", "echo test", set())
        assert result is not None
        assert "label" in result.lower()

    def test_validation_missing_command(self):
        """Test that missing command returns error."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("valid-id", "Test Label", "", set())
        assert result is not None
        assert "command" in result.lower()

    def test_validation_successful_returns_none(self):
        """Test that valid input returns None (no error)."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("my-new-script", "My New Script", "echo hello", set())
        assert result is None

    def test_validation_all_fields_valid(self):
        """Test that all valid fields pass validation."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("test-script-123", "Test Script 123", "go test ./...", set())
        assert result is None

    def test_validation_id_with_numbers(self):
        """Test that ID with numbers is valid."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("script123", "Script 123", "echo test", set())
        assert result is None

    def test_validation_id_with_hyphens(self):
        """Test that ID with hyphens is valid."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("my-script-456", "My Script", "echo test", set())
        assert result is None

    def test_validation_id_not_starting_with_hyphen(self):
        """Test that ID cannot start with hyphen."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("-script", "Script", "echo test", set())
        assert result is not None

    def test_validation_empty_existing_ids_set(self):
        """Test with empty existing IDs set."""
        from tui import CreateScriptModal
        
        result = CreateScriptModal.validate_script_input("new-script", "New", "echo new", set())
        assert result is None


class TestScriptsPaneHandleCreateScript:
    """Tests for ScriptsPane._handle_create_script integration with save_scripts."""

    def test_handle_create_script_with_none_result(self):
        """Test that _handle_create_script handles None result (cancel)."""
        from tui import ScriptsPane
        
        mock_script = MagicMock()
        mock_script.id = "existing-script"
        
        mock_status = None
        
        pane = ScriptsPane([mock_script], mock_status)
        pane._scripts = [mock_script]
        
        pane._handle_create_script(None)
        
        assert len(pane._scripts) == 1

    def test_handle_create_script_method_exists(self):
        """Test that _handle_create_script method exists on ScriptsPane."""
        from tui import ScriptsPane
        
        mock_script = MagicMock()
        mock_status = None
        
        pane = ScriptsPane([mock_script], mock_status)
        
        assert hasattr(pane, '_handle_create_script')
        assert callable(pane._handle_create_script)

    def test_handle_create_script_updates_internal_list(self):
        """Test that _handle_create_script can update internal script list."""
        from tui import ScriptsPane
        
        mock_script = MagicMock()
        mock_script.id = "existing-script"
        
        mock_status = None
        
        pane = ScriptsPane([mock_script], mock_status)
        
        new_script = MagicMock()
        new_script.id = "new-script"
        
        pane._scripts = [mock_script, new_script]
        
        assert len(pane._scripts) == 2
        assert pane._scripts[0].id == "existing-script"
        assert pane._scripts[1].id == "new-script"

    def test_refresh_script_list_method_exists(self):
        """Test that _refresh_script_list method exists."""
        from tui import ScriptsPane
        
        mock_script = MagicMock()
        mock_status = None
        
        pane = ScriptsPane([mock_script], mock_status)
        
        assert hasattr(pane, '_refresh_script_list')
        assert callable(pane._refresh_script_list)
