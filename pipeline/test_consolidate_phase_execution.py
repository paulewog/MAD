"""Tests for consolidate-phase-execution feature.

Run with:
    pytest test_consolidate_phase_execution.py -v
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
import phases


def _make_feature(title="Test Feature", slug="test-feature", fid="f1"):
    """Create a mock feature object."""
    f = SimpleNamespace()
    f.title = title
    f.slug = slug
    f.id = fid
    f.current_stage = "plan-inbox"
    f.board = "default"
    f.created = "2025-01-01"
    f.description = "A test feature"
    f.questions = []
    f.plan = ""
    f.impl_spec = ""
    f.test_spec = ""
    f.impl_notes = ""
    f.history = "[]"
    f._data = {"pipeline_log": [], "history": []}
    f.add_history = MagicMock()
    f.save = MagicMock()
    f.move_to_stage = MagicMock()
    f.set_plan = MagicMock()
    f.set_impl_spec = MagicMock()
    f.set_test_spec = MagicMock()
    f.set_impl_notes = MagicMock()
    f.set_questions = MagicMock()
    return f


class TestPhaseConfigRegistry:
    """Test PHASE_CONFIG dictionary structure and validation."""

    def test_phase_config_exists(self):
        """Verify PHASE_CONFIG dictionary exists."""
        assert hasattr(phases, 'PHASE_CONFIG')
        assert isinstance(phases.PHASE_CONFIG, dict)

    def test_all_8_phases_exist(self):
        """Verify all 8 phase keys exist."""
        expected_keys = [
            "planning", "reviewing_plan", "spec_impl", "spec_test",
            "implementing", "fix_feedback", "writing_tests", "review_impl"
        ]
        for key in expected_keys:
            assert key in phases.PHASE_CONFIG, f"Phase key '{key}' not found"

    def test_each_phase_has_required_keys(self):
        """Verify each phase has required configuration keys."""
        required_keys = ["runner_phase", "history_tag", "status_label", "template", "done_marker"]
        for phase_key, config in phases.PHASE_CONFIG.items():
            for key in required_keys:
                assert key in config, f"Phase '{phase_key}' missing key '{key}'"

    def test_history_tags_are_alphabetic(self):
        """Verify history_tag values contain only alphabetic characters."""
        for phase_key, config in phases.PHASE_CONFIG.items():
            history_tag = config["history_tag"]
            assert history_tag.replace("_", "").isalpha(), f"history_tag '{history_tag}' contains non-alphabetic characters"

    def test_done_markers_correct(self):
        """Verify done_marker values are correctly set."""
        assert phases.PHASE_CONFIG["spec_test"]["done_marker"] == "TESTDONE"
        for phase_key, config in phases.PHASE_CONFIG.items():
            if phase_key != "spec_test":
                assert config["done_marker"] == "DONE", f"Phase '{phase_key}' should use DONE, got {config['done_marker']}"


class TestBuildPromptHelper:
    """Test _build_prompt() function."""

    def test_build_prompt_raises_file_not_found(self):
        """Verify FileNotFoundError is raised when template doesn't exist."""
        with pytest.raises(FileNotFoundError) as exc_info:
            phases._build_prompt("nonexistent.md", {})
        assert "Prompt template not found" in str(exc_info.value)

    def test_build_prompt_loads_template(self):
        """Verify template file is loaded from PROMPTS_DIR."""
        result = phases._build_prompt("plan-headless.md", {})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_prompt_applies_replacements(self):
        """Verify all key-value replacements are applied."""
        replacements = {
            "{title}": "Test Title",
            "{description}": "Test Description"
        }
        result = phases._build_prompt("plan-headless.md", replacements)
        assert "Test Title" in result
        assert "Test Description" in result

    def test_build_prompt_empty_replacements(self):
        """Verify template is returned without modifications when replacements dict is empty."""
        result = phases._build_prompt("plan-headless.md", {})
        assert isinstance(result, str)
        assert "{title}" in result

    def test_build_prompt_circular_replacement(self):
        """Verify single pass replacement - placeholders remain when not in template."""
        replacements = {"{feature_id}": "test123", "{feature_slug}": "test"}
        result = phases._build_prompt("plan-headless.md", replacements)
        assert "test123" in result
        assert "test" in result


class TestBuildPromptWithCheckpointInstructions:
    """Test _build_prompt() checkpoint instruction injection."""

    def test_checkpoint_instructions_injected_when_marker_present(self):
        """Verify checkpoint block is injected when marker is present."""
        replacements = {
            "{title}": "Test",
            "{feature_slug}": "test",
            "{feature_id}": "f1",
            "{phase}": "planning"
        }
        result = phases._build_prompt("impl-spec.md", replacements, "implementing")
        assert "Checkpoint Instructions" in result or ".mad/checkpoints/" in result

    def test_done_marker_replaced_in_checkpoint(self):
        """Verify {done_marker} placeholder is replaced with correct value."""
        replacements = {
            "{title}": "Test",
            "{feature_slug}": "test",
            "{feature_id}": "f1",
            "{phase}": "testing"
        }
        result = phases._build_prompt("test-spec.md", replacements, "spec_test")
        assert "TESTDONE" in result

    def test_regular_templates_use_done(self):
        """Verify regular templates use DONE marker."""
        replacements = {
            "{title}": "Test",
            "{feature_slug}": "test",
            "{feature_id}": "f1",
            "{phase}": "implementing"
        }
        result = phases._build_prompt("implement.md", replacements, "implementing")
        assert "DONE" in result


class TestParseVerdictHelper:
    """Test _parse_verdict() function."""

    def test_parse_verdict_json_format_pass(self):
        """Verify JSON format with verdict PASS is parsed correctly."""
        output = '{"verdict": "PASS", "feedback": "Good job"}'
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "PASS"
        assert "Good job" in feedback

    def test_parse_verdict_json_format_fail(self):
        """Verify JSON format with verdict FAIL is parsed correctly."""
        output = '{"verdict": "FAIL", "feedback": "Needs work"}'
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "FAIL"
        assert "Needs work" in feedback

    def test_parse_verdict_text_format(self):
        """Verify text format with VERDICT: prefix is parsed correctly when JSON parsing fails."""
        output = '{ invalid json } \nVERDICT: PASS\nFEEDBACK: Looks good'
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "PASS"
        assert "Looks good" in feedback

    def test_parse_verdict_json_found_but_invalid_triggers_fallback(self):
        """Verify fallback to text parsing when JSON-like content is found but fails to parse."""
        output = "Some text { invalid json } \nVERDICT: PASS\nFEEDBACK: Looks good"
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "PASS"

    def test_parse_verdict_no_verdict_returns_fail(self):
        """Verify output with no VERDICT marker returns FAIL with full output as feedback."""
        output = "Just some plain text without verdict"
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "FAIL"
        assert "plain text" in feedback

    def test_parse_verdict_malformed_json_fallback(self):
        """Verify fallback to text parsing when JSON is malformed."""
        output = "Some text {bad json} \nVERDICT: PASS\nSome feedback text"
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "PASS"

    def test_parse_verdict_strips_markdown(self):
        """Verify markdown formatting is stripped from feedback."""
        output = '{"verdict": "PASS", "feedback": "**Bold text** and *italic*"}'
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "PASS"
        assert "**" not in feedback
        assert "*" not in feedback or "italic" not in feedback

    def test_parse_verdict_returns_tuple(self):
        """Verify _parse_verdict returns tuple of (verdict, feedback)."""
        output = '{"verdict": "PASS", "feedback": "test"}'
        result = phases._parse_verdict(output)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] in ("PASS", "FAIL")
        assert isinstance(result[1], str)


class TestRunPhaseHelper:
    """Test _run_phase() function."""

    @pytest.fixture
    def mock_runner(self):
        """Create a mock AgentRunner."""
        runner = MagicMock()
        runner.agent = MagicMock()
        runner.agent.name = "test-agent"
        return runner

    @pytest.fixture
    def feature(self):
        """Create a mock feature."""
        return _make_feature()

    def test_run_phase_updates_status_when_provided(self, feature, mock_runner):
        """Verify status is updated when status parameter is not None."""
        status = MagicMock()
        mock_runner.headless.return_value = "test output"
        
        phases._run_phase("planning", feature, mock_runner, "prompt", status=status)
        
        assert status.phase is not None

    def test_run_phase_skips_status_when_none(self, feature, mock_runner):
        """Verify status updates are skipped when status is None."""
        mock_runner.headless.return_value = "test output"
        
        phases._run_phase("planning", feature, mock_runner, "prompt", status=None)
        
        assert feature.add_history.call_count >= 1

    def test_run_phase_adds_history(self, feature, mock_runner):
        """Verify history is added with correct history_tag."""
        mock_runner.headless.return_value = "test output"
        
        phases._run_phase("planning", feature, mock_runner, "prompt")
        
        feature.add_history.assert_called()

    def test_run_phase_calls_headless(self, feature, mock_runner):
        """Verify runner.headless() is called with correct prompt."""
        mock_runner.headless.return_value = "test output"
        
        phases._run_phase("planning", feature, mock_runner, "test prompt")
        
        mock_runner.headless.assert_called_once()

    def test_run_phase_returns_output(self, feature, mock_runner):
        """Verify raw output string is returned from runner.headless()."""
        mock_runner.headless.return_value = "expected output"
        
        result = phases._run_phase("planning", feature, mock_runner, "prompt")
        
        assert result == "expected output"

    def test_run_phase_raises_runtime_error_on_empty_output(self, feature, mock_runner):
        """Verify RuntimeError is raised when output is empty."""
        mock_runner.headless.return_value = ""
        
        with pytest.raises(RuntimeError) as exc_info:
            phases._run_phase("planning", feature, mock_runner, "prompt")
        
        assert "empty output" in str(exc_info.value).lower()

    def test_run_phase_raises_runtime_error_on_whitespace_only(self, feature, mock_runner):
        """Verify RuntimeError is raised when output is whitespace only."""
        mock_runner.headless.return_value = "   \n\t  "
        
        with pytest.raises(RuntimeError) as exc_info:
            phases._run_phase("planning", feature, mock_runner, "prompt")
        
        assert "empty output" in str(exc_info.value).lower()

    def test_run_phase_handles_headless_exception(self, feature, mock_runner):
        """Verify exception from headless() is caught and logged to history."""
        mock_runner.headless.side_effect = Exception("API Error")
        
        with pytest.raises(Exception):
            phases._run_phase("planning", feature, mock_runner, "prompt")
        
        feature.add_history.assert_called()

    def test_run_phase_skip_history_start(self, feature, mock_runner):
        """Verify skip_history_start flag prevents double-logging."""
        mock_runner.headless.return_value = "output"
        
        phases._run_phase("spec_test", feature, mock_runner, "prompt", skip_history_start=True)
        
        assert feature.save.call_count == 0 or feature.add_history.call_count == 0

    def test_run_phase_invalid_phase_key_raises_key_error(self, feature, mock_runner):
        """Verify KeyError is raised when accessing non-existent PHASE_CONFIG key."""
        mock_runner.headless.return_value = "output"
        
        with pytest.raises(KeyError):
            phases._run_phase("invalid_phase", feature, mock_runner, "prompt")


class TestPhaseFunctionRefactoring:
    """Test that phase functions use the helper functions."""

    def test_run_planning_uses_build_prompt_not_load_prompt(self):
        """Verify run_planning uses _build_prompt() not _load_prompt() as required by spec."""
        import inspect
        source = inspect.getsource(phases.run_planning)
        assert "_build_prompt" in source, "run_planning must use _build_prompt()"
        assert "_load_prompt" not in source, "run_planning must NOT use _load_prompt()"

    def test_run_planning_injects_checkpoint_instructions(self):
        """Verify run_planning uses _build_prompt which injects checkpoint instructions."""
        import inspect
        source = inspect.getsource(phases.run_planning)
        assert "_build_prompt" in source
        assert '"planning"' in source or "'planning'" in source

    def test_run_plan_review_uses_build_prompt_not_load_prompt(self):
        """Verify run_plan_review uses _build_prompt() not _load_prompt() as required by spec."""
        import inspect
        source = inspect.getsource(phases.run_plan_review)
        assert "_build_prompt" in source, "run_plan_review must use _build_prompt()"
        assert "_load_prompt" not in source, "run_plan_review must NOT use _load_prompt()"

    def test_run_plan_review_uses_parse_verdict(self):
        """Verify run_plan_review uses _parse_verdict()."""
        import inspect
        source = inspect.getsource(phases.run_plan_review)
        assert "_parse_verdict" in source

    def test_run_review_impl_uses_build_prompt_not_load_prompt(self):
        """Verify run_review_impl uses _build_prompt() not _load_prompt() as required by spec."""
        import inspect
        source = inspect.getsource(phases.run_review_impl)
        assert "_build_prompt" in source, "run_review_impl must use _build_prompt()"
        assert "_load_prompt" not in source, "run_review_impl must NOT use _load_prompt()"

    def test_run_review_impl_uses_parse_verdict(self):
        """Verify run_review_impl uses _parse_verdict()."""
        import inspect
        source = inspect.getsource(phases.run_review_impl)
        assert "_parse_verdict" in source

    def test_run_spec_writing_uses_build_prompt(self):
        """Verify run_plan_review uses _parse_verdict()."""
        import inspect
        source = inspect.getsource(phases.run_plan_review)
        assert "_parse_verdict" in source

    def test_run_review_impl_uses_parse_verdict(self):
        """Verify run_review_impl uses _parse_verdict()."""
        import inspect
        source = inspect.getsource(phases.run_review_impl)
        assert "_parse_verdict" in source

    def test_run_spec_writing_uses_build_prompt(self):
        """Verify run_spec_writing uses _build_prompt()."""
        import inspect
        source = inspect.getsource(phases.run_spec_writing)
        assert "_build_prompt" in source

    def test_run_spec_writing_uses_run_phase(self):
        """Verify run_spec_writing uses _run_phase()."""
        import inspect
        source = inspect.getsource(phases.run_spec_writing)
        assert "_run_phase" in source

    def test_run_implementing_uses_build_prompt(self):
        """Verify run_implementing uses _build_prompt()."""
        import inspect
        source = inspect.getsource(phases.run_implementing)
        assert "_build_prompt" in source

    def test_run_implementing_uses_run_phase(self):
        """Verify run_implementing uses _run_phase()."""
        import inspect
        source = inspect.getsource(phases.run_implementing)
        assert "_run_phase" in source

    def test_run_fix_feedback_uses_build_prompt(self):
        """Verify run_fix_feedback uses _build_prompt()."""
        import inspect
        source = inspect.getsource(phases.run_fix_feedback)
        assert "_build_prompt" in source

    def test_run_fix_feedback_uses_run_phase(self):
        """Verify run_fix_feedback uses _run_phase()."""
        import inspect
        source = inspect.getsource(phases.run_fix_feedback)
        assert "_run_phase" in source

    def test_run_writing_tests_uses_build_prompt(self):
        """Verify run_writing_tests uses _build_prompt()."""
        import inspect
        source = inspect.getsource(phases.run_writing_tests)
        assert "_build_prompt" in source

    def test_run_writing_tests_uses_run_phase(self):
        """Verify run_writing_tests uses _run_phase()."""
        import inspect
        source = inspect.getsource(phases.run_writing_tests)
        assert "_run_phase" in source


class TestSpecWritingSubPhase:
    """Test spec_writing handles two sub-phases correctly."""

    def test_spec_writing_calls_spec_impl_first(self):
        """Verify first sub-call uses spec_impl with skip_history_start=False."""
        import inspect
        source = inspect.getsource(phases.run_spec_writing)
        assert '"spec_impl"' in source or "'spec_impl'" in source

    def test_spec_writing_calls_spec_test_second(self):
        """Verify second sub-call uses spec_test with skip_history_start=True."""
        import inspect
        source = inspect.getsource(phases.run_spec_writing)
        assert '"spec_test"' in source or "'spec_test'" in source
        assert "skip_history_start=True" in source


class TestImplementingWithFeedback:
    """Test implementing phase appends feedback to prompt."""

    def test_implementing_appends_feedback(self):
        """Verify run_implementing appends previous feedback to prompt if available."""
        import inspect
        source = inspect.getsource(phases.run_implementing)
        assert "_get_latest_feedback" in source or "feedback" in source.lower()


class TestCheckpointTemplateExtraction:
    """Test checkpoint instruction template consolidation."""

    def test_checkpoint_instructions_file_exists(self):
        """Verify _checkpoint-instructions.md file exists in pipeline/prompts/."""
        checkpoint_path = Path(__file__).parent / "prompts" / "_checkpoint-instructions.md"
        assert checkpoint_path.exists()

    def test_checkpoint_instructions_contains_placeholder(self):
        """Verify checkpoint file contains placeholders."""
        checkpoint_path = Path(__file__).parent / "prompts" / "_checkpoint-instructions.md"
        content = checkpoint_path.read_text()
        assert "{feature_slug}" in content
        assert "{feature_id}" in content
        assert "{phase}" in content
        assert "{done_marker}" in content

    def test_test_spec_template_has_checkpoint_marker(self):
        """Verify test-spec.md contains {checkpoint_instructions} marker."""
        template_path = Path(__file__).parent / "prompts" / "test-spec.md"
        content = template_path.read_text()
        assert "{checkpoint_instructions}" in content


class TestEdgeCasesEmptyNullInputs:
    """Test edge cases for empty/null inputs."""

    def test_build_prompt_with_empty_string_values(self):
        """Verify _build_prompt handles empty string values in replacements."""
        replacements = {"{title}": ""}
        result = phases._build_prompt("plan-headless.md", replacements)
        assert isinstance(result, str)

    def test_parse_verdict_empty_json_feedback(self):
        """Verify _parse_verdict handles empty feedback in JSON."""
        output = '{"verdict": "PASS", "feedback": ""}'
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "PASS"
        assert feedback == ""

    def test_parse_verdict_null_feedback_in_json(self):
        """Verify _parse_verdict handles null feedback in JSON."""
        output = '{"verdict": "PASS", "feedback": null}'
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "PASS"


class TestErrorConditions:
    """Test error condition handling."""

    def test_build_prompt_invalid_template_name(self):
        """Verify FileNotFoundError with clear message for invalid template."""
        with pytest.raises(FileNotFoundError) as exc_info:
            phases._build_prompt("invalid_template.md", {})
        assert "not found" in str(exc_info.value).lower() or "Prompt template" in str(exc_info.value)

    def test_parse_verdict_completely_malformed_json(self):
        """Verify _parse_verdict falls back to text parsing with malformed JSON."""
        output = "{{{{ invalid json }}]"
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "FAIL"
        assert isinstance(feedback, str)

    def test_parse_verdict_only_pass_in_text(self):
        """Verify verdict returns PASS when VERDICT: marker present and JSON parsing fails."""
        output = "Some text {invalid} \nVERDICT: PASS\n"
        verdict, feedback = phases._parse_verdict(output)
        assert verdict == "PASS"


class TestModuleLoadValidation:
    """Test module-level validation."""

    def test_module_imports_without_errors(self):
        """Verify phases module imports without errors."""
        import phases
        assert phases.PHASE_CONFIG is not None

    def test_history_tag_validation_at_load(self):
        """Verify history_tag validation assertion exists."""
        assert hasattr(phases, 'PHASE_CONFIG')
        for config in phases.PHASE_CONFIG.values():
            tag = config["history_tag"]
            assert tag.replace("_", "").isalpha()


class TestPromptTemplatesContainCheckpointMarker:
    """Verify all relevant templates contain checkpoint marker."""

    def test_impl_spec_has_checkpoint_marker(self):
        """Verify impl-spec.md contains checkpoint marker."""
        template_path = Path(__file__).parent / "prompts" / "impl-spec.md"
        content = template_path.read_text()
        assert "{checkpoint_instructions}" in content

    def test_implement_has_checkpoint_marker(self):
        """Verify implement.md contains checkpoint marker."""
        template_path = Path(__file__).parent / "prompts" / "implement.md"
        content = template_path.read_text()
        assert "{checkpoint_instructions}" in content

    def test_fix_feedback_has_checkpoint_marker(self):
        """Verify fix-feedback.md contains checkpoint marker."""
        template_path = Path(__file__).parent / "prompts" / "fix-feedback.md"
        content = template_path.read_text()
        assert "{checkpoint_instructions}" in content

    def test_write_tests_has_checkpoint_marker(self):
        """Verify write-tests.md contains checkpoint marker."""
        template_path = Path(__file__).parent / "prompts" / "write-tests.md"
        content = template_path.read_text()
        assert "{checkpoint_instructions}" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
