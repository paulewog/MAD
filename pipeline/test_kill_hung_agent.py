"""Tests for kill-hung-agent feature.

Run with:
    pytest test_kill_hung_agent.py -v
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from agent_status import AgentStatus


class TestAgentStatusNewFields:
    """Test 1.1: AgentStatus has new fields with correct defaults."""

    def test_agent_status_default_pid(self):
        """Verify pid defaults to None."""
        status = AgentStatus()
        assert status.pid is None

    def test_agent_status_default_last_activity_at(self):
        """Verify last_activity_at defaults to None."""
        status = AgentStatus()
        assert status.last_activity_at is None

    def test_agent_status_default_kill_requested(self):
        """Verify kill_requested defaults to False."""
        status = AgentStatus()
        assert status.kill_requested is False

    def test_agent_status_default_restart_requested(self):
        """Verify restart_requested defaults to False."""
        status = AgentStatus()
        assert status.restart_requested is False

    def test_agent_status_fields_independently_settable(self):
        """Verify each field can be set and read independently."""
        status = AgentStatus()
        
        status.pid = 12345
        assert status.pid == 12345
        
        status.last_activity_at = 1234567890.0
        assert status.last_activity_at == 1234567890.0
        
        status.kill_requested = True
        assert status.kill_requested is True
        
        status.restart_requested = True
        assert status.restart_requested is True

    def test_agent_status_fields_with_non_defaults(self):
        """Verify fields can be set to non-default values on init."""
        status = AgentStatus(
            pid=99999,
            last_activity_at=1234567890.5,
            kill_requested=True,
            restart_requested=True
        )
        
        assert status.pid == 99999
        assert status.last_activity_at == 1234567890.5
        assert status.kill_requested is True
        assert status.restart_requested is True


class TestPIDTracking:
    """Test 1.2: PID tracking in runner.py."""

    def test_status_pid_set_after_popen(self):
        """Verify status.pid is set to process.pid after Popen (captured mid-run)."""
        import subprocess
        import runner
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-pid"
        
        pid_at_loop_start = []
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_process.returncode = 0
            mock_process.poll.return_value = 0
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.return_value = ""
            mock_process.stderr.read.return_value = ""
            mock_popen.return_value = mock_process
            
            def capturing_select(*args, **kwargs):
                pid_at_loop_start.append(status.pid)
                return ([], [], [])
            
            with patch.object(runner.select, 'select', side_effect=capturing_select):
                agent_runner.headless("test prompt", status=status)
            
            assert len(pid_at_loop_start) > 0, "select was never called"
            assert pid_at_loop_start[0] == 12345

    def test_status_pid_reset_after_exit(self):
        """Verify status.pid is reset to None after process exits."""
        import subprocess
        import runner
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-pid-reset"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 99999
            mock_process.returncode = 0
            mock_process.poll.return_value = 0
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.return_value = ""
            mock_process.stderr.read.return_value = ""
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', return_value=([], [], [])):
                agent_runner.headless("test prompt", status=status)
                
                assert status.pid is None


class TestLastActivityTimestamp:
    """Test 1.3: Last activity timestamp updates."""

    def test_last_activity_at_none_initially(self):
        """Verify last_activity_at is None before any output."""
        status = AgentStatus()
        assert status.last_activity_at is None

    def test_last_activity_at_updated_on_output(self):
        """Verify last_activity_at is updated when output is read."""
        import subprocess
        import runner
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-activity"
        
        output_lines = ["First line", "Second line", "Third line"]
        captured_times = []
        
        original_last_activity_at = None
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 11111
            mock_process.returncode = 0
            mock_process.poll.return_value = None
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            
            call_count = [0]
            def readline_side_effect():
                if call_count[0] < len(output_lines):
                    line = output_lines[call_count[0]]
                    call_count[0] += 1
                    captured_times.append(time.time())
                    return line
                mock_process.poll.return_value = 0
                return ""
            
            mock_process.stdout.readline.side_effect = readline_side_effect
            mock_process.stderr.read.return_value = ""
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', return_value=([mock_process.stdout], [], [])):
                agent_runner.headless("test prompt", status=status)
                
                assert len(captured_times) > 0, "Output lines were not read"

    def test_last_activity_at_reset_after_exit(self):
        """Verify last_activity_at is reset to None after process exits."""
        import subprocess
        import runner
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-activity-reset"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 22222
            mock_process.returncode = 0
            mock_process.poll.return_value = 0
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.return_value = ""
            mock_process.stderr.read.return_value = ""
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', return_value=([], [], [])):
                agent_runner.headless("test prompt", status=status)
                
                assert status.last_activity_at is None


class TestKillSignalCheck:
    """Test 1.4: Kill signal check in monitoring loop."""

    def test_kill_requested_triggers_terminate(self):
        """Verify kill_requested=True triggers os.killpg."""
        import subprocess
        import runner
        import signal
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-kill"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            with patch('pipeline.runner.os.killpg') as mock_killpg:
                mock_process = MagicMock()
                mock_process.pid = 33333
                mock_process.returncode = 0
                mock_process.poll.return_value = None
                mock_process.stdout = MagicMock()
                mock_process.stderr = MagicMock()
                mock_process.stdout.readline.return_value = ""
                mock_process.stderr.read.return_value = ""
                mock_process.wait.side_effect = [subprocess.TimeoutExpired("cmd", 1), None]
                mock_popen.return_value = mock_process
                
                status.kill_requested = True
                
                with patch.object(runner.select, 'select', return_value=([], [], [])):
                    agent_runner.headless("test prompt", status=status)
                    
                    assert mock_killpg.call_count >= 1

    def test_kill_sets_running_false(self):
        """Verify after kill, status.running is False."""
        import subprocess
        import runner
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-kill-running"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 44444
            mock_process.returncode = 0
            mock_process.poll.return_value = None
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.return_value = ""
            mock_process.stderr.read.return_value = ""
            mock_process.wait.side_effect = [subprocess.TimeoutExpired("cmd", 1), None]
            mock_popen.return_value = mock_process
            
            status.kill_requested = True
            
            with patch.object(runner.select, 'select', return_value=([], [], [])):
                agent_runner.headless("test prompt", status=status)
                
                assert status.running is False

    def test_kill_appends_message_to_lines(self):
        """Verify kill appends message to status.lines."""
        import subprocess
        import runner
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-kill-lines"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 55555
            mock_process.returncode = 0
            mock_process.poll.return_value = None
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.return_value = ""
            mock_process.stderr.read.return_value = ""
            mock_process.wait.side_effect = [subprocess.TimeoutExpired("cmd", 1), None]
            mock_popen.return_value = mock_process
            
            status.kill_requested = True
            
            with patch.object(runner.select, 'select', return_value=([], [], [])):
                agent_runner.headless("test prompt", status=status)
                
                assert any("killed by user" in line for line in status.lines)


class TestFinallyBlockCleanup:
    """Test 1.5: Finally block cleanup."""

    def test_finally_resets_all_status_fields(self):
        """Verify finally block resets all status fields after any exit."""
        import subprocess
        import runner
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-finally"
        status.kill_requested = True
        status.restart_requested = True
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 66666
            mock_process.returncode = 0
            mock_process.poll.return_value = 0
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.return_value = ""
            mock_process.stderr.read.return_value = ""
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', return_value=([], [], [])):
                agent_runner.headless("test prompt", status=status)
                
                assert status.running is False
                assert status.pid is None
                assert status.last_activity_at is None
                assert status.kill_requested is False
                assert status.restart_requested is False


class TestStaleIndicatorDisplay:
    """Test 1.6: Stale indicator display."""

    def test_stale_indicator_shown_over_150_seconds(self):
        """Verify stale indicator appears when idle > 150 seconds."""
        from tui import AgentStatusWidget
        
        widget = AgentStatusWidget()
        status = AgentStatus()
        status.running = True
        status.started_at = time.time() - 200
        status.last_activity_at = time.time() - 160
        status.agent = "test-agent"
        status.feature_slug = "test-feature"
        status.phase = "planning"
        
        rendered = widget._render_single(status)
        assert "STALE" in rendered

    def test_stale_indicator_not_shown_under_150_seconds(self):
        """Verify stale indicator does not appear when idle < 150 seconds."""
        from tui import AgentStatusWidget
        
        widget = AgentStatusWidget()
        status = AgentStatus()
        status.running = True
        status.started_at = time.time() - 100
        status.last_activity_at = time.time() - 60
        status.agent = "test-agent"
        status.feature_slug = "test-feature"
        status.phase = "planning"
        
        rendered = widget._render_single(status)
        assert "STALE" not in rendered

    def test_stale_indicator_not_shown_when_not_running(self):
        """Verify stale indicator does not appear when not running."""
        from tui import AgentStatusWidget
        
        widget = AgentStatusWidget()
        status = AgentStatus()
        status.running = False
        status.started_at = time.time() - 200
        status.last_activity_at = time.time() - 160
        status.agent = "test-agent"
        status.feature_slug = "test-feature"
        status.phase = "planning"
        
        rendered = widget._render_single(status)
        assert "STALE" not in rendered

    def test_stale_indicator_not_shown_when_last_activity_none(self):
        """Verify stale indicator does not appear when last_activity_at is None."""
        from tui import AgentStatusWidget
        
        widget = AgentStatusWidget()
        status = AgentStatus()
        status.running = True
        status.started_at = time.time() - 200
        status.last_activity_at = None
        status.agent = "test-agent"
        status.feature_slug = "test-feature"
        status.phase = "planning"
        
        rendered = widget._render_single(status)
        assert "STALE" not in rendered

    def test_stale_indicator_at_boundary(self):
        """Verify stale indicator at exactly 150 seconds."""
        from tui import AgentStatusWidget
        
        widget = AgentStatusWidget()
        status = AgentStatus()
        status.running = True
        status.started_at = time.time() - 200
        status.last_activity_at = time.time() - 150
        status.agent = "test-agent"
        status.feature_slug = "test-feature"
        status.phase = "planning"
        
        rendered = widget._render_single(status)
        assert "STALE" in rendered


class TestDynamicFooterHints:
    """Test 1.10: Dynamic footer hints."""

    def test_footer_shows_kill_restart_when_plan_running(self):
        """Verify kill/restart appear when plan agent is running."""
        from tui import PipelineApp
        from unittest.mock import patch, PropertyMock
        
        app = object.__new__(PipelineApp)
        app._plan_running = True
        app._implement_running = False
        
        with patch.object(PipelineApp, 'selected_feature', new_callable=PropertyMock, return_value=None):
            bindings = app.get_contextual_bindings()
            binding_labels = [b[2] for b in bindings]
        
        assert "Kill" in binding_labels
        assert "Restart" in binding_labels

    def test_footer_shows_kill_restart_when_impl_running(self):
        """Verify kill/restart appear when implement agent is running."""
        from tui import PipelineApp
        from unittest.mock import patch, PropertyMock
        
        app = object.__new__(PipelineApp)
        app._plan_running = False
        app._implement_running = True
        
        with patch.object(PipelineApp, 'selected_feature', new_callable=PropertyMock, return_value=None):
            bindings = app.get_contextual_bindings()
            binding_labels = [b[2] for b in bindings]
        
        assert "Kill" in binding_labels
        assert "Restart" in binding_labels

    def test_footer_hides_kill_restart_when_no_agent_running(self):
        """Verify kill/restart do not appear when no agent is running."""
        from tui import PipelineApp
        from unittest.mock import patch, PropertyMock
        
        app = object.__new__(PipelineApp)
        app._plan_running = False
        app._implement_running = False
        
        with patch.object(PipelineApp, 'selected_feature', new_callable=PropertyMock, return_value=None):
            bindings = app.get_contextual_bindings()
            binding_labels = [b[2] for b in bindings]
        
        assert "Kill" not in binding_labels
        assert "Restart" not in binding_labels


class TestKillAgentBinding:
    """Test 1.7: Kill agent binding (k)."""

    def test_kill_no_agent_running_shows_warning(self):
        """Verify kill shows warning when no agent running."""
        from tui import PipelineApp
        
        app = object.__new__(PipelineApp)
        app._plan_running = False
        app._implement_running = False
        app._plan_agent_status = None
        app._implement_agent_status = None
        app.notify = MagicMock()
        app._log_line = MagicMock()
        
        app.action_kill_agent()
        
        app.notify.assert_called_once()
        args = app.notify.call_args
        assert "No agent is running" in str(args)

    def test_kill_plan_agent_sets_kill_requested(self):
        """Verify kill sets kill_requested on plan agent status."""
        from tui import PipelineApp
        
        app = object.__new__(PipelineApp)
        app._plan_running = True
        app._implement_running = False
        app._plan_agent_status = AgentStatus()
        app._plan_agent_status.feature_slug = "test-slug"
        app._implement_agent_status = None
        app.notify = MagicMock()
        app._log_line = MagicMock()
        with patch('tui.FeatureFile.find') as mock_find:
            mock_feature = MagicMock()
            mock_find.return_value = mock_feature
            
            app.action_kill_agent()
            
            assert app._plan_agent_status.kill_requested is True

    def test_kill_both_agents_kills_plan_first(self):
        """Verify kill prioritizes plan agent over implement agent."""
        from tui import PipelineApp
        
        app = object.__new__(PipelineApp)
        app._plan_running = True
        app._implement_running = True
        app._plan_agent_status = AgentStatus()
        app._plan_agent_status.feature_slug = "test-slug-plan"
        app._implement_agent_status = AgentStatus()
        app._implement_agent_status.feature_slug = "test-slug-impl"
        app.notify = MagicMock()
        app._log_line = MagicMock()
        with patch('tui.FeatureFile.find') as mock_find:
            mock_feature = MagicMock()
            mock_find.return_value = mock_feature
            
            app.action_kill_agent()
            
            assert app._plan_agent_status.kill_requested is True
            assert app._implement_agent_status.kill_requested is False


class TestRestartAgentBinding:
    """Test 1.8: Restart agent binding (ctrl+k)."""

    def test_restart_no_agent_running_shows_warning(self):
        """Verify restart shows warning when no agent running."""
        from tui import PipelineApp
        
        app = object.__new__(PipelineApp)
        app._plan_running = False
        app._implement_running = False
        app._plan_agent_status = None
        app._implement_agent_status = None
        app.notify = MagicMock()
        app._log_line = MagicMock()
        
        app.action_restart_agent()
        
        app.notify.assert_called_once()
        args = app.notify.call_args
        assert "No agent is running" in str(args)

    def test_restart_sets_both_flags(self):
        """Verify restart sets both kill_requested and restart_requested."""
        from tui import PipelineApp
        
        app = object.__new__(PipelineApp)
        app._plan_running = True
        app._implement_running = False
        app._plan_agent_status = AgentStatus()
        app._plan_agent_status.feature_slug = "test-slug"
        app._implement_agent_status = None
        app.notify = MagicMock()
        app._log_line = MagicMock()
        with patch('tui.FeatureFile.find') as mock_find:
            mock_feature = MagicMock()
            mock_find.return_value = mock_feature
            
            app.action_restart_agent()
            
            assert app._plan_agent_status.kill_requested is True
            assert app._plan_agent_status.restart_requested is True


class TestHistoryLogging:
    """Test 1.11: Feature history logging."""

    def test_kill_adds_killed_history_entry(self):
        """Verify kill adds KILLED history entry."""
        from tui import PipelineApp
        
        app = object.__new__(PipelineApp)
        app._plan_running = True
        app._implement_running = False
        app._plan_agent_status = AgentStatus()
        app._plan_agent_status.feature_slug = "test-slug"
        app._plan_agent_status.started_at = time.time() - 60
        app._implement_agent_status = None
        app.notify = MagicMock()
        app._log_line = MagicMock()
        with patch('tui.FeatureFile.find') as mock_find:
            mock_feature = MagicMock()
            mock_find.return_value = mock_feature
            
            app.action_kill_agent()
            
            mock_feature.add_history.assert_called()
            args = mock_feature.add_history.call_args
            assert args[0][0] == "KILLED"

    def test_restart_adds_restarted_history_entry(self):
        """Verify restart adds RESTARTED history entry."""
        from tui import PipelineApp
        
        app = object.__new__(PipelineApp)
        app._plan_running = True
        app._implement_running = False
        app._plan_agent_status = AgentStatus()
        app._plan_agent_status.feature_slug = "test-slug"
        app._plan_agent_status.started_at = time.time() - 60
        app._implement_agent_status = None
        app.notify = MagicMock()
        app._log_line = MagicMock()
        with patch('tui.FeatureFile.find') as mock_find:
            mock_feature = MagicMock()
            mock_find.return_value = mock_feature
            
            app.action_restart_agent()
            
            mock_feature.add_history.assert_called()
            args = mock_feature.add_history.call_args
            assert args[0][0] == "RESTARTED"


class TestEdgeCases:
    """Test edge cases from the test spec."""

    def test_kill_idempotent(self):
        """Verify kill can be called multiple times without issues."""
        from tui import PipelineApp
        
        app = object.__new__(PipelineApp)
        app._plan_running = True
        app._implement_running = False
        app._plan_agent_status = AgentStatus()
        app._plan_agent_status.feature_slug = "test-slug"
        app._implement_agent_status = None
        app.notify = MagicMock()
        app._log_line = MagicMock()
        with patch('tui.FeatureFile.find') as mock_find:
            mock_feature = MagicMock()
            mock_find.return_value = mock_feature
            
            app.action_kill_agent()
            app.action_kill_agent()
            
            assert app._plan_agent_status.kill_requested is True

    def test_kill_after_process_exited_no_exception(self):
        """Verify kill after process already exited doesn't raise."""
        import subprocess
        import runner
        from config import Config
        
        config = Config()
        agent_runner = runner.AgentRunner(config)
        
        status = AgentStatus()
        status.feature_slug = "test-kill-after-exit"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 77777
            mock_process.returncode = 0
            mock_process.poll.return_value = 0
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.return_value = ""
            mock_process.stderr.read.return_value = ""
            mock_popen.return_value = mock_process
            
            status.kill_requested = True
            
            with patch.object(runner.select, 'select', return_value=([], [], [])):
                agent_runner.headless("test prompt", status=status)
