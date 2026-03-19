"""Tests for service module - systemd user service management for MAD TUI instances."""

import os
import sys
import unittest
import subprocess
import tempfile
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(__file__))

from service import (
    service_name_for_dir,
    tmux_session_name,
    generate_unit_file,
    _find_pipeline_executable,
    install_service,
    uninstall_service,
    service_status,
    restart_service,
)


class TestServiceNameDerivation(unittest.TestCase):
    """Tests for service_name_for_dir() function."""

    def test_determinism(self):
        """Same path always yields the same service name."""
        path = Path("/home/user/my-project")
        name1 = service_name_for_dir(path)
        name2 = service_name_for_dir(path)
        self.assertEqual(name1, name2)

    def test_different_paths_different_names(self):
        """Different paths yield different service names."""
        path1 = Path("/home/user/project1")
        path2 = Path("/home/user/project2")
        name1 = service_name_for_dir(path1)
        name2 = service_name_for_dir(path2)
        self.assertNotEqual(name1, name2)

    def test_service_name_pattern(self):
        """Service name matches pattern mad-tui-[a-f0-9]{12}."""
        path = Path("/home/user/my-project")
        name = service_name_for_dir(path)
        self.assertTrue(name.startswith("mad-tui-"))
        hash_part = name.replace("mad-tui-", "")
        self.assertEqual(len(hash_part), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in hash_part))

    def test_special_characters_in_path(self):
        """Path with special characters produces valid service name."""
        path = Path("/home/user/My Project!")
        name = service_name_for_dir(path)
        self.assertTrue(name.startswith("mad-tui-"))

    def test_very_long_path(self):
        """Very long absolute path produces valid fixed-length name."""
        long_path = Path("/" + "/".join(["verylongdirectoryname"] * 50))
        name = service_name_for_dir(long_path)
        self.assertTrue(name.startswith("mad-tui-"))
        self.assertEqual(len(name), 20)  # mad-tui- + 12 hex chars


class TestTmuxSessionName(unittest.TestCase):
    """Tests for tmux_session_name() function."""

    def test_basic_name(self):
        """Basic project directory produces expected session name."""
        path = Path("/home/user/my-project")
        name = tmux_session_name(path)
        self.assertTrue(name.startswith("mad-my-project-"))

    def test_sanitization(self):
        """Non-alphanumeric characters are replaced with hyphens."""
        path = Path("/home/user/My Project!")
        name = tmux_session_name(path)
        self.assertTrue(name.startswith("mad-"))
        self.assertFalse(any(c.isalnum() is False and c != '-' for c in name))

    def test_lowercase(self):
        """Session name is lowercase."""
        path = Path("/home/user/MyProject")
        name = tmux_session_name(path)
        self.assertEqual(name, name.lower())

    def test_max_length(self):
        """Session name is max 32 characters."""
        path = Path("/home/user/verylongprojectname12345678901234567890")
        name = tmux_session_name(path)
        self.assertLessEqual(len(name), 32)

    def test_trailing_hyphens_stripped(self):
        """Trailing hyphens are stripped before appending hash."""
        path = Path("/home/user/My Project!")
        name = tmux_session_name(path)
        # After sanitization: "my-project-" then hash appended
        # Should NOT have double hyphens like "mad-my-project--"
        self.assertFalse(name.endswith("--"))

    def test_hash_appended(self):
        """Hash is appended for uniqueness."""
        path1 = Path("/home/user/project-a")
        path2 = Path("/home/user/project-b")
        name1 = tmux_session_name(path1)
        name2 = tmux_session_name(path2)
        self.assertNotEqual(name1, name2)


class TestGenerateUnitFile(unittest.TestCase):
    """Tests for generate_unit_file() function."""

    def test_sections_present(self):
        """Unit file contains [Unit], [Service], [Install] sections."""
        path = Path("/home/user/my-project")
        executable = Path("/usr/bin/pipeline")
        content = generate_unit_file(path, executable)
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)

    def test_working_directory(self):
        """WorkingDirectory matches provided project path."""
        path = Path("/home/user/my-project")
        executable = Path("/usr/bin/pipeline")
        content = generate_unit_file(path, executable)
        self.assertIn("WorkingDirectory=/home/user/my-project", content)

    def test_exec_start(self):
        """ExecStart references pipeline executable and tmux session."""
        path = Path("/home/user/my-project")
        executable = Path("/usr/bin/pipeline")
        content = generate_unit_file(path, executable)
        self.assertIn("ExecStart=", content)
        self.assertIn("/usr/bin/tmux", content)

    def test_type_forking(self):
        """Type is set to forking."""
        path = Path("/home/user/my-project")
        executable = Path("/usr/bin/pipeline")
        content = generate_unit_file(path, executable)
        self.assertIn("Type=forking", content)

    def test_restart_always(self):
        """Restart is set to always."""
        path = Path("/home/user/my-project")
        executable = Path("/usr/bin/pipeline")
        content = generate_unit_file(path, executable)
        self.assertIn("Restart=always", content)

    def test_restart_sec(self):
        """RestartSec is set."""
        path = Path("/home/user/my-project")
        executable = Path("/usr/bin/pipeline")
        content = generate_unit_file(path, executable)
        self.assertIn("RestartSec=", content)


class TestFindPipelineExecutable(unittest.TestCase):
    """Tests for _find_pipeline_executable() function."""

    @patch("service.shutil.which")
    def test_finds_on_path(self, mock_which):
        """Returns path when pipeline is on PATH."""
        mock_which.return_value = "/usr/local/bin/pipeline"
        result = _find_pipeline_executable()
        self.assertEqual(result, Path("/usr/local/bin/pipeline"))

    @patch("service.shutil.which")
    @patch("service.Path.exists")
    def test_fallback_to_pipeline_py(self, mock_exists, mock_which):
        """Falls back to pipeline.py when not on PATH."""
        mock_which.return_value = None
        mock_exists.return_value = True
        with patch("service.sys") as mock_sys:
            mock_sys.executable = "/usr/bin/python3"
            result = _find_pipeline_executable()
            self.assertIsNotNone(result)
            result_str = str(result)
            self.assertIn("/usr/bin/python3", result_str)
            self.assertIn("pipeline.py", result_str)


class TestInstallService(unittest.TestCase):
    """Tests for install_service() function."""

    @patch("service.shutil.which")
    def test_tmux_missing(self, mock_which):
        """Fails with clear error if tmux is not installed."""
        mock_which.return_value = None
        with patch("service._find_pipeline_executable") as mock_find:
            mock_find.return_value = Path("/usr/bin/pipeline")
            with self.assertRaises(SystemExit) as ctx:
                install_service(Path("/tmp/test-project"))
            self.assertEqual(ctx.exception.code, 1)

    @patch("service.shutil.which")
    @patch("service._find_pipeline_executable")
    def test_systemctl_missing(self, mock_find, mock_which):
        """Fails with clear error if systemctl is not available."""
        def which_side_effect(cmd):
            if cmd == "tmux":
                return "/usr/bin/tmux"
            return None
        mock_which.side_effect = which_side_effect
        mock_find.return_value = Path("/usr/bin/pipeline")
        with self.assertRaises(SystemExit) as ctx:
            install_service(Path("/tmp/test-project"))
        self.assertEqual(ctx.exception.code, 1)

    @patch("service.shutil.which")
    @patch("service._find_pipeline_executable")
    @patch("service.subprocess.run")
    @patch("service.Path.mkdir")
    @patch("service.Path.write_text")
    @patch("service.Path.home")
    def test_install_success(
        self, mock_home, mock_write, mock_mkdir, mock_run, mock_find, mock_which
    ):
        """Successfully installs service with correct systemctl calls."""
        mock_which.return_value = "/usr/bin/tmux"
        mock_find.return_value = Path("/usr/bin/pipeline")
        mock_home.return_value = Path("/home/testuser")
        
        install_service(Path("/home/testuser/my-project"))
        
        # Verify daemon-reload, enable, start are called
        calls = mock_run.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn("daemon-reload", str(calls[0]))
        self.assertIn("enable", str(calls[1]))
        self.assertIn("start", str(calls[2]))

    @patch("service.shutil.which")
    @patch("service._find_pipeline_executable")
    @patch("service.subprocess.run")
    @patch("service.Path.mkdir")
    @patch("service.Path.write_text")
    @patch("service.Path.home")
    def test_subprocess_error_handling(
        self, mock_home, mock_write, mock_mkdir, mock_run, mock_find, mock_which
    ):
        """Handles subprocess failures gracefully."""
        mock_which.return_value = "/usr/bin/tmux"
        mock_find.return_value = Path("/usr/bin/pipeline")
        mock_home.return_value = Path("/home/testuser")
        
        # Make subprocess.run raise CalledProcessError
        mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl")
        
        # Should raise SystemExit when subprocess fails
        with self.assertRaises(SystemExit) as ctx:
            install_service(Path("/home/testuser/my-project"))
        self.assertEqual(ctx.exception.code, 1)


class TestUninstallService(unittest.TestCase):
    """Tests for uninstall_service() function."""

    @patch("service.subprocess.run")
    @patch("service.Path.unlink")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_uninstall_success(self, mock_home, mock_exists, mock_unlink, mock_run):
        """Successfully uninstalls service with correct systemctl calls."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = True
        
        uninstall_service(Path("/home/testuser/my-project"))
        
        # Verify stop, disable, daemon-reload are called
        calls = mock_run.call_args_list
        self.assertTrue(len(calls) >= 3)
        # Check that stop and disable are called
        all_calls_str = str(calls)
        self.assertIn("stop", all_calls_str)
        self.assertIn("disable", all_calls_str)
        self.assertIn("daemon-reload", all_calls_str)

    @patch("service.subprocess.run")
    @patch("service.Path.unlink")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_uninstall_when_not_installed(
        self, mock_home, mock_exists, mock_unlink, mock_run
    ):
        """Does not crash when service is not installed."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = False
        
        # Should not crash
        uninstall_service(Path("/home/testuser/my-project"))
        
        # unlink should not be called
        mock_unlink.assert_not_called()


class TestServiceStatus(unittest.TestCase):
    """Tests for service_status() function."""

    @patch("service.subprocess.run")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_status_installed(self, mock_home, mock_exists, mock_run):
        """Shows status when service is installed."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(stdout="Active: active", returncode=0)
        
        # Should not crash
        service_status(Path("/home/testuser/my-project"))

    @patch("service.subprocess.run")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_status_not_installed(self, mock_home, mock_exists, mock_run):
        """Shows helpful message when service is not installed."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = False
        
        # Should not crash and should print message about install
        with patch("builtins.print") as mock_print:
            service_status(Path("/home/testuser/my-project"))
            # Check that it suggests running install
            printed = [str(c) for c in mock_print.call_args_list]
            self.assertTrue(any("install" in p.lower() for p in printed))


class TestRestartService(unittest.TestCase):
    """Tests for restart_service() function."""

    @patch("service.subprocess.run")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_restart_not_installed(self, mock_home, mock_exists, mock_run):
        """Prints 'not installed' message and does not call subprocess when service doesn't exist."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = False
        
        with patch("builtins.print") as mock_print:
            restart_service(Path("/home/testuser/my-project"))
            
            printed = [str(c) for c in mock_print.call_args_list]
            self.assertTrue(any("not installed" in p.lower() for p in printed))
            
            mock_run.assert_not_called()

    @patch("service.subprocess.run")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_restart_success(self, mock_home, mock_exists, mock_run):
        """Successfully restarts service with correct systemctl call."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = True
        
        with patch("builtins.print") as mock_print:
            restart_service(Path("/home/testuser/my-project"))
            
            mock_run.assert_called_once_with(
                ["systemctl", "--user", "restart", "mad-tui-029bdb872059"],
                check=True
            )
            
            printed = [str(c) for c in mock_print.call_args_list]
            self.assertTrue(any("restarted" in p.lower() for p in printed))

    @patch("service.subprocess.run")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_restart_failure(self, mock_home, mock_exists, mock_run):
        """Handles restart failure with error message and sys.exit(1)."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = True
        mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl")
        
        with patch("builtins.print") as mock_print:
            with self.assertRaises(SystemExit) as ctx:
                restart_service(Path("/home/testuser/my-project"))
            
            self.assertEqual(ctx.exception.code, 1)
            
            printed = [str(c) for c in mock_print.call_args_list]
            self.assertTrue(any("error" in p.lower() for p in printed))
            self.assertTrue(any("manually" in p.lower() for p in printed))

    @patch("service.subprocess.run")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_restart_no_tmux_kill(self, mock_home, mock_exists, mock_run):
        """Does not call tmux kill-session; only systemctl restart is called."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = True
        
        restart_service(Path("/home/testuser/my-project"))
        
        self.assertEqual(mock_run.call_count, 1)
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "systemctl")
        self.assertNotIn("kill-session", " ".join(call_args))

    @patch("service.subprocess.run")
    @patch("service.Path.exists")
    @patch("service.Path.home")
    def test_restart_uses_correct_service_name(self, mock_home, mock_exists, mock_run):
        """Uses service_name_for_dir to derive correct service name."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = True
        
        restart_service(Path("/home/testuser/my-project"))
        
        call_args = mock_run.call_args[0][0]
        expected_service_name = service_name_for_dir(Path("/home/testuser/my-project"))
        self.assertEqual(call_args[3], expected_service_name)


class TestServerClientRestartHandler(unittest.TestCase):
    """Tests for restart_tui WebSocket message handling in ServerClient."""

    def test_restart_callback_invoked(self):
        """Callback is called when restart_tui message is received."""
        from server_client import ServerClient
        
        callback_called = []
        
        async def on_restart():
            callback_called.append(True)
        
        sc = ServerClient("ws://localhost:8080", "key", "client1", on_restart=on_restart)
        
        # Simulate receiving restart_tui message
        import asyncio
        asyncio.run(sc._handle_incoming('{"type": "restart_tui"}'))
        
        self.assertEqual(len(callback_called), 1)

    def test_no_callback_no_crash(self):
        """No crash when no callback is registered."""
        from server_client import ServerClient
        
        sc = ServerClient("ws://localhost:8080", "key", "client1")
        
        # Should not crash
        import asyncio
        asyncio.run(sc._handle_incoming('{"type": "restart_tui"}'))


if __name__ == "__main__":
    unittest.main()
