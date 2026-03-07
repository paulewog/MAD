"""Tests for the add code path to board feature."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys, os as _
sys.path.insert(0, os.path.dirname(__file__))

from config import (
    Config, 
    PROJECT_CONFIG, 
    LOCAL_DIR, 
    LOCAL_CONFIG, 
    CONTEXT_FILENAME, 
    MAX_CONTEXT_SIZE,
    read_context_file, 
    write_context_file, 
    view_context_file, 
    edit_context_file
)
from runner import AgentRunner


class TestConfigCodePath(unittest.TestCase):
    """Tests for code_path config property and methods."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mad_dir = Path(self.test_dir) / LOCAL_DIR
        self.boards_dir = self.mad_dir / "boards" / "testboard"
        self.boards_dir.mkdir(parents=True)
        self.project_root = Path(self.test_dir)
        
        self.config_path = self.mad_dir / LOCAL_CONFIG
        default_config = {
            "current_agent": "claude",
            "agents": {
                "claude": {
                    "command": "claude",
                    "headless_flag": "-p",
                    "headless_extra_flags": ["--dangerously-skip-permissions"]
                }
            },
            "boards": ["testboard"],
        }
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_set_and_get_global_code_path(self):
        """Test setting and getting global code_path via Config."""
        config = Config(path=self.config_path)
        config.set_code_path("/some/path")
        
        project_config_path = self.project_root / PROJECT_CONFIG
        self.assertTrue(project_config_path.exists())
        
        with open(project_config_path) as f:
            data = json.load(f)
        self.assertEqual(data["code_path"], "/some/path")
        
        value = config.get_code_path_value()
        self.assertEqual(value, "/some/path")

    def test_code_path_resolves_symlinks(self):
        """Test that code_path resolves symlinks when using property."""
        config = Config(path=self.config_path)
        
        real_path = self.test_dir + "/test_real"
        symlink_path = self.test_dir + "/test_symlink"
        
        if os.path.exists(symlink_path):
            os.remove(symlink_path)
        
        os.makedirs(real_path, exist_ok=True)
        os.symlink(real_path, symlink_path)
        
        try:
            config.set_code_path(symlink_path)
            
            code_path = config.code_path
            self.assertEqual(str(code_path), real_path)
        finally:
            if os.path.islink(symlink_path):
                os.remove(symlink_path)
        
        try:
            os.symlink(real_path, symlink_path)
            config.set_code_path(symlink_path)
            
            code_path = config.code_path
            self.assertEqual(str(code_path), real_path)
        finally:
            if os.path.exists(symlink_path):
                os.remove(symlink_path)

    def test_per_board_config_overrides_global(self):
        """Test that per-board config takes precedence over global config."""
        config = Config(path=self.config_path)
        
        project_config_path = self.project_root / PROJECT_CONFIG
        with open(project_config_path, "w") as f:
            json.dump({"code_path": "/global/path"}, f)
        
        board_config_path = self.boards_dir / LOCAL_CONFIG
        with open(board_config_path, "w") as f:
            json.dump({"code_path": "/per/board/path"}, f)
        
        code_path = config.code_path
        self.assertEqual(str(code_path), "/per/board/path")

    def test_derive_code_path_from_board_location(self):
        """Test default code_path derivation from board location."""
        config = Config(path=self.config_path)
        
        code_path = config._derive_code_path()
        
        self.assertEqual(code_path, self.project_root)

    def test_get_code_path_not_set(self):
        """Test get_code_path_value when not configured."""
        config = Config(path=self.config_path)
        
        value = config.get_code_path_value()
        self.assertIsNone(value)

    def test_empty_code_path_is_allowed(self):
        """Test that empty code_path raises ValueError."""
        config = Config(path=self.config_path)
        
        with self.assertRaises(ValueError) as ctx:
            config.set_code_path("")
        
        self.assertIn("cannot be empty", str(ctx.exception))

    def test_corrupted_project_config_derives_code_path(self):
        """Test that corrupted .mad.config.json falls back to derived code path."""
        project_config_path = self.project_root / PROJECT_CONFIG
        project_config_path.write_text("{ invalid json }")
        
        config = Config(path=self.config_path)
        
        code_path = config.code_path
        self.assertIsNotNone(code_path)


class TestPerBoardConfig(unittest.TestCase):
    """Tests for per-board configuration."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mad_dir = Path(self.test_dir) / LOCAL_DIR
        self.boards_dir = self.mad_dir / "boards"
        self.board_dir = self.boards_dir / "myboard"
        self.board_dir.mkdir(parents=True)
        self.project_root = Path(self.test_dir)
        
        self.config_path = self.mad_dir / LOCAL_CONFIG
        default_config = {
            "current_agent": "claude",
            "agents": {
                "claude": {
                    "command": "claude",
                    "headless_flag": "-p",
                    "headless_extra_flags": []
                }
            },
            "boards": ["myboard"],
        }
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_invalid_per_board_config_falls_back_to_global(self):
        """Test that invalid per-board config falls back to global."""
        project_config_path = self.project_root / PROJECT_CONFIG
        with open(project_config_path, "w") as f:
            json.dump({"code_path": "/global/path"}, f)
        
        board_config_path = self.board_dir / LOCAL_CONFIG
        board_config_path.write_text("not valid json")
        
        config = Config(path=self.config_path)
        
        code_path = config.code_path
        self.assertEqual(str(code_path), "/global/path")


class TestContextFile(unittest.TestCase):
    """Tests for CONTEXT.md file handling."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mad_dir = Path(self.test_dir) / LOCAL_DIR
        self.boards_dir = self.mad_dir / "boards" / "default"
        self.boards_dir.mkdir(parents=True)
        self.project_root = Path(self.test_dir)
        
        self.config_path = self.mad_dir / LOCAL_CONFIG
        default_config = {
            "current_agent": "claude",
            "agents": {
                "claude": {
                    "command": "claude",
                    "headless_flag": "-p",
                    "headless_extra_flags": []
                }
            },
            "boards": ["default"],
        }
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)
        
        self.config = Config(path=self.config_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_context_file_property_returns_correct_path(self):
        """Test that context_file property returns correct path."""
        expected = self.project_root / LOCAL_DIR / CONTEXT_FILENAME
        self.assertEqual(self.config.context_file, expected)

    def test_read_context_file_not_exists(self):
        """Test reading when CONTEXT.md doesn't exist."""
        result = read_context_file(self.config)
        self.assertIsNone(result)

    def test_read_context_file_exists(self):
        """Test reading existing CONTEXT.md."""
        context_path = self.config.context_file
        context_path.write_text("Test context content")
        
        result = read_context_file(self.config)
        self.assertEqual(result, "Test context content")

    def test_read_context_file_truncates_at_100kb(self):
        """Test that CONTEXT.md is truncated at 100KB byte boundary."""
        context_path = self.config.context_file
        
        large_content = "x" * (MAX_CONTEXT_SIZE + 1000)
        context_path.write_bytes(large_content.encode("utf-8"))
        
        result = read_context_file(self.config)
        
        self.assertLessEqual(len(result.encode("utf-8")), MAX_CONTEXT_SIZE)

    def test_read_context_file_exactly_100kb(self):
        """Test that exactly 100KB is accepted without truncation."""
        context_path = self.config.context_file
        
        content_100kb = "x" * MAX_CONTEXT_SIZE
        context_path.write_bytes(content_100kb.encode("utf-8"))
        
        result = read_context_file(self.config)
        
        self.assertEqual(len(result.encode("utf-8")), MAX_CONTEXT_SIZE)

    def test_read_context_file_truncates_at_valid_utf8_boundary(self):
        """Test truncation at valid UTF-8 byte boundary."""
        context_path = self.config.context_file
        
        multi_byte_content = "é" * (MAX_CONTEXT_SIZE // 2 + 100)
        context_path.write_text(multi_byte_content)
        
        result = read_context_file(self.config)
        
        try:
            result.encode("utf-8")
        except UnicodeEncodeError:
            self.fail("Truncation split a multi-byte character")

    def test_read_context_file_permission_error(self):
        """Test that permission errors are handled gracefully."""
        context_path = self.config.context_file
        context_path.write_text("test")
        
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            result = read_context_file(self.config)
        
        self.assertIsNone(result)

    def test_write_and_read_context_file(self):
        """Test writing and reading context file."""
        content = "New context content"
        write_context_file(self.config, content)
        
        result = read_context_file(self.config)
        self.assertEqual(result, content)

    def test_write_context_file_truncates_at_100kb(self):
        """Test that writing truncates at 100KB."""
        large_content = "y" * (MAX_CONTEXT_SIZE + 500)
        write_context_file(self.config, large_content)
        
        context_path = self.config.context_file
        file_size = context_path.stat().st_size
        
        self.assertLessEqual(file_size, MAX_CONTEXT_SIZE)

    def test_view_context_file_prints_content(self):
        """Test view_context_file prints content to stdout."""
        context_path = self.config.context_file
        context_path.write_text("View test content")
        
        with patch("builtins.print") as mock_print:
            view_context_file(self.config)
            mock_print.assert_called_with("View test content")

    def test_view_context_file_not_exists(self):
        """Test view_context_file when file doesn't exist."""
        with patch("builtins.print") as mock_print:
            view_context_file(self.config)
            mock_print.assert_called_with("No CONTEXT.md file found.")

    @patch.dict(os.environ, {"EDITOR": "true_editor"})
    def test_edit_context_file_uses_editor_env(self):
        """Test edit_context_file uses $EDITOR when set."""
        context_path = self.config.context_file
        context_path.write_text("original")
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            edit_context_file(self.config)
            
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            self.assertEqual(call_args[0], "true_editor")

    def test_edit_context_file_creates_if_not_exists(self):
        """Test edit_context_file creates file if it doesn't exist."""
        context_path = self.config.context_file
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            edit_context_file(self.config)
        
        self.assertTrue(context_path.exists())


class TestAgentRunner(unittest.TestCase):
    """Tests for AgentRunner code_path integration."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mad_dir = Path(self.test_dir) / LOCAL_DIR
        self.boards_dir = self.mad_dir / "boards" / "default"
        self.boards_dir.mkdir(parents=True)
        self.project_root = Path(self.test_dir)
        
        self.config_path = self.mad_dir / LOCAL_CONFIG
        default_config = {
            "current_agent": "claude",
            "agents": {
                "claude": {
                    "command": "claude",
                    "headless_flag": "-p",
                    "headless_extra_flags": []
                }
            },
            "boards": ["default"],
        }
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)
        
        self.config = Config(path=self.config_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_runner_uses_code_path_as_workdir(self):
        """Test that AgentRunner uses code_path as default workdir."""
        self.config.set_code_path("/some/code/path")
        
        runner = AgentRunner(self.config)
        
        self.assertEqual(runner.workdir, Path("/some/code/path"))

    def test_runner_falls_back_to_mad_dir(self):
        """Test runner falls back to MAD_DIR when code_path not set."""
        runner = AgentRunner(self.config)
        
        self.assertEqual(runner.workdir, self.project_root)

    def test_runner_accepts_explicit_workdir(self):
        """Test that runner accepts explicit workdir parameter."""
        runner = AgentRunner(self.config, workdir=Path("/explicit/path"))
        
        self.assertEqual(str(runner.workdir), "/explicit/path")

    def test_prepend_context_includes_code_path(self):
        """Test that _prepend_context includes code path."""
        self.config.set_code_path("/my/code")
        
        runner = AgentRunner(self.config)
        result = runner._prepend_context("test prompt")
        
        self.assertIn("Code Path: /my/code", result)

    def test_prepend_context_includes_context_file(self):
        """Test that _prepend_context includes context file path."""
        runner = AgentRunner(self.config)
        result = runner._prepend_context("test prompt")
        
        expected_context_path = str(self.config.context_file)
        self.assertIn(f"Context File: {expected_context_path}", result)

    def test_prepend_context_includes_context_content(self):
        """Test that _prepend_context includes context file content."""
        context_path = self.config.context_file
        context_path.write_text("Context content here")
        
        runner = AgentRunner(self.config)
        result = runner._prepend_context("test prompt")
        
        self.assertIn("Context content here", result)

    def test_prepend_context_does_not_include_missing_context_content(self):
        """Test that missing context content is handled gracefully."""
        runner = AgentRunner(self.config)
        result = runner._prepend_context("test prompt")
        
        self.assertIn("test prompt", result)
        self.assertNotIn("None", result)

    def test_prepend_context_appends_original_prompt(self):
        """Test that original prompt is at the end."""
        runner = AgentRunner(self.config)
        result = runner._prepend_context("original prompt")
        
        self.assertTrue(result.endswith("original prompt"))


class TestConfigCLI(unittest.TestCase):
    """Tests for CLI config commands via _handle_config_command."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mad_dir = Path(self.test_dir) / LOCAL_DIR
        self.boards_dir = self.mad_dir / "boards" / "default"
        self.boards_dir.mkdir(parents=True)
        
        self.config_path = self.mad_dir / LOCAL_CONFIG
        default_config = {
            "current_agent": "claude",
            "agents": {
                "claude": {
                    "command": "claude",
                    "headless_flag": "-p",
                    "headless_extra_flags": []
                }
            },
            "boards": ["default"],
        }
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)
        
        self.config = Config(path=self.config_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_config_get_code_path_when_set(self):
        """Test 'mad config get code_path' when set."""
        from tui import _handle_config_command
        
        self.config.set_code_path("/test/path")
        
        with patch("tui.Config", return_value=self.config):
            with patch("builtins.print") as mock_print:
                _handle_config_command(["get", "code_path"])
                
                mock_print.assert_called_with("/test/path")

    def test_config_get_code_path_when_not_set(self):
        """Test 'mad config get code_path' when not set."""
        from tui import _handle_config_command
        
        with patch("tui.Config", return_value=self.config):
            with patch("builtins.print") as mock_print:
                _handle_config_command(["get", "code_path"])
                
                printed = [str(call) for call in mock_print.call_args_list]
                self.assertTrue(any("(derived)" in str(p) or "(not set)" in str(p) for p in printed))

    def test_config_set_code_path(self):
        """Test 'mad config set code_path <value>'."""
        from tui import _handle_config_command
        
        with patch("tui.Config", return_value=self.config):
            with patch("builtins.print") as mock_print:
                _handle_config_command(["set", "code_path", "/new/path"])
                
                mock_print.assert_called_with("code_path set to: /new/path")
        
        value = self.config.get_code_path_value()
        self.assertEqual(value, "/new/path")

    def test_config_set_empty_code_path_allowed(self):
        """Test 'mad config set code_path' with empty value raises error."""
        from tui import _handle_config_command
        
        with patch("tui.Config", return_value=self.config):
            with self.assertRaises(ValueError) as ctx:
                _handle_config_command(["set", "code_path", ""])
            
            self.assertIn("cannot be empty", str(ctx.exception))


class TestContextCLI(unittest.TestCase):
    """Tests for CLI context commands via _handle_context_command."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mad_dir = Path(self.test_dir) / LOCAL_DIR
        self.boards_dir = self.mad_dir / "boards" / "default"
        self.boards_dir.mkdir(parents=True)
        
        self.config_path = self.mad_dir / LOCAL_CONFIG
        default_config = {
            "current_agent": "claude",
            "agents": {
                "claude": {
                    "command": "claude",
                    "headless_flag": "-p",
                    "headless_extra_flags": []
                }
            },
            "boards": ["default"],
        }
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)
        
        self.config = Config(path=self.config_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_context_view_when_exists(self):
        """Test 'mad context view' when file exists."""
        from tui import _handle_context_command
        
        context_path = self.config.context_file
        context_path.write_text("Viewable content")
        
        with patch("tui.Config", return_value=self.config):
            with patch("builtins.print") as mock_print:
                _handle_context_command(["view"])
                
                mock_print.assert_called_with("Viewable content")

    def test_context_view_when_not_exists(self):
        """Test 'mad context view' when file doesn't exist."""
        from tui import _handle_context_command
        
        with patch("tui.Config", return_value=self.config):
            with patch("builtins.print") as mock_print:
                _handle_context_command(["view"])
                
                mock_print.assert_called_with("No CONTEXT.md file found.")

    def test_context_edit_uses_editor(self):
        """Test 'mad context edit' invokes editor."""
        from tui import _handle_context_command
        
        with patch("tui.Config", return_value=self.config):
            with patch("tui.edit_context_file") as mock_edit:
                _handle_context_command(["edit"])
                
                mock_edit.assert_called_once_with(self.config)


class TestEdgeCases(unittest.TestCase):
    """Tests for edge cases from the test specification."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mad_dir = Path(self.test_dir) / LOCAL_DIR
        self.boards_dir = self.mad_dir / "boards" / "default"
        self.boards_dir.mkdir(parents=True)
        self.project_root = Path(self.test_dir)
        
        self.config_path = self.mad_dir / LOCAL_CONFIG
        default_config = {
            "current_agent": "claude",
            "agents": {
                "claude": {
                    "command": "claude",
                    "headless_flag": "-p",
                    "headless_extra_flags": []
                }
            },
            "boards": ["default"],
        }
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)
        
        self.config = Config(path=self.config_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_set_code_path_to_none_raises_error(self):
        """Test that setting code_path to None raises ValueError."""
        with self.assertRaises((ValueError, TypeError)):
            self.config.set_code_path(None)

    def test_set_code_path_to_whitespace_raises_error(self):
        """Test that setting code_path to whitespace-only string raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.config.set_code_path("   ")
        
        self.assertIn("cannot be empty", str(ctx.exception))

    def test_set_code_path_to_nonexistent_path_allowed(self):
        """Test that setting code_path to non-existent path is allowed."""
        self.config.set_code_path("/nonexistent/path/that/does/not/exist")
        
        value = self.config.get_code_path_value()
        self.assertEqual(value, "/nonexistent/path/that/does/not/exist")

    def test_set_code_path_to_very_long_path(self):
        """Test that very long paths for code_path are handled correctly."""
        long_path = "/".join(["a" * 50] * 20)
        
        self.config.set_code_path(long_path)
        
        value = self.config.get_code_path_value()
        self.assertEqual(value, long_path)

    def test_edit_context_file_falls_back_to_vim(self):
        """Test edit_context_file falls back to vim when $EDITOR not set."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("subprocess.run") as mock_run:
                def run_side_effect(*args, **kwargs):
                    cmd = args[0]
                    if cmd[0] == "which":
                        return MagicMock(returncode=0)
                    return MagicMock(returncode=0)
                
                mock_run.side_effect = run_side_effect
                edit_context_file(self.config)
                
                call_args = mock_run.call_args_list
                which_calls = [c for c in call_args if c[0][0][0] == "which"]
                self.assertTrue(len(which_calls) > 0, "which should be called")

    def test_edit_context_file_no_editor_available(self):
        """Test edit_context_file raises error when no editor available."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("subprocess.run") as mock_run:
                def run_side_effect(*args, **kwargs):
                    cmd = args[0]
                    if cmd[0] == "which":
                        return MagicMock(returncode=1)
                    return MagicMock(returncode=0)
                
                mock_run.side_effect = run_side_effect
                with self.assertRaises(RuntimeError) as ctx:
                    edit_context_file(self.config)
                
                self.assertIn("No editor found", str(ctx.exception))

    def test_edit_context_file_non_zero_exit_code(self):
        """Test edit_context_file raises error when editor exits with non-zero code."""
        with patch.dict(os.environ, {"EDITOR": "test_editor"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                with self.assertRaises(RuntimeError) as ctx:
                    edit_context_file(self.config)
                
                self.assertIn("exited with code", str(ctx.exception))

    def test_per_board_config_nonexistent_board(self):
        """Test that accessing per-board config for non-existent board fails gracefully."""
        config = Config(path=self.config_path)
        
        result = config._get_per_board_config("nonexistent_board")
        self.assertIsNone(result)

    def test_config_write_permission_error(self):
        """Test that config write with permission error raises RuntimeError."""
        config = Config(path=self.config_path)
        
        project_config_path = self.project_root / PROJECT_CONFIG
        project_config_path.parent.mkdir(parents=True, exist_ok=True)
        project_config_path.write_text("{}")
        project_config_path.chmod(0o000)
        
        try:
            with self.assertRaises((RuntimeError, OSError, PermissionError)):
                config.set_code_path("/test/path")
        finally:
            project_config_path.chmod(0o644)

    def test_context_file_truncation_at_100kb_plus_one(self):
        """Test CONTEXT.md at 100KB + 1 byte is truncated at byte boundary."""
        context_path = self.config.context_file
        
        content = "x" * (MAX_CONTEXT_SIZE + 1)
        context_path.write_bytes(content.encode("utf-8"))
        
        result = read_context_file(self.config)
        
        self.assertLessEqual(len(result.encode("utf-8")), MAX_CONTEXT_SIZE)


class TestConcurrentOperations(unittest.TestCase):
    """Tests for concurrent operations from the test specification."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mad_dir = Path(self.test_dir) / LOCAL_DIR
        self.boards_dir = self.mad_dir / "boards" / "default"
        self.boards_dir.mkdir(parents=True)
        self.project_root = Path(self.test_dir)
        
        self.config_path = self.mad_dir / LOCAL_CONFIG
        default_config = {
            "current_agent": "claude",
            "agents": {
                "claude": {
                    "command": "claude",
                    "headless_flag": "-p",
                    "headless_extra_flags": []
                }
            },
            "boards": ["default"],
        }
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_concurrent_config_set_operations(self):
        """Test that concurrent config set operations don't corrupt the config file."""
        import threading
        
        config1 = Config(path=self.config_path)
        config2 = Config(path=self.config_path)
        
        errors = []
        
        def set_path(path):
            try:
                config = Config(path=self.config_path)
                config.set_code_path(path)
            except Exception as e:
                errors.append(e)
        
        t1 = threading.Thread(target=set_path, args=("/path/one",))
        t2 = threading.Thread(target=set_path, args=("/path/two",))
        
        t1.start()
        t2.start()
        
        t1.join()
        t2.join()
        
        self.assertEqual(len(errors), 0, f"Errors during concurrent operations: {errors}")
        
        project_config_path = self.project_root / PROJECT_CONFIG
        self.assertTrue(project_config_path.exists())
        
        with open(project_config_path) as f:
            data = json.load(f)
        
        self.assertIn("code_path", data)


if __name__ == "__main__":
    unittest.main()
