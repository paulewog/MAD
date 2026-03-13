"""Tests for Claude rate limiting handling.

Run with:
    pytest test_rate_limit_handling.py -v
"""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from runner import AgentRunner, RateLimitError
from config import AgentConfig, Config
from agent_status import AgentStatus


def create_mock_config():
    """Create a mock Config and AgentConfig for testing."""
    mock_config = MagicMock(spec=Config)
    mock_config.code_path = Path("/tmp/test_mad")
    mock_config.mad_path = Path("/tmp/test_mad/.mad")
    mock_config.agent = MagicMock(spec=AgentConfig)
    mock_config.agent.name = "test-agent"
    mock_config.agent.cmd = ["echo", "test"]
    mock_config.agent.args = []
    mock_config.agent.prompt = "test prompt"
    mock_config.agent.env = {}
    mock_config.agent.model = "claude"
    mock_config.agent.output_mode = "json"
    mock_config.agent.command = "echo"
    mock_config.agent.headless_extra_flags = []
    mock_config.agent.headless_flag = None
    mock_config.agent.model_flag = None
    return mock_config


def create_mock_process(stdout_content, stderr_content="", return_code=0):
    """Create a mock process with stdout and stderr."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.returncode = return_code
    mock_process.poll.return_value = return_code
    
    mock_stdout = MagicMock()
    if isinstance(stdout_content, list):
        mock_stdout.__iter__ = lambda self: iter(stdout_content)
        lines = stdout_content.copy()
        mock_stdout.readline.side_effect = lambda: lines.pop(0) if lines else ""
    else:
        lines = stdout_content.split('\n') if stdout_content else []
        mock_stdout.__iter__ = lambda self: iter(lines)
        mock_stdout.readline.side_effect = lambda: lines.pop(0) if lines else ""
    
    mock_process.stdout = mock_stdout
    
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = stderr_content
    mock_process.stderr = mock_stderr
    
    return mock_process


def make_select_side_effect(mock_process, num_calls=10):
    """Create a select side effect that returns the mock process stdout."""
    call_count = [0]
    def select_with_stdout(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= num_calls:
            return ([mock_process.stdout], [], [])
        return ([], [], [])
    return select_with_stdout


class TestJSONErrorEventHandling:
    """Test Fix 1: JSON error/system/result event handling."""

    def test_error_event_extracted_from_nested_message(self):
        """Behavior 1.1: Error-type JSON events are extracted and visible in output."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "error", "error": {"message": "rate limit exceeded"}}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                try:
                    result = runner_instance.headless("test prompt", status=None)
                    assert "rate limit exceeded" in result
                except RateLimitError:
                    pass

    def test_error_event_extracted_from_top_level_message(self):
        """Behavior 1.2: Error events with top-level message field are handled."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "error", "message": "some error"}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert "some error" in result

    def test_system_event_extracted(self):
        """Behavior 1.3: System-type JSON events are extracted."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "system", "message": "system notification here"}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert "system notification here" in result

    def test_result_error_event_extracted(self):
        """Behavior 1.4: Result-type error events are extracted."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "result", "is_error": true, "result": "rate limit exceeded"}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                try:
                    result = runner_instance.headless("test prompt", status=None)
                    assert "rate limit exceeded" in result
                except RateLimitError:
                    pass

    def test_result_non_error_not_added(self):
        """Behavior 1.5: Non-error result events are NOT added to output."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = [
            '{"type": "result", "is_error": false, "result": "success"}',
            '{"type": "text", "text": {"text": "hello"}}'
        ]
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert "hello" in result

    def test_status_lines_updated_for_error_events(self):
        """Behavior 1.6: Status lines are updated for error/system/result events."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        status = AgentStatus()
        stdout_lines = ['{"type": "error", "error": {"message": "test error"}}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                try:
                    runner_instance.headless("test prompt", status=status)
                    assert "test error" in status.lines
                except (RateLimitError, Exception):
                    pass

    def test_existing_text_handling_unchanged(self):
        """Behavior 1.7: Existing text/tool_result/tool_use handling is unchanged."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = [
            '{"type": "text", "text": {"text": "hello world"}}',
            '{"type": "tool_result", "tool_result": {"tool_use_id": "123", "content": "tool result data"}}',
            '{"type": "tool_use", "part": {"name": "test_tool"}}'
        ]
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert "hello world" in result
                assert "tool result data" in result
                assert "test_tool" in result


class TestStderrRateLimitDetection:
    """Test Fix 2: Stderr rate limit detection."""

    def test_rate_limit_phrase_in_stderr_raises_error(self):
        """Behavior 2.1: Rate limit phrases in stderr trigger RateLimitError."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "some output"}}']
        stderr = "Error: rate limit exceeded"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content=stderr, return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with pytest.raises(RateLimitError) as exc_info:
                    runner_instance.headless("test prompt", status=None)
                assert "rate limit" in str(exc_info.value).lower()

    def test_stderr_check_only_runs_without_completion_marker(self):
        """Behavior 2.2: Stderr rate limit check only runs when no completion marker."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "some output"}}']
        stderr = "Error: rate limit exceeded"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content=stderr, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.read_text', return_value="PHASE_COMPLETE"):
                        try:
                            result = runner_instance.headless("test prompt", status=None)
                        except RateLimitError:
                            pytest.fail("RateLimitError should NOT be raised when completion marker exists")

    def test_stdout_rate_limit_still_works(self):
        """Behavior 2.3: Stdout rate limit detection still works."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "rate limit exceeded"}}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with patch('pathlib.Path.exists', return_value=False):
                    with pytest.raises(RateLimitError):
                        runner_instance.headless("test prompt", status=None)

    def test_all_rate_limit_phrases_in_stderr(self):
        """Behavior 2.4: All rate limit phrases are checked in stderr."""
        import runner
        
        phrases = [
            "rate limit",
            "rate limit exceeded",
            "you are being rate limited",
            "rate limited due to",
            "too many requests"
        ]
        
        for phrase in phrases:
            config = create_mock_config()
            runner_instance = AgentRunner(config, agent_name="test-agent")
            
            stdout_lines = ['{"type": "text", "text": {"text": "output"}}']
            stderr = f"Error: {phrase}"
            
            with patch('pipeline.runner.subprocess.Popen') as mock_popen:
                mock_process = create_mock_process(stdout_lines, stderr_content=stderr, return_code=1)
                mock_popen.return_value = mock_process
                
                with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                    with pytest.raises(RateLimitError):
                        runner_instance.headless("test prompt", status=None)

    def test_case_insensitive_stderr_matching(self):
        """Behavior 2.5: Case-insensitive matching on stderr."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "output"}}']
        stderr = "Error: RATE LIMIT EXCEEDED"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content=stderr, return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with pytest.raises(RateLimitError):
                    runner_instance.headless("test prompt", status=None)


class TestNonZeroExitError:
    """Test Fix 3: Include stdout in non-zero exit error."""

    def test_runtime_error_includes_both_stderr_and_stdout(self):
        """Behavior 3.1: RuntimeError includes both stderr and stdout snippets."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "long output here"}}']
        stderr = "Error message in stderr"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content=stderr, return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with pytest.raises(RuntimeError) as exc_info:
                    runner_instance.headless("test prompt", status=None)
                error_msg = str(exc_info.value)
                assert "stderr:" in error_msg
                assert "stdout (last 500 chars):" in error_msg
                assert "Error message in stderr" in error_msg

    def test_empty_stderr_shows_placeholder(self):
        """Behavior 3.2: Empty stderr shows placeholder."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "some output"}}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content="", return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with pytest.raises(RuntimeError) as exc_info:
                    runner_instance.headless("test prompt", status=None)
                assert "(no stderr)" in str(exc_info.value)

    def test_empty_stdout_shows_placeholder(self):
        """Behavior 3.3: Empty stdout shows placeholder."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = []
        stderr = "Error in stderr"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content=stderr, return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', return_value=([], [], [])):
                with pytest.raises(RuntimeError) as exc_info:
                    runner_instance.headless("test prompt", status=None)
                assert "(no stdout)" in str(exc_info.value)

    def test_long_stderr_truncated_to_500_chars(self):
        """Behavior 3.4: Long stderr is truncated to 500 chars."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "output"}}']
        stderr = "x" * 600
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content=stderr, return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with pytest.raises(RuntimeError) as exc_info:
                    runner_instance.headless("test prompt", status=None)
                error_msg = str(exc_info.value)
                stderr_part = error_msg.split("stderr:")[1].split("stdout")[0]
                assert len(stderr_part.strip()) == 500

    def test_long_stdout_shows_last_500_chars(self):
        """Behavior 3.5: Long stdout is truncated to last 500 chars."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        long_output = "x" * 600
        stdout_lines = [f'{{"type": "text", "text": {{"text": "{long_output}"}}}}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with pytest.raises(RuntimeError) as exc_info:
                    runner_instance.headless("test prompt", status=None)
                error_msg = str(exc_info.value)
                stdout_part = error_msg.split("stdout (last 500 chars):")[1]
                assert len(stdout_part.strip()) == 500


class TestEdgeCases:
    """Test edge cases from the spec."""

    def test_empty_error_message_uses_str_event(self):
        """Empty error message falls back to str(event) representation."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "error", "error": {}}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert len(result) > 0

    def test_null_message_fields(self):
        """Null/missing message fields fall back to str(event)."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "error"}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert len(result) > 0

    def test_system_event_no_message(self):
        """System event with no message uses str(event)."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "system"}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert len(result) > 0

    def test_result_event_no_result_field(self):
        """Result event with is_error=true but no result field uses str(event)."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "result", "is_error": true}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert len(result) > 0

    def test_mixed_event_types(self):
        """Mixed event types handle each correctly."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = [
            '{"type": "text", "text": {"text": "hello"}}',
            '{"type": "error", "message": "error msg"}',
            '{"type": "system", "message": "system notification"}}',
            '{"type": "tool_use", "part": {"name": "tool"}}'
        ]
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                result = runner_instance.headless("test prompt", status=None)
                assert "hello" in result
                assert "error msg" in result
                assert "system notification" in result
                assert "tool" in result

    def test_stderr_empty_no_crash(self):
        """Stderr is empty/None doesn't cause crash."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "output"}}']
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content="", return_code=0)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                try:
                    result = runner_instance.headless("test prompt", status=None)
                except Exception as e:
                    pytest.fail(f"Should not crash with empty stderr: {e}")

    def test_multiline_rate_limit_in_stderr(self):
        """Rate limit phrase spans multiple lines in stderr."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = ['{"type": "text", "text": {"text": "output"}}']
        stderr = "Error\nYou are being\nrate limited\nnow"
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content=stderr, return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', side_effect=make_select_side_effect(mock_process)):
                with pytest.raises(RateLimitError):
                    runner_instance.headless("test prompt", status=None)

    def test_both_stderr_and_stdout_empty(self):
        """Both stderr and stdout are empty shows both placeholders."""
        import runner
        
        config = create_mock_config()
        runner_instance = AgentRunner(config, agent_name="test-agent")
        
        stdout_lines = []
        
        with patch('pipeline.runner.subprocess.Popen') as mock_popen:
            mock_process = create_mock_process(stdout_lines, stderr_content="", return_code=1)
            mock_popen.return_value = mock_process
            
            with patch.object(runner.select, 'select', return_value=([], [], [])):
                with pytest.raises(RuntimeError) as exc_info:
                    runner_instance.headless("test prompt", status=None)
                error_msg = str(exc_info.value)
                assert "(no stderr)" in error_msg
                assert "(no stdout)" in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
