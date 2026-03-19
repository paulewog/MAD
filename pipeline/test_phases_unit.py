"""Unit tests for phases.py — Phase orchestration module."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import phases
from phases import (
    _spec_to_string,
    _build_prompt,
    _extract_json_robust,
    _parse_json_output,
    _parse_verdict,
    _strip_markdown,
    _validate_exploration_artifacts,
    _get_latest_feedback,
    PHASE_CONFIG,
    PHASE_SCHEMAS,
)


class MockFeatureFile:
    """Mock FeatureFile for testing."""
    def __init__(self, data=None):
        self._data = data or {}
        self._path = Path("/tmp/test.json")
        self.history_entries = []
        self.saved = False

    @property
    def title(self):
        return self._data.get("title", "Test Feature")

    @property
    def slug(self):
        return "test-feature"

    @property
    def board(self):
        return "default"

    @property
    def current_stage(self):
        return "ideas"

    @property
    def plan(self):
        return self._data.get("plan", "")

    @property
    def impl_spec(self):
        return self._data.get("impl_spec", "")

    @property
    def test_spec(self):
        return self._data.get("test_spec", "")

    @property
    def plan_reviews(self):
        return self._data.get("plan_reviews", [])

    @property
    def impl_reviews(self):
        return self._data.get("impl_reviews", [])

    @property
    def spec_reviews(self):
        return self._data.get("spec_reviews", [])

    @property
    def ideation_prompt(self):
        return self._data.get("ideation_prompt", "")

    def get_latest_plan_review(self):
        reviews = self._data.get("plan_reviews", [])
        return reviews[-1] if reviews else None

    def get_latest_impl_review(self):
        reviews = self._data.get("impl_reviews", [])
        return reviews[-1] if reviews else None

    def get_latest_spec_review(self):
        reviews = self._data.get("spec_reviews", [])
        return reviews[-1] if reviews else None

    def add_history(self, stage, note):
        self.history_entries.append({"stage": stage, "note": note})

    def save(self):
        self.saved = True


class TestSpecToString(unittest.TestCase):
    """Tests for _spec_to_string function."""

    def test_spec_to_string_dict(self):
        """dict → JSON string."""
        result = _spec_to_string({"key": "value"})
        self.assertIn("key", result)
        self.assertIn("value", result)

    def test_spec_to_string_str(self):
        """string → same string."""
        result = _spec_to_string("plain string")
        self.assertEqual(result, "plain string")

    def test_spec_to_string_empty(self):
        """Empty string returns empty string."""
        result = _spec_to_string("")
        self.assertEqual(result, "")

    def test_spec_to_string_none(self):
        """None returns empty string."""
        result = _spec_to_string(None)
        self.assertEqual(result, "")


class TestBuildPrompt(unittest.TestCase):
    """Tests for _build_prompt function."""

    def setUp(self):
        self.prompts_dir = Path("/tmp/test-prompts")
        self.prompts_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.prompts_dir.exists():
            shutil.rmtree(self.prompts_dir)

    def test_build_prompt_loads_template(self):
        """Loads from prompts/ dir."""
        test_file = self.prompts_dir / "test.md"
        test_file.write_text("Hello {title}")

        with patch('phases.PROMPTS_DIR', self.prompts_dir):
            result = _build_prompt("test.md", {"{title}": "World"})

        self.assertIn("World", result)

    def test_build_prompt_variable_substitution(self):
        """{title} etc. replaced."""
        test_file = self.prompts_dir / "test.md"
        test_file.write_text("Feature: {title}\nBoard: {board}")

        with patch('phases.PROMPTS_DIR', self.prompts_dir):
            result = _build_prompt("test.md", {"{title}": "My Feature", "{board}": "default"})

        self.assertIn("My Feature", result)
        self.assertIn("default", result)

    def test_build_prompt_missing_template_raises(self):
        """FileNotFoundError."""
        with patch('phases.PROMPTS_DIR', self.prompts_dir):
            with self.assertRaises(FileNotFoundError):
                _build_prompt("nonexistent.md", {})

    def test_build_prompt_checkpoint_injection(self):
        """{checkpoint_instructions} replaced from _checkpoint-instructions.md."""
        test_file = self.prompts_dir / "test.md"
        test_file.write_text("Start\n{checkpoint_instructions}\nEnd")

        checkpoint_file = self.prompts_dir / "_checkpoint-instructions.md"
        checkpoint_file.write_text("Resume instructions here")

        with patch('phases.PROMPTS_DIR', self.prompts_dir):
            result = _build_prompt("test.md", {})

        self.assertIn("Resume instructions here", result)


class TestExtractJsonRobust(unittest.TestCase):
    """Tests for _extract_json_robust function."""

    def test_extract_single_json(self):
        """'{"key": "val"}' → one candidate."""
        result = _extract_json_robust('{"key": "val"}')
        self.assertEqual(len(result), 1)

    def test_extract_nested_json(self):
        """Nested braces handled correctly."""
        result = _extract_json_robust('{"outer": {"inner": "value"}}')
        self.assertEqual(len(result), 1)

    def test_extract_json_in_text(self):
        """JSON embedded in prose extracted."""
        result = _extract_json_robust('Some text {"key": "val"} more text')
        self.assertEqual(len(result), 1)

    def test_extract_multiple_json(self):
        """Two JSON objects → two candidates."""
        result = _extract_json_robust('{"a": 1} and {"b": 2}')
        self.assertEqual(len(result), 2)

    def test_extract_json_with_escaped_braces(self):
        """Escaped braces in strings don't break counting."""
        result = _extract_json_robust('{"text": "has {curly} in it"}')
        self.assertEqual(len(result), 1)

    def test_extract_empty_input(self):
        """Empty → []."""
        result = _extract_json_robust("")
        self.assertEqual(result, [])

    def test_extract_no_json(self):
        """'just text' → []."""
        result = _extract_json_robust("just text")
        self.assertEqual(result, [])

    def test_extract_unclosed_brace(self):
        """Unmatched { → no candidate."""
        result = _extract_json_robust("text { incomplete")
        self.assertEqual(result, [])


class TestParseJsonOutput(unittest.TestCase):
    """Tests for _parse_json_output function."""

    def test_parse_direct_json(self):
        """Direct JSON with field → field value."""
        result = _parse_json_output('{"implementation_spec": "the spec"}', "implementation_spec")
        self.assertEqual(result, "the spec")

    def test_parse_json5_relaxed(self):
        """Trailing commas, single quotes accepted."""
        result = _parse_json_output("{implementation_spec: 'the spec',}", "implementation_spec")
        self.assertEqual(result, "the spec")

    def test_parse_json_embedded_in_text(self):
        """JSON block in prose → extracted."""
        result = _parse_json_output('Here is the spec: {"implementation_spec": "the spec"}', "implementation_spec")
        self.assertEqual(result, "the spec")

    def test_parse_json_multiple_blocks_uses_last(self):
        """Last candidate with field wins."""
        result = _parse_json_output('{"impl": "first"} {"implementation_spec": "second"}', "implementation_spec")
        self.assertEqual(result, "second")

    def test_parse_json_field_missing(self):
        """Field not in JSON → empty string."""
        result = _parse_json_output('{"other": "value"}', "implementation_spec")
        self.assertEqual(result, "")

    def test_parse_json_empty_output(self):
        """Empty → empty string."""
        result = _parse_json_output("", "implementation_spec")
        self.assertEqual(result, "")


class TestParseVerdict(unittest.TestCase):
    """Tests for _parse_verdict function."""

    def test_verdict_json_pass(self):
        """{"verdict": "PASS", "summary": "...", "feedback": null} → ("PASS", summary, "")."""
        verdict, summary, feedback = _parse_json_verdict('{"verdict": "PASS", "summary": "Looks good", "feedback": null}')
        self.assertEqual(verdict, "PASS")
        self.assertEqual(summary, "Looks good")
        self.assertEqual(feedback, "")

    def test_verdict_json_fail(self):
        """{"verdict": "FAIL", "summary": "...", "feedback": "fix X"} → ("FAIL", summary, feedback)."""
        verdict, summary, feedback = _parse_json_verdict('{"verdict": "FAIL", "summary": "Needs work", "feedback": "fix X"}')
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(summary, "Needs work")
        self.assertEqual(feedback, "fix X")

    def test_verdict_json_case_insensitive(self):
        """'pass' → 'PASS'."""
        verdict, summary, feedback = _parse_json_verdict('{"verdict": "pass", "summary": "ok"}')
        self.assertEqual(verdict, "PASS")

    def test_verdict_json_missing_summary(self):
        """summary=None → default 'Review PASS - no summary provided'."""
        verdict, summary, feedback = _parse_json_verdict('{"verdict": "PASS", "summary": null}')
        self.assertIn("no summary", summary.lower())

    def test_verdict_json_summary_truncation(self):
        """summary > 500 chars truncated with '...'."""
        long_summary = "A" * 600
        verdict, summary, feedback = _parse_json_verdict(f'{{"verdict": "PASS", "summary": "{long_summary}"}}')
        self.assertEqual(len(summary), 500)
        self.assertTrue(summary.endswith("..."))

    def test_verdict_json_test_results_pass(self):
        """{"test_results": {"failed": 0}} → 'PASS'."""
        verdict, summary, feedback = _parse_json_verdict('{"test_results": {"failed": 0}}')
        self.assertEqual(verdict, "PASS")

    def test_verdict_json_test_results_fail(self):
        """{"test_results": {"failed": 2}} → 'FAIL'."""
        verdict, summary, feedback = _parse_json_verdict('{"test_results": {"failed": 2}}')
        self.assertEqual(verdict, "FAIL")

    def test_verdict_text_fallback(self):
        """'VERDICT: PASS\\nSUMMARY: looks good' → text parsing."""
        verdict, summary, feedback = _parse_json_verdict("VERDICT: PASS\nSUMMARY: looks good")
        self.assertEqual(verdict, "PASS")

    def test_verdict_text_fail_with_feedback(self):
        """'VERDICT: FAIL\\nFEEDBACK: fix this' → extracts feedback."""
        verdict, summary, feedback = _parse_json_verdict("VERDICT: FAIL\nFEEDBACK: fix this")
        self.assertEqual(verdict, "FAIL")
        self.assertIn("fix this", feedback)

    def test_verdict_plan_shaped_rejected(self):
        """{"plan": "..."} without verdict → system error feedback."""
        verdict, summary, feedback = _parse_json_verdict('{"plan": "some plan content"}')
        self.assertEqual(verdict, "FAIL")
        self.assertIn("SYSTEM ERROR", feedback)

    def test_verdict_json_embedded_in_text(self):
        """JSON verdict in prose → parsed correctly."""
        verdict, summary, feedback = _parse_json_verdict("Here is my review: {\"verdict\": \"PASS\", \"summary\": \"looks good\"}")
        self.assertEqual(verdict, "PASS")


def _parse_json_verdict(output):
    """Helper to call the internal function."""
    return _parse_verdict(output)


class TestStripMarkdown(unittest.TestCase):
    """Tests for _strip_markdown function."""

    def test_strip_bold(self):
        """**bold** → bold."""
        result = _strip_markdown("**bold**")
        self.assertEqual(result, "bold")

    def test_strip_headers(self):
        """# Header → Header."""
        result = _strip_markdown("# Header")
        self.assertEqual(result, "Header")

    def test_strip_code_blocks(self):
        """Code blocks removed entirely (fences + content)."""
        result = _strip_markdown("```\ncode\n```")
        self.assertEqual(result, "")

    def test_strip_inline_code(self):
        """`code` → code."""
        result = _strip_markdown("`code`")
        self.assertEqual(result, "code")

    def test_strip_bullets(self):
        """- item → item."""
        result = _strip_markdown("- item")
        self.assertEqual(result, "item")

    def test_strip_numbered_lists(self):
        """1. item → item."""
        result = _strip_markdown("1. item")
        self.assertEqual(result, "item")


class TestValidateExplorationArtifacts(unittest.TestCase):
    """Tests for _validate_exploration_artifacts function."""

    def test_validate_empty_output(self):
        """Empty → (True, '')."""
        result = _validate_exploration_artifacts("")
        self.assertEqual(result, (True, ""))

    def test_validate_no_questions(self):
        """Output without questions → (True, '')."""
        result = _validate_exploration_artifacts('{"plan": "some plan"}')
        self.assertEqual(result, (True, ""))

    def test_validate_with_exploration_summary(self):
        """Questions + exploration_summary → (True, '')."""
        result = _validate_exploration_artifacts('{"questions": [{"question": "Q?"}], "exploration_summary": "explored the code"}')
        self.assertEqual(result, (True, ""))

    def test_validate_with_indicator_phrases(self):
        """Questions + exploration phrases → (True, '')."""
        result = _validate_exploration_artifacts('{"questions": [{"question": "Q?"}]}\n\nI searched for the relevant files.')
        self.assertEqual(result, (True, ""))


class TestGetLatestFeedback(unittest.TestCase):
    """Tests for _get_latest_feedback function."""

    def test_feedback_from_impl_review(self):
        """Latest FAIL impl_review → its feedback."""
        feature = MockFeatureFile({
            "impl_reviews": [
                {"verdict": "FAIL", "feedback": "Fix the impl"}
            ]
        })
        result = _get_latest_feedback(feature)
        self.assertEqual(result, "Fix the impl")

    def test_feedback_from_plan_review(self):
        """Latest FAIL plan_review → its feedback."""
        feature = MockFeatureFile({
            "plan_reviews": [
                {"verdict": "FAIL", "feedback": "Fix the plan"}
            ]
        })
        result = _get_latest_feedback(feature)
        self.assertEqual(result, "Fix the plan")

    def test_feedback_no_reviews(self):
        """No reviews → 'No previous feedback available.'."""
        feature = MockFeatureFile()
        result = _get_latest_feedback(feature)
        self.assertEqual(result, "No previous feedback available.")

    def test_feedback_pass_review(self):
        """Latest PASS review → falls through."""
        feature = MockFeatureFile({
            "impl_reviews": [
                {"verdict": "PASS", "feedback": ""},
                {"verdict": "FAIL", "feedback": "Fix it"}
            ]
        })
        result = _get_latest_feedback(feature)
        self.assertEqual(result, "Fix it")


class TestPhaseConfig(unittest.TestCase):
    """Tests for PHASE_CONFIG and PHASE_SCHEMAS."""

    def test_phase_config_keys_match_schemas(self):
        """All keys in PHASE_SCHEMAS exist in PHASE_CONFIG."""
        for key in PHASE_SCHEMAS:
            self.assertIn(key, PHASE_CONFIG)

    def test_phase_config_has_required_fields(self):
        """Each config has runner_phase, history_tag, status_label, template."""
        for key, config in PHASE_CONFIG.items():
            self.assertIn("runner_phase", config)
            self.assertIn("history_tag", config)
            self.assertIn("status_label", config)
            self.assertIn("template", config)

    def test_phase_schemas_valid_jsonschema(self):
        """Each schema is valid JSON Schema."""
        import jsonschema
        for key, schema in PHASE_SCHEMAS.items():
            try:
                jsonschema.Draft7Validator.check_schema(schema)
            except jsonschema.SchemaError as e:
                self.fail(f"Invalid schema for {key}: {e}")


class TestIdeationPromptInjection(unittest.TestCase):
    """Tests for ideation_prompt injection in run_ideating."""

    def setUp(self):
        self.prompts_dir = Path("/tmp/test-prompts")
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        test_file = self.prompts_dir / "ideating.md"
        test_file.write_text("""# Ideation: {title}

**Description:** {description}

{previous_ideation}

{ideation_prompt_section}

---

## Task

You are participating in an ideation session.

## Your Role

{role_instruction}
""")

    def tearDown(self):
        import shutil
        if self.prompts_dir.exists():
            shutil.rmtree(self.prompts_dir)

    def test_ideation_prompt_injected_when_set(self):
        """When ideation_prompt is set, User Direction section appears."""
        with patch('phases.PROMPTS_DIR', self.prompts_dir):
            feature = MockFeatureFile({"ideation_prompt": "Focus on security"})
            prompt = _build_prompt("ideating.md", {
                "{title}": "Test Feature",
                "{description}": "A test",
                "{previous_ideation}": "",
                "{ideation_prompt_section}": "\n\n## User Direction\nFocus on security\n\nThe above user direction should guide your ideation — prioritize it over default assumptions, but still fulfill your assigned role below.",
                "{role_instruction}": "You are a reviewer",
            })
        self.assertIn("## User Direction", prompt)
        self.assertIn("Focus on security", prompt)
        self.assertIn("prioritize it over default assumptions", prompt)

    def test_ideation_prompt_not_injected_when_empty(self):
        """When ideation_prompt is empty, no User Direction section."""
        with patch('phases.PROMPTS_DIR', self.prompts_dir):
            feature = MockFeatureFile({"ideation_prompt": ""})
            prompt = _build_prompt("ideating.md", {
                "{title}": "Test Feature",
                "{description}": "A test",
                "{previous_ideation}": "",
                "{ideation_prompt_section}": "",
                "{role_instruction}": "You are a reviewer",
            })
        self.assertNotIn("## User Direction", prompt)
        self.assertNotIn("Focus on security", prompt)

    def test_ideation_prompt_section_placeholder_present(self):
        """Template contains {ideation_prompt_section} placeholder."""
        template = (self.prompts_dir / "ideating.md").read_text()
        self.assertIn("{ideation_prompt_section}", template)


class TestIdeationSummaryStorageTruncationRemoved(unittest.TestCase):
    """Tests verifying ideation summary truncation has been removed (storage level)."""

    def _create_mock_feature(self):
        """Create a mock FeatureFile with all required properties for run_ideating."""
        from unittest.mock import MagicMock
        
        feature = MagicMock()
        feature.title = "Test Feature"
        feature.description = "Test description"
        feature.slug = "test-feature"
        feature.board = "default"
        feature.current_stage = "ideas"
        feature.Ideation = ""
        feature.ideation_prompt = ""
        feature.ideation_max_rounds = None
        feature.ideation_summaries = []
        feature.ideation_cycle_verdicts = []
        
        return feature

    def _create_mock_runner(self, output):
        """Create a mock AgentRunner that returns the given output."""
        from unittest.mock import MagicMock
        
        runner = MagicMock()
        mock_round_runner = MagicMock()
        mock_round_runner.headless.return_value = str(output)
        runner.for_cli_model.return_value = mock_round_runner
        runner.headless.return_value = str(output)
        
        return runner

    @patch('phases.Config')
    @patch('phases.AgentRunner')
    @patch('pathlib.Path.mkdir')
    @patch('pathlib.Path.write_text')
    def test_summary_json_path_long_summary_stored_in_full(self, mock_write_text, mock_mkdir, mock_agent_runner_class, mock_config_class):
        """Summary >500 chars from JSON is stored in full without slicing."""
        import json
        
        long_summary = "A" * 600
        output = json.dumps({"summary": long_summary})
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [MagicMock()]
        mock_config.ideating_max_rounds = 1
        mock_config.boards_dir = Path("/tmp/boards")
        mock_config.mad_dir = Path("/tmp/mad")
        mock_config_class.return_value = mock_config
        
        feature = self._create_mock_feature()
        runner = self._create_mock_runner(output)
        
        phases.run_ideating(feature, runner)
        
        feature.add_ideation_summary.assert_called()
        stored_summary = feature.add_ideation_summary.call_args[0][0]
        self.assertEqual(len(stored_summary), 600)
        self.assertEqual(stored_summary, long_summary)

    @patch('phases.Config')
    @patch('phases.AgentRunner')
    @patch('pathlib.Path.mkdir')
    @patch('pathlib.Path.write_text')
    def test_summary_fallback_path_long_raw_output_stored_in_full(self, mock_write_text, mock_mkdir, mock_agent_runner_class, mock_config_class):
        """Raw output >500 chars (non-JSON) stored in full without slicing."""
        long_output = "B" * 600
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [MagicMock()]
        mock_config.ideating_max_rounds = 1
        mock_config.boards_dir = Path("/tmp/boards")
        mock_config.mad_dir = Path("/tmp/mad")
        mock_config_class.return_value = mock_config
        
        feature = self._create_mock_feature()
        runner = self._create_mock_runner(long_output)
        
        phases.run_ideating(feature, runner)
        
        feature.add_ideation_summary.assert_called()
        stored_summary = feature.add_ideation_summary.call_args[0][0]
        self.assertEqual(len(stored_summary), 600)
        self.assertEqual(stored_summary, long_output)

    @patch('phases.Config')
    @patch('phases.AgentRunner')
    @patch('pathlib.Path.mkdir')
    @patch('pathlib.Path.write_text')
    def test_summary_json_path_short_summary_stored_as_is(self, mock_write_text, mock_mkdir, mock_agent_runner_class, mock_config_class):
        """Short summary (<300 chars) stored as-is."""
        import json
        
        short_summary = "C" * 100
        output = json.dumps({"summary": short_summary})
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [MagicMock()]
        mock_config.ideating_max_rounds = 1
        mock_config.boards_dir = Path("/tmp/boards")
        mock_config.mad_dir = Path("/tmp/mad")
        mock_config_class.return_value = mock_config
        
        feature = self._create_mock_feature()
        runner = self._create_mock_runner(output)
        
        phases.run_ideating(feature, runner)
        
        feature.add_ideation_summary.assert_called()
        stored_summary = feature.add_ideation_summary.call_args[0][0]
        self.assertEqual(len(stored_summary), 100)
        self.assertEqual(stored_summary, short_summary)


class TestTerminalStagePipeline(unittest.TestCase):
    """Tests for terminal_stage feature in run_pipeline."""

    def _create_mock_feature(self, board="default", current_stage="reviewing-plan",
                             requires_human_approval=False):
        """Create a mock FeatureFile for testing pipeline behavior."""
        from unittest.mock import MagicMock
        
        feature = MagicMock()
        feature.title = "Test Feature"
        feature.slug = "test-feature"
        feature.board = board
        feature.requires_human_approval = requires_human_approval
        feature.plan_reviews = []
        feature.spec_reviews = []
        feature.history_entries = []
        feature.plan = "test plan"
        feature.impl_spec = "test spec"
        feature.test_spec = "test test spec"
        
        stage_storage = [current_stage]
        
        def move_to_stage(stage):
            stage_storage[0] = stage
            feature.move_to_stage_calls.append(stage)
        
        def add_history(stage, note):
            feature.history_entries.append({"stage": stage, "note": note})
            feature.add_history_calls.append((stage, note))
        
        feature.move_to_stage_calls = []
        feature.add_history_calls = []
        
        feature.move_to_stage = MagicMock(side_effect=move_to_stage)
        feature.add_history = MagicMock(side_effect=add_history)
        feature.save = MagicMock()
        
        type(feature).current_stage = property(lambda self: stage_storage[0])
        
        return feature

    @patch('phases.run_plan_review')
    @patch('phases.run_spec_writing')
    @patch('phases.run_implementing')
    @patch('phases._git_commit')
    @patch('phases.Config')
    def test_terminal_stage_plan_stops_after_plan_review(
            self, mock_config_class, mock_git_commit, mock_run_implementing,
            mock_run_spec_writing, mock_run_plan_review):
        """spec 1.8: terminal_stage='plan' stops after plan review passes."""
        mock_feature = self._create_mock_feature(board="planning", current_stage="reviewing-plan")
        
        mock_config = MagicMock()
        mock_config.get_terminal_stage.return_value = "plan"
        mock_config_class.return_value = mock_config
        
        mock_run_plan_review.return_value = ("PASS", "")
        
        from phases import _run_pipeline_impl
        from runner import AgentRunner
        runner = MagicMock(spec=AgentRunner)
        
        _run_pipeline_impl(mock_feature, runner)
        
        self.assertEqual(mock_feature.move_to_stage.call_count, 1)
        final_call = mock_feature.move_to_stage.call_args[0][0]
        self.assertEqual(final_call, "final-human-approval")
        
        history_calls = mock_feature.add_history.call_args_list
        terminal_history = [c for c in history_calls if c[0][0] == "TERMINAL_STAGE"]
        self.assertEqual(len(terminal_history), 1)
        self.assertIn("plan", terminal_history[0][0][1])
        self.assertIn("final-human-approval", terminal_history[0][0][1])
        
        mock_run_spec_writing.assert_not_called()
        mock_run_implementing.assert_not_called()

    @patch('phases.run_plan_review')
    @patch('phases.run_spec_writing')
    @patch('phases.run_spec_review')
    @patch('phases.run_implementing')
    @patch('phases._git_commit')
    @patch('phases.Config')
    def test_terminal_stage_spec_stops_after_spec_review(
            self, mock_config_class, mock_git_commit, mock_run_implementing,
            mock_run_spec_review, mock_run_spec_writing, mock_run_plan_review):
        """spec 1.9: terminal_stage='spec' stops after spec review passes."""
        mock_feature = self._create_mock_feature(board="spec", current_stage="approved")
        
        mock_config = MagicMock()
        mock_config.get_terminal_stage.return_value = "spec"
        mock_config_class.return_value = mock_config
        
        mock_run_plan_review.return_value = ("PASS", "")
        mock_run_spec_review.return_value = ("PASS", "")
        
        from phases import _run_pipeline_impl
        from runner import AgentRunner
        runner = MagicMock(spec=AgentRunner)
        
        _run_pipeline_impl(mock_feature, runner)
        
        self.assertEqual(mock_feature.move_to_stage.call_count, 1)
        final_call = mock_feature.move_to_stage.call_args[0][0]
        self.assertEqual(final_call, "final-human-approval")
        
        history_calls = mock_feature.add_history.call_args_list
        terminal_history = [c for c in history_calls if c[0][0] == "TERMINAL_STAGE"]
        self.assertEqual(len(terminal_history), 1)
        self.assertIn("spec", terminal_history[0][0][1])
        self.assertIn("final-human-approval", terminal_history[0][0][1])
        
        mock_run_spec_writing.assert_called_once()
        mock_run_implementing.assert_not_called()

    @patch('phases.run_plan_review')
    @patch('phases.run_spec_writing')
    @patch('phases.run_spec_review')
    @patch('phases.run_implementing')
    @patch('phases.run_verify_tests')
    @patch('phases.run_review_impl')
    @patch('phases._git_commit')
    @patch('phases.Config')
    def test_no_terminal_stage_runs_full_pipeline(
            self, mock_config_class, mock_git_commit, mock_run_review_impl,
            mock_run_verify_tests, mock_run_implementing, mock_run_spec_review,
            mock_run_spec_writing, mock_run_plan_review):
        """spec 1.10: No terminal_stage runs full pipeline."""
        mock_feature = self._create_mock_feature(board="default", current_stage="reviewing-plan")
        
        mock_config = MagicMock()
        mock_config.get_terminal_stage.return_value = None
        mock_config_class.return_value = mock_config
        
        mock_run_plan_review.return_value = ("PASS", "")
        mock_run_spec_review.return_value = ("PASS", "")
        mock_run_verify_tests.return_value = ("PASS", "")
        mock_run_review_impl.return_value = ("PASS", "")
        
        from phases import _run_pipeline_impl
        from runner import AgentRunner
        runner = MagicMock(spec=AgentRunner)
        
        _run_pipeline_impl(mock_feature, runner)
        
        mock_run_plan_review.assert_called()
        mock_run_spec_writing.assert_called()
        mock_run_spec_review.assert_called()
        mock_run_implementing.assert_called()
        
        history_calls = mock_feature.add_history.call_args_list
        terminal_history = [c for c in history_calls if c[0][0] == "TERMINAL_STAGE"]
        self.assertEqual(len(terminal_history), 0)

    @patch('phases.run_plan_review')
    @patch('phases.run_spec_writing')
    @patch('phases._git_commit')
    @patch('phases.Config')
    def test_stage_guard_prevents_spec_fallthrough(
            self, mock_config_class, mock_git_commit,
            mock_run_spec_writing, mock_run_plan_review):
        """spec 1.11: Stage guard prevents spec writing when requires_human_approval."""
        mock_feature = self._create_mock_feature(
            board="default", current_stage="reviewing-plan", requires_human_approval=True)
        
        mock_config = MagicMock()
        mock_config.get_terminal_stage.return_value = None
        mock_config_class.return_value = mock_config
        
        mock_run_plan_review.return_value = ("PASS", "")
        
        from phases import _run_pipeline_impl
        from runner import AgentRunner
        runner = MagicMock(spec=AgentRunner)
        
        _run_pipeline_impl(mock_feature, runner)
        
        self.assertEqual(mock_feature.move_to_stage.call_count, 1)
        final_call = mock_feature.move_to_stage.call_args[0][0]
        self.assertEqual(final_call, "awaiting-human-approval")
        
        history_calls = mock_feature.add_history.call_args_list
        approval_history = [c for c in history_calls if c[0][0] == "AWAITING_HUMAN_APPROVAL"]
        self.assertEqual(len(approval_history), 1)
        
        mock_run_spec_writing.assert_not_called()

    @patch('phases.run_plan_review')
    @patch('phases.run_spec_writing')
    @patch('phases.run_implementing')
    @patch('phases._git_commit')
    @patch('phases.Config')
    def test_terminal_stage_plan_priority_over_human_approval(
            self, mock_config_class, mock_git_commit, mock_run_implementing,
            mock_run_spec_writing, mock_run_plan_review):
        """spec 1.12: terminal_stage='plan' takes priority over requires_human_approval."""
        mock_feature = self._create_mock_feature(
            board="planning", current_stage="reviewing-plan", requires_human_approval=True)
        
        mock_config = MagicMock()
        mock_config.get_terminal_stage.return_value = "plan"
        mock_config_class.return_value = mock_config
        
        mock_run_plan_review.return_value = ("PASS", "")
        
        from phases import _run_pipeline_impl
        from runner import AgentRunner
        runner = MagicMock(spec=AgentRunner)
        
        _run_pipeline_impl(mock_feature, runner)
        
        final_call = mock_feature.move_to_stage.call_args[0][0]
        self.assertEqual(final_call, "final-human-approval")
        
        history_calls = mock_feature.add_history.call_args_list
        terminal_history = [c for c in history_calls if c[0][0] == "TERMINAL_STAGE"]
        self.assertEqual(len(terminal_history), 1)
        
        approval_history = [c for c in history_calls if c[0][0] == "AWAITING_HUMAN_APPROVAL"]
        self.assertEqual(len(approval_history), 0)
        
        mock_run_spec_writing.assert_not_called()
        mock_run_implementing.assert_not_called()

    @patch('phases.run_plan_review')
    @patch('phases.run_spec_writing')
    @patch('phases.run_spec_review')
    @patch('phases.run_implementing')
    @patch('phases._git_commit')
    @patch('phases.Config')
    def test_terminal_stage_spec_plan_review_normal(
            self, mock_config_class, mock_git_commit, mock_run_implementing,
            mock_run_spec_review, mock_run_spec_writing, mock_run_plan_review):
        """spec 1.13: terminal_stage='spec' allows plan review to proceed normally."""
        mock_feature = self._create_mock_feature(board="spec", current_stage="reviewing-plan")
        
        mock_config = MagicMock()
        mock_config.get_terminal_stage.return_value = "spec"
        mock_config_class.return_value = mock_config
        
        mock_run_plan_review.return_value = ("PASS", "")
        mock_run_spec_review.return_value = ("PASS", "")
        
        from phases import _run_pipeline_impl
        from runner import AgentRunner
        runner = MagicMock(spec=AgentRunner)
        
        _run_pipeline_impl(mock_feature, runner)
        
        mock_run_plan_review.assert_called_once()
        
        mock_run_spec_writing.assert_called_once()
        mock_run_spec_review.assert_called_once()
        
        final_call = mock_feature.move_to_stage.call_args[0][0]
        self.assertEqual(final_call, "final-human-approval")

    @patch('phases.run_plan_review')
    @patch('phases.run_spec_writing')
    @patch('phases.run_implementing')
    @patch('phases._git_commit')
    @patch('phases.Config')
    def test_terminal_stage_history_entries(
            self, mock_config_class, mock_git_commit, mock_run_implementing,
            mock_run_spec_writing, mock_run_plan_review):
        """spec 1.14: Verify TERMINAL_STAGE history entries are descriptive."""
        mock_feature = self._create_mock_feature(board="planning", current_stage="reviewing-plan")
        
        mock_config = MagicMock()
        mock_config.get_terminal_stage.return_value = "plan"
        mock_config_class.return_value = mock_config
        
        mock_run_plan_review.return_value = ("PASS", "")
        
        from phases import _run_pipeline_impl
        from runner import AgentRunner
        runner = MagicMock(spec=AgentRunner)
        
        _run_pipeline_impl(mock_feature, runner)
        
        history_calls = mock_feature.add_history.call_args_list
        terminal_history = [c for c in history_calls if c[0][0] == "TERMINAL_STAGE"]
        
        self.assertEqual(len(terminal_history), 1)
        tag, message = terminal_history[0][0]
        
        self.assertEqual(tag, "TERMINAL_STAGE")
        self.assertIn("plan", message)
        self.assertIn("final-human-approval", message)


if __name__ == '__main__':
    unittest.main()
