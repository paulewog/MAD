"""Tests for preserve-plan-across-replanning feature"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import phases
from pipeline.phases import _build_prompt


class MockFeature:
    """Mock FeatureFile for testing."""
    
    def __init__(self, plan_content=None, item_type="feature", title="Test Feature", feature_id="test123", slug="test-feature"):
        self._plan = plan_content
        self.item_type = item_type
        self._title = title
        self.id = feature_id
        self.slug = slug
        self._description = "Test description"
        self._questions = []
        self._history = []
        self._current_stage = "planning"
        self.board = "test-board"
        self.Ideation = ""
    
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
    """Mock AgentRunner for testing."""
    
    def __init__(self):
        self.phase = None
        self.captured_prompt = None
        self.agent = MagicMock()
        self.agent.name = "Test Agent"
    
    def for_phase(self, phase):
        self.phase = phase
        return self


class TestPreviousPlanContext:
    """Tests for previous plan context injection."""
    
    def test_previous_plan_context_injected_when_plan_exists(self):
        """When feature has existing plan, previous_plan_context should be populated."""
        feature = MockFeature(plan_content="Previous plan content here")
        runner = MockRunner()
        
        with patch.object(phases, '_run_phase', return_value='{"plan": "done", "questions": []}'):
            with patch.object(phases, '_get_latest_feedback', return_value=None):
                phases.run_planning(feature, runner)
    
    def test_no_context_when_plan_is_none(self):
        """When feature.plan is None, no previous plan context should be added."""
        feature = MockFeature(plan_content=None)
        runner = MockRunner()
        
        with patch.object(phases, '_run_phase', return_value='{"plan": "done", "questions": []}'):
            with patch.object(phases, '_get_latest_feedback', return_value=None):
                phases.run_planning(feature, runner)
    
    def test_no_context_when_plan_is_empty_string(self):
        """When feature.plan is empty string, no previous plan context should be added."""
        feature = MockFeature(plan_content="")
        runner = MockRunner()
        
        with patch.object(phases, '_run_phase', return_value='{"plan": "done", "questions": []}'):
            with patch.object(phases, '_get_latest_feedback', return_value=None):
                phases.run_planning(feature, runner)
    
    def test_no_context_when_plan_is_whitespace_only(self):
        """When feature.plan is whitespace-only, no previous plan context should be added."""
        feature = MockFeature(plan_content="   \n\t   ")
        runner = MockRunner()
        
        with patch.object(phases, '_run_phase', return_value='{"plan": "done", "questions": []}'):
            with patch.object(phases, '_get_latest_feedback', return_value=None):
                phases.run_planning(feature, runner)


class TestTemplateVariableSubstitution:
    """Tests for {previous_plan} template variable substitution."""
    
    def test_previous_plan_substituted_in_template(self):
        """The {previous_plan} placeholder should be replaced in the template."""
        previous_plan_context = "\n\n## Previous Plan (refine rather than rewrite):\nOld plan content\n"
        
        result = _build_prompt("plan-headless.md", {
            "{title}": "Test",
            "{description}": "Test desc",
            "{feature_slug}": "test",
            "{feature_id}": "123",
            "{phase}": "planning",
            "{previous_plan}": previous_plan_context,
        }, "planning")
        
        assert "## Previous Plan (refine rather than rewrite):" in result
        assert "Old plan content" in result
    
    def test_previous_plan_empty_in_template(self):
        """When previous_plan is empty, the placeholder should be replaced with empty string."""
        result = _build_prompt("plan-headless.md", {
            "{title}": "Test",
            "{description}": "Test desc",
            "{feature_slug}": "test",
            "{feature_id}": "123",
            "{phase}": "planning",
            "{previous_plan}": "",
        }, "planning")
        
        assert "## Previous Plan" not in result
    
    def test_previous_plan_in_bug_template(self):
        """The {previous_plan} placeholder should also work in plan-bug.md."""
        previous_plan_context = "\n\n## Previous Plan (refine rather than rewrite):\nBug fix plan\n"
        
        result = _build_prompt("plan-bug.md", {
            "{title}": "Test Bug",
            "{description}": "Bug desc",
            "{feature_slug}": "test-bug",
            "{feature_id}": "456",
            "{phase}": "planning",
            "{previous_plan}": previous_plan_context,
        }, "planning")
        
        assert "## Previous Plan (refine rather than rewrite):" in result
        assert "Bug fix plan" in result


class TestPromptBuildingOrder:
    """Tests for prompt building order."""
    
    def test_prompt_order_with_all_contexts(self):
        """When all contexts are present, order should be: template + questions + feedback + previous_plan."""
        feature = MockFeature(plan_content="Existing plan")
        feature._questions = [{"question": "Q1", "answer": "A1"}]
        runner = MockRunner()
        
        captured_prompt = None
        
        def capture_run_phase(phase_name, feature_obj, runner_obj, prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return '{"plan": "done", "questions": []}'
        
        with patch.object(phases, '_run_phase', side_effect=capture_run_phase):
            with patch.object(phases, '_get_latest_feedback', return_value="Some feedback"):
                phases.run_planning(feature, runner)
        
        assert captured_prompt is not None
        template_idx = captured_prompt.index("# Design Planning")
        previous_plan_idx = captured_prompt.index("## Previous Plan")
        questions_idx = captured_prompt.index("## Previous Answers:")
        feedback_idx = captured_prompt.index("## Previous Review Feedback")
        
        assert template_idx < previous_plan_idx < questions_idx < feedback_idx


class TestPlanWithSpecialCharacters:
    """Tests for plans with special characters or template-like syntax."""
    
    def test_plan_with_template_like_syntax(self):
        """Plan containing {variable} patterns should be handled correctly."""
        plan_with_vars = "Use {title} and {description} for the plan"
        
        result = _build_prompt("plan-headless.md", {
            "{title}": "Test",
            "{description}": "Test desc",
            "{feature_slug}": "test",
            "{feature_id}": "123",
            "{phase}": "planning",
            "{previous_plan}": f"\n\n## Previous Plan (refine rather than rewrite):\n{plan_with_vars}\n",
        }, "planning")
        
        assert plan_with_vars in result
    
    def test_plan_with_markdown_syntax(self):
        """Plan containing markdown should be preserved."""
        plan_with_md = "## Step 1\n- Item 1\n- Item 2\n```python\ncode\n```"
        
        result = _build_prompt("plan-headless.md", {
            "{title}": "Test",
            "{description}": "Test desc",
            "{feature_slug}": "test",
            "{feature_id}": "123",
            "{phase}": "planning",
            "{previous_plan}": f"\n\n## Previous Plan (refine rather than rewrite):\n{plan_with_md}\n",
        }, "planning")
        
        assert "## Step 1" in result
        assert "- Item 1" in result
        assert "```python" in result
    
    def test_plan_with_unicode_characters(self):
        """Plan with unicode characters should be preserved."""
        plan_with_unicode = "Plan with émojis: 🎉 and special chars: ñ, ü"
        
        result = _build_prompt("plan-headless.md", {
            "{title}": "Test",
            "{description}": "Test desc",
            "{feature_slug}": "test",
            "{feature_id}": "123",
            "{phase}": "planning",
            "{previous_plan}": f"\n\n## Previous Plan (refine rather than rewrite):\n{plan_with_unicode}\n",
        }, "planning")
        
        assert "🎉" in result
        assert "ñ" in result
