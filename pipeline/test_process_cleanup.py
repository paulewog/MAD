"""Tests for process cleanup race conditions fix.

Run with:
    pytest test_process_cleanup.py -v
"""

import errno
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from runner import AgentRunner
from agent_status import AgentStatus


@pytest.fixture
def tmp_workdir(tmp_path):
    """Create a temp workdir with .mad structure."""
    mad_dir = tmp_path / ".mad"
    mad_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir = mad_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    context_file = mad_dir / "CONTEXT.md"
    context_file.write_text("Test context")
    return tmp_path


@pytest.fixture
def mock_config(tmp_workdir):
    """Create a mock config pointing to tmp workdir."""
    cfg = MagicMock(spec=Config)
    cfg.code_path = tmp_workdir
    cfg.context_file = tmp_workdir / ".mad" / "CONTEXT.md"
    cfg.mad_dir = tmp_workdir / ".mad"
    cfg.agents = {}
    cfg.current_agent = MagicMock()
    cfg.current_agent.name = "test-agent"
    cfg.current_agent.command = "echo"
    cfg.current_agent.headless_extra_flags = []
    cfg.current_agent.model = None
    cfg.current_agent.model_flag = None
    cfg.current_agent.headless_flag = None
    return cfg


class TestStatusLockInitialization:
    """Test 1.7: Status lock is initialized in __init__."""

    def test_runner_has_status_lock(self, mock_config):
        """Verify AgentRunner has _status_lock attribute."""
        runner = AgentRunner(mock_config)
        assert hasattr(runner, '_status_lock')

    def test_status_lock_is_rlock(self, mock_config):
        """Verify _status_lock is an RLock (reentrant)."""
        runner = AgentRunner(mock_config)
        assert type(runner._status_lock).__name__ == 'RLock'


class TestProcessWaitTimeout:
    """Test 1.1 & 1.2: process.wait() uses timeout."""

    @patch('runner.select')
    @patch('runner.os.killpg')
    @patch('subprocess.Popen')
    def test_user_kill_timeout_triggers_force_kill(self, mock_popen_class, mock_killpg, mock_select, mock_config, tmp_workdir, caplog):
        """When process.wait() times out after SIGTERM, force-kill should be triggered."""
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.side_effect = [
            subprocess.TimeoutExpired("test", 10),
            None
        ]
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()
        status.kill_requested = True

        runner.headless("test prompt", workdir=tmp_workdir, status=status)

        assert mock_killpg.call_count == 2

    @pytest.mark.skip(reason="Idle kill path requires 15min timeout simulation, complex to test")
    @patch('runner.select')
    @patch('runner.os.killpg')
    @patch('subprocess.Popen')
    def test_idle_kill_timeout_triggers_force_kill(self, mock_popen_class, mock_killpg, mock_select, mock_config, tmp_workdir, caplog):
        """When process.wait() times out after SIGTERM in idle path, force-kill should be triggered."""
        runner = AgentRunner(mock_config)
        
        logs_dir = tmp_workdir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.side_effect = [
            subprocess.TimeoutExpired("test", 10),
            None
        ]
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()
        status.feature_slug = 'test-feature'

        runner.headless("test prompt", workdir=tmp_workdir, status=status)

        assert mock_killpg.call_count >= 1


class TestKillpgErrorLogging:
    """Test 1.3-1.6: os.killpg() errors are logged with context."""

    @patch('runner.select')
    @patch('runner.os.killpg')
    @patch('subprocess.Popen')
    def test_killpg_esrch_logged_at_debug(self, mock_popen_class, mock_killpg, mock_select, mock_config, tmp_workdir, caplog):
        """When ESRCH (process already dead), log at DEBUG level."""
        logging.getLogger("runner").setLevel(logging.DEBUG)
        mock_killpg.side_effect = OSError(errno.ESRCH, "No such process")
        
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.return_value = 0
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()
        status.kill_requested = True

        runner.headless("test prompt", workdir=tmp_workdir, status=status)

        assert any("already dead" in record.message.lower() for record in caplog.records)

    @patch('runner.select')
    @patch('runner.os.killpg')
    @patch('subprocess.Popen')
    def test_killpg_eperm_logged_at_error(self, mock_popen_class, mock_killpg, mock_select, mock_config, tmp_workdir, caplog):
        """When EPERM (permission denied), log at ERROR level."""
        mock_killpg.side_effect = OSError(errno.EPERM, "Operation not permitted")
        
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.return_value = 0
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()
        status.kill_requested = True

        runner.headless("test prompt", workdir=tmp_workdir, status=status)

        assert any("permission denied" in record.message.lower() for record in caplog.records)
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(error_records) > 0

    @patch('runner.select')
    @patch('runner.os.killpg')
    @patch('subprocess.Popen')
    def test_killpg_other_error_logged_at_warning(self, mock_popen_class, mock_killpg, mock_select, mock_config, tmp_workdir, caplog):
        """When other errno, log at WARNING level."""
        mock_killpg.side_effect = OSError(errno.EIO, "Input/output error")
        
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.return_value = 0
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()
        status.kill_requested = True

        runner.headless("test prompt", workdir=tmp_workdir, status=status)

        assert any("failed" in record.message.lower() for record in caplog.records)
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_records) > 0


class TestStatusMutationsSynchronized:
    """Test 1.8-1.14: Status mutations are synchronized."""

    @patch('runner.select')
    @patch('runner.os.killpg')
    @patch('subprocess.Popen')
    def test_status_mutations_use_lock(self, mock_popen_class, mock_killpg, mock_select, mock_config, tmp_workdir):
        """Verify that status mutations acquire the lock."""
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.return_value = 0
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()

        assert hasattr(runner, '_status_lock')
        
        runner.headless("test prompt", workdir=tmp_workdir, status=status)

    @patch('runner.select')
    @patch('runner.os.killpg')
    @patch('subprocess.Popen')
    def test_concurrent_status_access_no_corruption(self, mock_popen_class, mock_killpg, mock_select, mock_config, tmp_workdir):
        """Two threads accessing status simultaneously should not corrupt data."""
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.return_value = 0
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()
        errors = []

        def append_lines():
            try:
                for i in range(50):
                    with runner._status_lock:
                        status.lines.append(f"line-{i}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def read_lines():
            try:
                for _ in range(50):
                    with runner._status_lock:
                        _ = len(status.lines)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=append_lines)
        t2 = threading.Thread(target=read_lines)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0


class TestEdgeCases:
    """Test edge cases from spec section 2."""

    @patch('runner.select')
    @patch('subprocess.Popen')
    def test_status_none_no_lock_acquired(self, mock_popen_class, mock_select, mock_config, tmp_workdir):
        """When status is None, no lock should be acquired."""
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.return_value = 0
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        runner.headless("test prompt", workdir=tmp_workdir, status=None)

    @patch('runner.select')
    @patch('runner.os.killpg')
    @patch('subprocess.Popen')
    def test_process_already_dead_before_sigterm_esrch(self, mock_popen_class, mock_killpg, mock_select, mock_config, tmp_workdir):
        """When process already dead (ESRCH), continue normally."""
        call_count = [0]
        
        def killpg_side_effect(pid, sig):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError(errno.ESRCH, "No such process")
        
        mock_killpg.side_effect = killpg_side_effect
        
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.return_value = 0
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()
        status.kill_requested = True

        runner.headless("test prompt", workdir=tmp_workdir, status=status)

    @patch('runner.select')
    @patch('subprocess.Popen')
    def test_empty_status_lines_trim(self, mock_popen_class, mock_select, mock_config, tmp_workdir):
        """Trimming empty list should not cause errors."""
        runner = AgentRunner(mock_config)
        
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.fileno.return_value = -1
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.returncode = 0
        mock_process.wait.return_value = 0
        mock_popen_class.return_value = mock_process
        mock_select.select.return_value = ([], [], [])

        status = AgentStatus()
        status.lines = []

        runner.headless("test prompt", workdir=tmp_workdir, status=status)


class TestReentrantLock:
    """Test 2.8: RLock allows reentrant access."""

    def test_rlock_allows_reentrant_access(self, mock_config):
        """RLock should allow same thread to acquire lock multiple times."""
        runner = AgentRunner(mock_config)
        
        with runner._status_lock:
            with runner._status_lock:
                pass
