"""Tests for runner authentication error handling and prompt ideation content."""

import pytest
import sys
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAuthErrorHandling:
    """Tests for authentication error detection in runner."""

    def test_auth_error_check_only_when_not_completed(self):
        """Verify that the auth error check is now only performed when phase did NOT complete.
        
        This test verifies the code structure change - the auth error check should be inside
        the 'if not got_completion_marker:' block, similar to rate limit checking."""
        import pipeline.runner as runner_module
        import inspect
        
        # Get the source code of the headless method
        source = inspect.getsource(runner_module.AgentRunner.headless)
        
        # Find the auth error check and verify it's inside the completion check
        # The auth error check should come AFTER the completion marker check
        auth_check_pos = source.find('# Only check for API key errors')
        
        # Find where the completion marker is checked
        completion_check_pos = source.find('got_completion_marker')
        
        # Find where rate limit checking starts (which is correctly inside the if not got_completion_marker block)
        rate_limit_pos = source.find('# Only check for rate limiting')
        
        # The auth check should be after the completion marker check and before rate limit checking
        # This verifies it's inside the "if not got_completion_marker:" block
        assert auth_check_pos > completion_check_pos, "Auth check should come after completion marker check"
        assert auth_check_pos < rate_limit_pos, "Auth check should come before rate limit check (inside the same if block)"
        
    def test_rate_limit_check_is_inside_completion_check(self):
        """Verify rate limit checking is inside the 'if not got_completion_marker:' block."""
        import pipeline.runner as runner_module
        import inspect
        
        source = inspect.getsource(runner_module.AgentRunner.headless)
        
        # Find positions
        completion_check_pos = source.find('got_completion_marker')
        rate_limit_pos = source.find('# Only check for rate limiting')
        
        # Rate limit check should come after completion marker check
        assert rate_limit_pos > completion_check_pos


class TestIdeationInPrompts:
    """Tests for ideation content in AI prompts."""

    def test_ideation_synthesis_in_planning_prompt(self):
        """When feature has Ideation, it should be included in the planning prompt."""
        from pipeline.phases import _build_prompt
        
        replacements = {
            "{title}": "Test Feature",
            "{description}": "Test description",
            "{feature_slug}": "test-feature",
            "{feature_id}": "test123",
            "{phase}": "planning",
            "{previous_plan}": "",
            "{ideation_synthesis}": "This is the synthesized ideation content from debate rounds.",
            "{feature_file_path}": "/tmp/test.json",
        }
        
        result = _build_prompt("plan-headless.md", replacements, "planning")
        
        assert "This is the synthesized ideation content from debate rounds." in result
        assert "Ideation (synthesized):" in result

    def test_no_ideation_in_planning_prompt(self):
        """When feature has no Ideation, placeholder text should be included."""
        from pipeline.phases import _build_prompt
        
        replacements = {
            "{title}": "Test Feature",
            "{description}": "Test description",
            "{feature_slug}": "test-feature",
            "{feature_id}": "test123",
            "{phase}": "planning",
            "{previous_plan}": "",
            "{ideation_synthesis}": "(no ideation)",
            "{feature_file_path}": "/tmp/test.json",
        }
        
        result = _build_prompt("plan-headless.md", replacements, "planning")
        
        assert "(no ideation)" in result

    def test_ideation_summaries_accessible_in_prompt(self):
        """Planning prompt should instruct agent to read feature file for ideation_summaries."""
        from pipeline.phases import _build_prompt
        
        replacements = {
            "{title}": "Test Feature",
            "{description}": "Test description",
            "{feature_slug}": "test-feature",
            "{feature_id}": "test123",
            "{phase}": "planning",
            "{previous_plan}": "",
            "{ideation_synthesis}": "test",
            "{feature_file_path}": "/tmp/test.json",
        }
        
        result = _build_prompt("plan-headless.md", replacements, "planning")
        
        # The prompt should tell the agent to read the feature file for full ideation_summaries
        assert "ideation_summaries" in result
        assert "Read tool" in result or "Read" in result

    def test_bug_planning_prompt_has_ideation(self):
        """Bug planning prompt should also include ideation content."""
        from pipeline.phases import _build_prompt
        
        replacements = {
            "{title}": "Test Bug",
            "{description}": "Test bug description",
            "{feature_slug}": "test-bug",
            "{feature_id}": "bug123",
            "{phase}": "planning",
            "{previous_plan}": "",
            "{ideation_synthesis}": "Bug analysis from ideation.",
            "{feature_file_path}": "/tmp/test.json",
        }
        
        result = _build_prompt("plan-bug.md", replacements, "planning")
        
        assert "Bug analysis from ideation." in result
        assert "Ideation (synthesized):" in result


class TestRunPlanningWithIdeation:
    """Integration tests for run_planning with ideation content."""

    def test_run_planning_includes_ideation(self):
        """run_planning should pass ideation to the prompt."""
        from pipeline import phases
        
        class MockFeature:
            def __init__(self):
                self._plan = None
                self.item_type = "feature"
                self._title = "Test Feature"
                self.id = "test123"
                self.slug = "test-feature"
                self._description = "Test description"
                self._questions = []
                self._history = []
                self._current_stage = "planning"
                self.board = "test-board"
                self.Ideation = "This is the ideation from debate rounds."
                self.ideation_summaries = [
                    "Round 1: Initial analysis...",
                    "Round 2: Further discussion...",
                ]
            
            @property
            def title(self):
                return self._title
            
            @property
            def plan(self):
                return self._plan
            
            @property
            def questions(self):
                return self._questions
            
            @property
            def current_stage(self):
                return self._current_stage
            
            def get_section(self, section):
                if section == "Description":
                    return self._description
                return None
            
            def add_history(self, phase, action):
                self._history.append((phase, action))
            
            def save(self):
                pass
            
            def set_plan(self, value):
                self._plan = value
            
            def set_questions(self, questions):
                self._questions = questions
            
            def move_to_stage(self, stage):
                self._current_stage = stage

        class MockRunner:
            def __init__(self):
                self.phase = None
                self.agent = MagicMock()
                self.agent.name = "Test Agent"
            
            def for_phase(self, phase):
                self.phase = phase
                return self

        feature = MockFeature()
        runner = MockRunner()
        
        # Mock the internal functions to capture the prompt
        captured_prompt = {}
        
        def capture_run_phase(phase_name, feature, runner, prompt, **kwargs):
            captured_prompt['prompt'] = prompt
            return '{"plan": "test plan", "questions": []}'
        
        with patch.object(phases, '_run_phase', side_effect=capture_run_phase):
            with patch.object(phases, '_get_latest_feedback', return_value=None):
                phases.run_planning(feature, runner)
        
        # Verify ideation is in the prompt
        assert 'prompt' in captured_prompt
        prompt = captured_prompt['prompt']
        assert "This is the ideation from debate rounds." in prompt
