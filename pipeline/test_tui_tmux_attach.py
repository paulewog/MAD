"""Tests for TUI tmux attach functionality."""

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import pipeline as pipeline_module
from lock import LockInfo, PipelineLock
from config import Config


class MockConfig:
    """Mock Config for testing."""
    def __init__(self, mad_dir: Path, code_path: Path = None):
        self._mad_dir = mad_dir
        self._code_path = code_path
    
    @property
    def mad_dir(self) -> Path:
        return self._mad_dir
    
    @property
    def code_path(self) -> Path:
        return self._code_path


class TestTuiTmuxAttach(unittest.TestCase):
    """Test cases for TUI tmux attach feature."""
    
    def setUp(self):
        """Create a temp directory for each test."""
        self.temp_dir = Path("/tmp/test-tui-tmux")
        self.temp_dir.mkdir(exist_ok=True)
        self.project_dir = Path("/tmp/test-project")
        self.project_dir.mkdir(exist_ok=True)
        
        self.mock_config = MockConfig(self.temp_dir, self.project_dir)
        
        self.mock_lock = MagicMock(spec=PipelineLock)
        self.mock_lock_info = LockInfo(
            pid=12345,
            hostname="testhost",
            timestamp=datetime.now().isoformat(),
            username="testuser"
        )
    
    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
        if self.project_dir.exists():
            shutil.rmtree(self.project_dir)
    
    @patch('tui.PipelineApp')
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    def test_1_1_no_lock_normal_launch(self, mock_pipeline_lock_cls, mock_config_cls, mock_app_cls):
        """Test 1.1: No lock exists → normal TUI launch."""
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = True
        mock_pipeline_lock_cls.return_value = mock_lock
        
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        
        from tui import run_tui
        run_tui(force=False)
        
        mock_lock.acquire.assert_called_once_with('tui', force=False)
    
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    def test_1_2_stale_lock_auto_clean(self, mock_pipeline_lock_cls, mock_config_cls):
        """Test 1.2: Stale lock → auto-clean and launch normally."""
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = True
        mock_pipeline_lock_cls.return_value = mock_lock
        
        from tui import _acquire_lock
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                lock = _acquire_lock(force=False)
        
        self.assertEqual(lock, mock_lock)
    
    @patch.dict(os.environ, {}, clear=False)
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    @patch('tui.subprocess.run')
    @patch('tui.shutil.which')
    def test_1_3_valid_lock_tmux_session_attach(
        self, mock_which, mock_subprocess, mock_pipeline_lock_cls, mock_config_cls
    ):
        """Test 1.3: Valid lock + tmux session exists + not inside tmux → attach-session."""
        if 'TMUX' in os.environ:
            del os.environ['TMUX']
        
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = False
        mock_lock.check.return_value = self.mock_lock_info
        mock_pipeline_lock_cls.return_value = mock_lock
        
        mock_which.return_value = "/usr/bin/tmux"
        mock_subprocess.return_value = MagicMock(returncode=0)
        
        from service import tmux_session_name
        expected_session = tmux_session_name(self.project_dir)
        
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                with self.assertRaises(SystemExit) as ctx:
                    from tui import _acquire_lock
                    _acquire_lock(force=False)
        
        self.assertEqual(ctx.exception.code, 0)
        
        mock_subprocess.assert_any_call(
            ["tmux", "has-session", "-t", expected_session],
            capture_output=True
        )
        
        attach_call = mock_subprocess.call_args_list[-1]
        self.assertEqual(attach_call[0][0], ["tmux", "attach-session", "-t", expected_session])
    
    @patch.dict(os.environ, {'TMUX': '/tmp/tmux-12345'}, clear=False)
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    @patch('tui.subprocess.run')
    @patch('tui.shutil.which')
    def test_1_4_valid_lock_tmux_session_switch_client(
        self, mock_which, mock_subprocess, mock_pipeline_lock_cls, mock_config_cls
    ):
        """Test 1.4: Valid lock + tmux session exists + inside tmux → switch-client."""
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = False
        mock_lock.check.return_value = self.mock_lock_info
        mock_pipeline_lock_cls.return_value = mock_lock
        
        mock_which.return_value = "/usr/bin/tmux"
        mock_subprocess.return_value = MagicMock(returncode=0)
        
        from service import tmux_session_name
        expected_session = tmux_session_name(self.project_dir)
        
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                with self.assertRaises(SystemExit) as ctx:
                    from tui import _acquire_lock
                    _acquire_lock(force=False)
        
        self.assertEqual(ctx.exception.code, 0)
        
        attach_call = mock_subprocess.call_args_list[-1]
        self.assertEqual(attach_call[0][0], ["tmux", "switch-client", "-t", expected_session])
    
    @patch.dict(os.environ, {}, clear=False)
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    @patch('tui.subprocess.run')
    @patch('tui.shutil.which')
    def test_1_5_valid_lock_no_tmux_session_error(
        self, mock_which, mock_subprocess, mock_pipeline_lock_cls, mock_config_cls
    ):
        """Test 1.5: Valid lock + no tmux session → error with lock details."""
        if 'TMUX' in os.environ:
            del os.environ['TMUX']
        
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = False
        mock_lock.check.return_value = self.mock_lock_info
        mock_pipeline_lock_cls.return_value = mock_lock
        
        mock_which.return_value = "/usr/bin/tmux"
        mock_subprocess.return_value = MagicMock(returncode=1)
        
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                with self.assertRaises(SystemExit) as ctx:
                    from tui import _acquire_lock
                    _acquire_lock(force=False)
        
        self.assertEqual(ctx.exception.code, 1)
    
    @patch.dict(os.environ, {}, clear=False)
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    @patch('tui.shutil.which')
    def test_1_6_valid_lock_tmux_not_installed(
        self, mock_which, mock_pipeline_lock_cls, mock_config_cls
    ):
        """Test 1.6: Valid lock + tmux not installed → fallback error."""
        if 'TMUX' in os.environ:
            del os.environ['TMUX']
        
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = False
        mock_lock.check.return_value = self.mock_lock_info
        mock_pipeline_lock_cls.return_value = mock_lock
        
        mock_which.return_value = None
        
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                with self.assertRaises(SystemExit) as ctx:
                    from tui import _acquire_lock
                    _acquire_lock(force=False)
        
        self.assertEqual(ctx.exception.code, 1)
    
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    def test_1_7_force_flag_bypasses_lock(self, mock_pipeline_lock_cls, mock_config_cls):
        """Test 1.7: --force flag → lock.acquire called with force=True."""
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = True
        mock_pipeline_lock_cls.return_value = mock_lock
        
        from tui import _acquire_lock
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                lock = _acquire_lock(force=True)
        
        mock_lock.acquire.assert_called_once_with('tui', force=True)
    
    def test_1_8_cli_passes_force_flag(self):
        """Test 1.8: CLI passes force flag through to run_tui."""
        with patch('tui.run_tui') as mock_run_tui:
            from click.testing import CliRunner
            
            runner = CliRunner()
            result = runner.invoke(pipeline_module.cli, ['tui', '--force'])
            
            mock_run_tui.assert_called_once_with(force=True)
    
    @patch.dict(os.environ, {}, clear=False)
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    @patch('tui.subprocess.run')
    @patch('tui.shutil.which')
    def test_1_9_session_name_from_service(
        self, mock_which, mock_subprocess, mock_pipeline_lock_cls, mock_config_cls
    ):
        """Test 1.9: Session name comes from service.tmux_session_name()."""
        if 'TMUX' in os.environ:
            del os.environ['TMUX']
        
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = False
        mock_lock.check.return_value = self.mock_lock_info
        mock_pipeline_lock_cls.return_value = mock_lock
        
        mock_which.return_value = "/usr/bin/tmux"
        
        from service import tmux_session_name
        expected_session = tmux_session_name(self.project_dir)
        
        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0]
            if cmd[1] == 'has-session':
                return MagicMock(returncode=0)
            elif cmd[1] == 'attach-session':
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)
        
        mock_subprocess.side_effect = subprocess_side_effect
        
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                with patch('service.tmux_session_name', return_value=expected_session) as mock_session_name:
                    from tui import _acquire_lock
                    try:
                        _acquire_lock(force=False)
                    except SystemExit:
                        pass
                    
                    mock_session_name.assert_called_once()
    
    @patch.dict(os.environ, {}, clear=False)
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    @patch('tui.subprocess.run')
    @patch('tui.shutil.which')
    def test_1_10_tmux_attach_fails_error(
        self, mock_which, mock_subprocess, mock_pipeline_lock_cls, mock_config_cls
    ):
        """Test 1.10: Tmux attach fails → clear error message."""
        if 'TMUX' in os.environ:
            del os.environ['TMUX']
        
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = False
        mock_lock.check.return_value = self.mock_lock_info
        mock_pipeline_lock_cls.return_value = mock_lock
        
        mock_which.return_value = "/usr/bin/tmux"
        
        from service import tmux_session_name
        expected_session = tmux_session_name(self.project_dir)
        
        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0]
            if cmd[1] == 'has-session':
                return MagicMock(returncode=0)
            elif cmd[1] == 'attach-session':
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)
        
        mock_subprocess.side_effect = subprocess_side_effect
        
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                with self.assertRaises(SystemExit) as ctx:
                    from tui import _acquire_lock
                    _acquire_lock(force=False)
        
        self.assertEqual(ctx.exception.code, 1)
        
        call_args = [call for call in mock_subprocess.call_args_list]
        attach_call = call_args[-1]
        self.assertIn("attach-session", attach_call[0][0])
    
    @patch.dict(os.environ, {'TMUX': ''}, clear=False)
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    @patch('tui.subprocess.run')
    @patch('tui.shutil.which')
    def test_2_3_tmux_empty_uses_attach(
        self, mock_which, mock_subprocess, mock_pipeline_lock_cls, mock_config_cls
    ):
        """Test 2.3: TMUX="" (empty) should use attach-session not switch-client."""
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = False
        mock_lock.check.return_value = self.mock_lock_info
        mock_pipeline_lock_cls.return_value = mock_lock
        
        mock_which.return_value = "/usr/bin/tmux"
        mock_subprocess.return_value = MagicMock(returncode=0)
        
        from service import tmux_session_name
        expected_session = tmux_session_name(self.project_dir)
        
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                with self.assertRaises(SystemExit) as ctx:
                    from tui import _acquire_lock
                    _acquire_lock(force=False)
        
        self.assertEqual(ctx.exception.code, 0)
        
        attach_call = mock_subprocess.call_args_list[-1]
        self.assertEqual(attach_call[0][0], ["tmux", "attach-session", "-t", expected_session])
    
    @patch('tui.Config')
    @patch('tui.PipelineLock')
    def test_2_7_force_with_no_lock_works(self, mock_pipeline_lock_cls, mock_config_cls):
        """Test 2.7: --force with no existing lock works normally."""
        mock_config = MockConfig(self.temp_dir, self.project_dir)
        mock_config_cls.return_value = mock_config
        
        mock_lock = MagicMock(spec=PipelineLock)
        mock_lock.acquire.return_value = True
        mock_pipeline_lock_cls.return_value = mock_lock
        
        from tui import _acquire_lock
        with patch('tui.Config', return_value=mock_config):
            with patch('tui.PipelineLock', return_value=mock_lock):
                lock = _acquire_lock(force=True)
        
        mock_lock.acquire.assert_called_once_with('tui', force=True)
        self.assertEqual(lock, mock_lock)


if __name__ == '__main__':
    unittest.main()
