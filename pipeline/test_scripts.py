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

from scripts import ScriptConfig, ScriptStatus, load_scripts, run_script


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
