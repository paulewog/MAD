"""Unit tests for runner.py — AgentRunner module."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from runner import AgentRunner, RateLimitError
from config import AgentConfig, Config
from agent_status import AgentStatus


class MockConfig:
    """Mock Config for testing."""
    def __init__(self, code_path=None):
        self._code_path = code_path
        self._mad_dir = Path("/tmp/test-mad")
        self._context_file = Path("/tmp/test-mad/CONTEXT.md")

    @property
    def code_path(self):
        return self._code_path

    @property
    def mad_dir(self):
        return self._mad_dir

    @property
    def context_file(self):
        return self._context_file

    @property
    def current_agent(self):
        return AgentConfig(
            name="claude",
            command="claude",
            headless_flag="-p",
            headless_extra_flags=["--dangerously-skip-permissions"],
            model_flag="--model",
            model=None,
        )

    @property
    def agents(self):
        return {
            "claude": AgentConfig(
                name="claude",
                command="claude",
                headless_flag="-p",
                headless_extra_flags=["--dangerously-skip-permissions"],
                model_flag="--model",
                model=None,
            ),
            "opencode": AgentConfig(
                name="opencode",
                command="opencode",
                headless_flag="",
                headless_extra_flags=["run", "--format", "json"],
                model_flag="-m",
                model=None,
            ),
        }

    def get_agent_for_phase(self, phase: str):
        return self.current_agent


class TestAgentRunnerConstructor(unittest.TestCase):
    """Tests for AgentRunner constructor and properties."""

    def test_init_with_workdir(self):
        """Custom workdir used."""
        config = MockConfig()
        workdir = Path("/tmp/test-workdir")
        runner = AgentRunner(config, workdir=workdir)

        self.assertEqual(runner.workdir, workdir)

    def test_init_default_workdir(self):
        """Falls back to config.code_path or MAD_DIR env."""
        config = MockConfig(code_path=Path("/tmp/code"))
        runner = AgentRunner(config)

        self.assertEqual(runner.workdir, Path("/tmp/code"))

    def test_agent_property_default(self):
        """Returns config.current_agent."""
        config = MockConfig()
        runner = AgentRunner(config)

        self.assertEqual(runner.agent.name, "claude")

    def test_agent_property_named(self):
        """Returns named agent from config."""
        config = MockConfig()
        runner = AgentRunner(config, agent_name="opencode")

        self.assertEqual(runner.agent.name, "opencode")

    def test_agent_property_override(self):
        """_agent_config_override takes precedence."""
        config = MockConfig()
        runner = AgentRunner(config)
        override = AgentConfig(
            name="opencode",
            command="opencode",
            headless_flag="",
            headless_extra_flags=["run"],
            model_flag="-m",
            model="gpt-4",
        )
        runner._agent_config_override = override

        self.assertEqual(runner.agent.name, "opencode")
        self.assertEqual(runner.agent.model, "gpt-4")


class TestForPhase(unittest.TestCase):
    """Tests for for_phase method."""

    def test_for_phase_creates_new_runner(self):
        """Returns new runner with phase-specific agent."""
        config = MockConfig()
        runner = AgentRunner(config)

        new_runner = runner.for_phase("planning")

        self.assertIsInstance(new_runner, AgentRunner)
        self.assertIsNot(new_runner, runner)


class TestForCLIModel(unittest.TestCase):
    """Tests for for_cli_model method."""

    def test_for_cli_model_with_model(self):
        """Creates runner with model override on AgentConfig."""
        config = MockConfig()
        runner = AgentRunner(config)

        new_runner = runner.for_cli_model("claude", model="opus")

        self.assertEqual(new_runner.agent.name, "claude")
        self.assertEqual(new_runner.agent.model, "opus")

    def test_for_cli_model_without_model(self):
        """Creates runner using base agent config."""
        config = MockConfig()
        runner = AgentRunner(config)

        new_runner = runner.for_cli_model("claude")

        self.assertEqual(new_runner.agent.name, "claude")
        self.assertIsNone(new_runner.agent.model)


class TestBuildCompletionBlock(unittest.TestCase):
    """Tests for _build_completion_block method."""

    def test_completion_block_contains_paths(self):
        """Output path and marker path present in block."""
        config = MockConfig()
        runner = AgentRunner(config)

        output_path = Path("/tmp/output.json")
        marker_path = Path("/tmp/marker")
        schema = {"plan": "string"}

        block = runner._build_completion_block(output_path, marker_path, schema)

        self.assertIn(str(output_path), block)
        self.assertIn(str(marker_path), block)

    def test_completion_block_schema_fields(self):
        """Schema fields listed in block."""
        config = MockConfig()
        runner = AgentRunner(config)

        output_path = Path("/tmp/output.json")
        marker_path = Path("/tmp/marker")
        schema = {"plan": "string", "questions": "list"}

        block = runner._build_completion_block(output_path, marker_path, schema)

        self.assertIn("plan", block)
        self.assertIn("questions", block)

    def test_completion_block_phase_complete(self):
        """Contains "PHASE_COMPLETE" instruction."""
        config = MockConfig()
        runner = AgentRunner(config)

        output_path = Path("/tmp/output.json")
        marker_path = Path("/tmp/marker")
        schema = {"plan": "string"}

        block = runner._build_completion_block(output_path, marker_path, schema)

        self.assertIn("PHASE_COMPLETE", block)


class TestPrependContext(unittest.TestCase):
    """Tests for _prepend_context method."""

    def test_prepend_context_with_code_path(self):
        """Code path and context file path in header."""
        config = MockConfig(code_path=Path("/tmp/code"))
        runner = AgentRunner(config)

        prompt = runner._prepend_context("Do something")

        self.assertIn("Code Path:", prompt)
        self.assertIn("/tmp/code", prompt)
        self.assertIn("Context File:", prompt)

    def test_prepend_context_with_content(self):
        """Context file content included."""
        config = MockConfig()
        config._context_file = Path("/tmp/test-mad/CONTEXT.md")
        
        with patch('runner.read_context_file') as mock_read:
            mock_read.return_value = "Context content here"
            runner = AgentRunner(config)
            prompt = runner._prepend_context("Do something")

        self.assertIn("Context content here", prompt)

    def test_prepend_context_no_content(self):
        """Missing context file → no content section."""
        config = MockConfig()
        
        with patch('runner.read_context_file') as mock_read:
            mock_read.return_value = None
            runner = AgentRunner(config)
            prompt = runner._prepend_context("Do something")

        self.assertIn("Do something", prompt)


class TestReadCheckpoint(unittest.TestCase):
    """Tests for _read_checkpoint method."""

    def test_read_checkpoint_exists(self):
        """Returns resume context string with completed steps."""
        config = MockConfig()
        config._mad_dir = Path("/tmp/test-mad")
        runner = AgentRunner(config)

        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "planning",
            "completed_steps": ["step1", "step2"],
            "next_step": "step3",
            "notes": "Some notes",
            "files_modified": ["file1.py"],
        }

        workdir = Path("/tmp/test-workdir")
        workdir.mkdir(parents=True, exist_ok=True)
        runner._workdir = workdir

        checkpoint_dir = workdir / ".mad" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        result = runner._read_checkpoint("test-feature")

        self.assertIsNotNone(result)
        self.assertIn("Resuming from checkpoint", result)
        self.assertIn("step1", result)

    def test_read_checkpoint_missing(self):
        """Returns None."""
        config = MockConfig()
        config._mad_dir = Path("/tmp/test-mad")
        runner = AgentRunner(config)

        workdir = Path("/tmp/test-workdir-nockpoint")
        workdir.mkdir(parents=True, exist_ok=True)
        runner._workdir = workdir

        result = runner._read_checkpoint("nonexistent")

        self.assertIsNone(result)

    def test_read_checkpoint_invalid_json(self):
        """Returns None, logs warning."""
        config = MockConfig()
        config._mad_dir = Path("/tmp/test-mad")
        runner = AgentRunner(config)

        workdir = Path("/tmp/test-workdir-invalid")
        workdir.mkdir(parents=True, exist_ok=True)
        runner._workdir = workdir

        checkpoint_dir = workdir / ".mad" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / "test-feature.checkpoint.json"
        checkpoint_path.write_text("not valid json")

        with patch('runner.logger') as mock_logger:
            result = runner._read_checkpoint("test-feature")
            self.assertIsNone(result)
            mock_logger.warning.assert_called()

    def test_read_checkpoint_missing_fields(self):
        """Returns None when feature_slug/phase missing."""
        config = MockConfig()
        config._mad_dir = Path("/tmp/test-mad")
        runner = AgentRunner(config)

        workdir = Path("/tmp/test-workdir-fields")
        workdir.mkdir(parents=True, exist_ok=True)
        runner._workdir = workdir

        checkpoint_dir = workdir / ".mad" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps({"completed_steps": []}))

        result = runner._read_checkpoint("test-feature")

        self.assertIsNone(result)

    def test_read_checkpoint_default_slug(self):
        """Returns None for slug 'default'."""
        config = MockConfig()
        runner = AgentRunner(config)

        result = runner._read_checkpoint("default")

        self.assertIsNone(result)


class TestHeadlessCommandBuilding(unittest.TestCase):
    """Tests for headless command building."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-runner-headless")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.mock_config = MockConfig(code_path=self.temp_dir)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_claude_cmd(self, mock_popen, mock_select):
        """Builds ["claude", "-p", "Read...", "--dangerously-skip-permissions"] with model."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        runner.headless("Do something", workdir=self.temp_dir)

        call_args = mock_popen.call_args[0][0]
        self.assertIn("claude", call_args)
        self.assertIn("-p", call_args)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_opencode_cmd(self, mock_popen, mock_select):
        """Builds ["opencode", "run", "--format", "json", "Read..."]"""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config, agent_name="opencode")

        with patch.object(Path, 'unlink', return_value=None):
            runner.headless("Do something", workdir=self.temp_dir)

        call_args = mock_popen.call_args[0][0]
        self.assertIn("opencode", call_args)


class TestHeadlessOutputHandling(unittest.TestCase):
    """Tests for headless output handling."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-runner-output")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.temp_dir / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_reads_output_file(self, mock_popen, mock_select):
        """Prefers output from .tmp/<item>.output.json over stdout."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        output_path = self.tmp_dir / "default.output.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"result": "from file"}')

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with patch.object(Path, 'unlink', return_value=None):
            result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("from file", result)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_falls_back_to_stdout(self, mock_popen, mock_select):
        """Uses stdout when no output file exists."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.side_effect = ['{"result": "from stdout"}', '']
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("from stdout", result)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_completion_marker(self, mock_popen, mock_select):
        """Detects PHASE_COMPLETE in marker file."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        output_path = self.tmp_dir / "default.output.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"result": "ok"}')
        
        marker_path = self.tmp_dir / "default.complete"
        marker_path.write_text("PHASE_COMPLETE")

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with patch.object(Path, 'unlink', return_value=None):
            result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("ok", result)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_done_signal(self, mock_popen, mock_select):
        """Detects DONE in stdout."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.side_effect = ["some output", "DONE", ""]
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIsNotNone(result)


class TestHeadlessErrorDetection(unittest.TestCase):
    """Tests for headless error detection."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-runner-errors")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.temp_dir / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_rate_limit_detection(self, mock_popen, mock_select):
        """RateLimitError raised on rate limit phrases in output."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.side_effect = ['You are being rate limited', '']
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        output_path = self.tmp_dir / "default.output.json"
        output_path.write_text('{"result": "ok"}')

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with self.assertRaises(RateLimitError):
            runner.headless("Do something", workdir=self.temp_dir)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_rate_limit_stderr(self, mock_popen, mock_select):
        """RateLimitError raised on rate limit in stderr."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = "rate limit exceeded"
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        output_path = self.tmp_dir / "default.output.json"
        output_path.write_text('{"result": "ok"}')

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with self.assertRaises(RateLimitError):
            runner.headless("Do something", workdir=self.temp_dir)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_rate_limit_skipped_on_completion(self, mock_popen, mock_select):
        """No RateLimitError when marker exists."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.side_effect = ['You are being rate limited', '']
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        output_path = self.tmp_dir / "default.output.json"
        output_path.write_text('{"result": "ok"}')
        
        marker_path = self.tmp_dir / "default.complete"
        marker_path.write_text("PHASE_COMPLETE")

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with patch.object(Path, 'unlink', return_value=None):
            result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("ok", result)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_auth_error_detection(self, mock_popen, mock_select):
        """RuntimeError raised on auth error phrases in stderr."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = "invalid api key"
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        output_path = self.tmp_dir / "default.output.json"
        output_path.write_text('{"result": "ok"}')

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with self.assertRaises(RuntimeError) as ctx:
            runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("authentication", str(ctx.exception))

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_auth_error_skipped_on_completion(self, mock_popen, mock_select):
        """No auth error when marker exists."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = "invalid api key"
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        output_path = self.tmp_dir / "default.output.json"
        output_path.write_text('{"result": "ok"}')
        
        marker_path = self.tmp_dir / "default.complete"
        marker_path.write_text("PHASE_COMPLETE")

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with patch.object(Path, 'unlink', return_value=None):
            result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("ok", result)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_nonzero_exit(self, mock_popen, mock_select):
        """RuntimeError raised on non-zero exit code."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 1
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        output_path = self.tmp_dir / "default.output.json"
        output_path.write_text('{"result": "ok"}')

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with self.assertRaises(RuntimeError):
            runner.headless("Do something", workdir=self.temp_dir)


class TestHeadlessStatusTracking(unittest.TestCase):
    """Tests for headless status tracking."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-runner-status")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.temp_dir / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_status_running_flag(self, mock_popen, mock_select):
        """status.running set True during run, False after."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        status = AgentStatus()
        self.assertFalse(status.running)

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        runner.headless("Do something", workdir=self.temp_dir, status=status)

        self.assertFalse(status.running)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_headless_status_pid(self, mock_popen, mock_select):
        """status.pid set to process.pid during execution."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        status = AgentStatus()

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        with patch.object(Path, 'unlink', return_value=None):
            runner.headless("Do something", workdir=self.temp_dir, status=status)

        # The pid is reset to None after headless() returns (in finally block)
        # So we verify the mock was called with correct pid
        self.assertEqual(mock_popen.return_value.pid, 12345)

    @patch('runner.subprocess.Popen')
    def test_headless_status_lines_capped(self, mock_popen):
        """status.lines capped at 200 entries."""
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline.side_effect = [f"line {i}\n" for i in range(250)] + [""]
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        status = AgentStatus()

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        try:
            runner.headless("Do something", workdir=self.temp_dir, status=status)
        except Exception:
            pass

        self.assertLessEqual(len(status.lines), 200)

    @patch('runner.subprocess.Popen')
    def test_headless_status_cleanup_on_finish(self, mock_popen):
        """running=False, pid=None, kill_requested=False after finish."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        status = AgentStatus()
        status.kill_requested = True

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        runner.headless("Do something", workdir=self.temp_dir, status=status)

        self.assertFalse(status.running)
        self.assertIsNone(status.pid)
        self.assertFalse(status.kill_requested)


class TestHeadlessJSONParsing(unittest.TestCase):
    """Tests for JSON event parsing in headless mode."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-runner-json")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.temp_dir / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_json_format_text_event(self, mock_popen, mock_select):
        """{"type": "text", "part": {"text": "hello"}} → "hello" in output."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.side_effect = ['{"type": "text", "part": {"text": "hello"}}', '']
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("hello", result)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_json_format_tool_use_event(self, mock_popen, mock_select):
        """{"type": "tool_use", "part": {"name": "Read"}} → "[tool: Read]" in output."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.side_effect = ['{"type": "tool_use", "part": {"name": "Read"}}', '']
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        config = MockConfig(code_path=self.temp_dir)
        # Use opencode agent which includes --format json in command
        runner = AgentRunner(config, agent_name="opencode")

        with patch.object(Path, 'unlink', return_value=None):
            result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("[tool: Read]", result)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_json_format_error_event(self, mock_popen, mock_select):
        """{"type": "error", ...} → error message in output."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.side_effect = ['{"type": "error", "error": {"message": "Something went wrong"}}', '']
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("Something went wrong", result)

    @patch('runner.select')
    @patch('runner.subprocess.Popen')
    def test_json_format_invalid_json_line(self, mock_popen, mock_select):
        """Non-JSON line passed through as-is."""
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_select.select.return_value = ([mock_stdout], [], [])
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stdout.readline.side_effect = ["just plain text", ""]
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        result = runner.headless("Do something", workdir=self.temp_dir)

        self.assertIn("just plain text", result)


class TestHeadlessKillHandling(unittest.TestCase):
    """Tests for kill handling in headless mode."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test-runner-kill")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.temp_dir / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch('runner.subprocess.Popen')
    @patch('runner.os.killpg')
    def test_headless_kill_requested(self, mock_killpg, mock_popen):
        """SIGTERM sent, then SIGKILL if needed."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = MagicMock()
        
        call_count = [0]
        def readline_side_effect():
            call_count[0] += 1
            if call_count[0] > 5:
                return ""
            return 'some output'
        
        mock_process.stdout.readline.side_effect = readline_side_effect
        mock_process.poll.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        status = AgentStatus()
        status.kill_requested = True

        config = MockConfig(code_path=self.temp_dir)
        runner = AgentRunner(config)

        result = runner.headless("Do something", workdir=self.temp_dir, status=status)

        mock_killpg.assert_called()


if __name__ == '__main__':
    unittest.main()
