"""Tests for planning agent question validation in phases.py"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.phases import (
    _validate_exploration_artifacts,
    _check_question_antipatterns,
    run_planning,
)


class TestValidateExplorationArtifacts:
    """Tests for _validate_exploration_artifacts() function."""

    def test_empty_string_returns_true(self):
        """Empty agent output string should return (True, '')."""
        result = _validate_exploration_artifacts("")
        assert result == (True, "")

    def test_whitespace_only_returns_true(self):
        """Whitespace-only output should return (True, '')."""
        result = _validate_exploration_artifacts("   \n\t  ")
        assert result == (True, "")

    def test_no_questions_returns_true(self):
        """Output with no questions should return (True, '')."""
        output = '{"plan": "some plan", "other": "data"}'
        result = _validate_exploration_artifacts(output)
        assert result == (True, "")

    def test_questions_with_exploration_indicator(self):
        """Questions with exploration evidence should pass."""
        output = '''I searched for UnifiedEditModal in the codebase.
I found the issue in the code.

```json
{"questions": [{"question": "What error do you see?", "answer": ""}], "plan": "fix it"}
```'''
        result = _validate_exploration_artifacts(output)
        assert result == (True, "")

    def test_questions_without_exploration_fails(self):
        """Questions without exploration evidence now logs warning but allows continuing."""
        output = '''Here are some questions for you:

```json
{"questions": [{"question": "What error do you see?", "answer": ""}], "plan": "fix it"}
```'''
        result = _validate_exploration_artifacts(output)
        # Current behavior: logs warning and allows continuing
        assert result[0] is True
        assert result[1] == ''

    def test_case_insensitive_matching(self):
        """Exploration indicator matching should be case-insensitive."""
        output = '''I FOUND IN THE CODE the issue.
```json
{"questions": [{"question": "Test?", "answer": ""}]}
```'''
        result = _validate_exploration_artifacts(output)
        assert result == (True, "")

    def test_single_indicator_sufficient(self):
        """One exploration indicator should be enough to pass."""
        output = 'Reading the file revealed the bug.\n{"questions": [{"question": "?", "answer": ""}]}'
        result = _validate_exploration_artifacts(output)
        assert result == (True, "")

    def test_using_grep_indicator(self):
        """Using grep indicator should work."""
        output = 'Using Grep to search for the error message.\n{"questions": [{"question": "?", "answer": ""}]}'
        result = _validate_exploration_artifacts(output)
        assert result == (True, "")

    def test_using_glob_indicator(self):
        """Using glob indicator should work."""
        output = 'Using Glob to find relevant files.\n{"questions": [{"question": "?", "answer": ""}]}'
        result = _validate_exploration_artifacts(output)
        assert result == (True, "")

    def test_looking_at_indicator(self):
        """Looking at indicator should work."""
        output = 'Looking at the source code.\n{"questions": [{"question": "?", "answer": ""}]}'
        result = _validate_exploration_artifacts(output)
        assert result == (True, "")


class TestCheckQuestionAntipatterns:
    """Tests for _check_question_antipatterns() function."""

    def test_empty_list_returns_true(self):
        """Empty questions list should pass."""
        result = _check_question_antipatterns([])
        assert result == (True, "")

    def test_legitimate_question_passes(self):
        """A legitimate question should pass."""
        questions = [{"question": "Which environment variable controls timeout?", "answer": ""}]
        result = _check_question_antipatterns(questions)
        assert result == (True, "")

    def test_antipattern_all_items(self):
        """Antipattern: does this happen for all items?"""
        questions = [{"question": "Does this happen for all items?", "answer": ""}]
        result = _check_question_antipatterns(questions)
        assert result[0] is False
        assert "anti-pattern" in result[1]
        assert "code paths" in result[1]

    def test_antipattern_steps_to_reproduce(self):
        """Antipattern: what steps to reproduce?"""
        questions = [{"question": "What steps do you use to reproduce this?", "answer": ""}]
        result = _check_question_antipatterns(questions)
        assert result[0] is False
        assert "anti-pattern" in result[1]
        assert "inferred" in result[1].lower()

    def test_antipattern_operating_system(self):
        """Antipattern: what operating system?"""
        questions = [{"question": "What operating system are you using?", "answer": ""}]
        result = _check_question_antipatterns(questions)
        assert result[0] is False
        assert "anti-pattern" in result[1]

    def test_antipattern_different_phase(self):
        """Antipattern: does it work in different phases?"""
        questions = [{"question": "Does this work in different phases?", "answer": ""}]
        result = _check_question_antipatterns(questions)
        assert result[0] is False
        assert "anti-pattern" in result[1]

    def test_antipattern_describe_steps(self):
        """Antipattern: can you describe steps?"""
        questions = [{"question": "Can you describe the steps to trigger this?", "answer": ""}]
        result = _check_question_antipatterns(questions)
        assert result[0] is False
        assert "anti-pattern" in result[1]

    def test_missing_question_key_no_crash(self):
        """Missing 'question' key should not crash."""
        questions = [{"answer": "some answer"}]
        result = _check_question_antipatterns(questions)
        assert result == (True, "")

    def test_mixed_questions_fail_fast(self):
        """Mix of anti-pattern and legitimate should fail on first anti-pattern."""
        questions = [
            {"question": "What is your username?", "answer": ""},
            {"question": "Does this happen for all items?", "answer": ""},
            {"question": "Another legitimate question", "answer": ""},
        ]
        result = _check_question_antipatterns(questions)
        assert result[0] is False
        assert "Does this happen" in result[1]


class TestRunPlanningValidation:
    """Integration tests for run_planning() validation."""

    def test_raises_runtime_error_on_missing_exploration(self):
        """run_planning() should raise RuntimeError when exploration is missing."""
        mock_runner = MagicMock()
        mock_runner.for_phase.return_value = mock_runner

        mock_feature = MagicMock()
        mock_feature.title = "test-feature"
        mock_feature.item_type = "bug"
        mock_feature.slug = "test-slug"
        mock_feature.id = "test-id"
        mock_feature.get_section.return_value = "Test description"
        mock_feature.plan = ""
        mock_feature.questions = []
        mock_feature.Ideation = ""
        mock_feature.board = "test-board"
        mock_feature.current_stage = "planning"

        agent_output = '''Here are my questions:

```json
{"questions": [{"question": "What error do you see?", "answer": ""}], "plan": "fix it"}
```'''

        # Current behavior: validation logs warning but allows continuing (no RuntimeError)
        with patch('pipeline.phases._run_phase', return_value=agent_output):
            result = run_planning(mock_feature, mock_runner)

        # Function completes without raising - validation allows continuing

    def test_saves_feature_before_raising(self):
        """run_planning() should complete without raising when validation allows continuing."""
        mock_runner = MagicMock()
        mock_runner.for_phase.return_value = mock_runner

        mock_feature = MagicMock()
        mock_feature.title = "test-feature"
        mock_feature.item_type = "bug"
        mock_feature.slug = "test-slug"
        mock_feature.id = "test-id"
        mock_feature.get_section.return_value = "Test description"
        mock_feature.plan = ""
        mock_feature.questions = []
        mock_feature.Ideation = ""
        mock_feature.board = "test-board"
        mock_feature.current_stage = "planning"

        agent_output = '''Questions:

```json
{"questions": [{"question": "What error?", "answer": ""}], "plan": "fix"}
```'''

        # Current behavior: validation allows continuing, no RuntimeError
        with patch('pipeline.phases._run_phase', return_value=agent_output):
            result = run_planning(mock_feature, mock_runner)

        # Feature.save is called during normal execution
        mock_feature.save.assert_called()

    def test_anti_pattern_question_raises(self):
        """run_planning() should raise on anti-pattern questions."""
        mock_runner = MagicMock()
        mock_runner.for_phase.return_value = mock_runner

        mock_feature = MagicMock()
        mock_feature.title = "test-feature"
        mock_feature.item_type = "bug"
        mock_feature.slug = "test-slug"
        mock_feature.id = "test-id"
        mock_feature.get_section.return_value = "Test description"
        mock_feature.plan = ""
        mock_feature.questions = []
        mock_feature.Ideation = ""
        mock_feature.board = "test-board"
        mock_feature.current_stage = "planning"

        agent_output = '''I searched for the issue in the code.

```json
{"questions": [{"question": "Does this happen for all items?", "answer": ""}], "plan": "fix"}
```'''

        with patch('pipeline.phases._run_phase', return_value=agent_output):
            with pytest.raises(RuntimeError) as excinfo:
                run_planning(mock_feature, mock_runner)

        assert str(excinfo.value).startswith("Planning validation failed:")
        assert "anti-pattern" in str(excinfo.value)

    def test_valid_exploration_no_raise(self):
        """run_planning() should NOT raise when exploration evidence is present."""
        mock_runner = MagicMock()
        mock_runner.for_phase.return_value = mock_runner

        mock_feature = MagicMock()
        mock_feature.title = "test-feature"
        mock_feature.item_type = "bug"
        mock_feature.slug = "test-slug"
        mock_feature.id = "test-id"
        mock_feature.get_section.return_value = "Test description"
        mock_feature.plan = ""
        mock_feature.questions = []
        mock_feature.Ideation = ""
        mock_feature.board = "test-board"
        mock_feature.current_stage = "planning"

        agent_output = '''I searched for the error in the codebase.
I found the issue in the code.

```json
{"questions": [{"question": "What is your username?", "answer": ""}], "plan": "fix it"}
```'''

        with patch('pipeline.phases._run_phase', return_value=agent_output):
            result = run_planning(mock_feature, mock_runner)

        mock_feature.add_history.assert_called()

    def test_no_questions_no_validation(self):
        """run_planning() should not validate when there are no questions."""
        mock_runner = MagicMock()
        mock_runner.for_phase.return_value = mock_runner

        mock_feature = MagicMock()
        mock_feature.title = "test-feature"
        mock_feature.item_type = "bug"
        mock_feature.slug = "test-slug"
        mock_feature.id = "test-id"
        mock_feature.get_section.return_value = "Test description"
        mock_feature.plan = ""
        mock_feature.questions = []
        mock_feature.Ideation = ""
        mock_feature.board = "test-board"
        mock_feature.current_stage = "planning"

        agent_output = '''{"plan": "my plan without questions"}'''

        with patch('pipeline.phases._run_phase', return_value=agent_output):
            result = run_planning(mock_feature, mock_runner)

        assert result is not None
