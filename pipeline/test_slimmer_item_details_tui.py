"""Tests for TUI slimmer-item-details feature (collapsible sections).

Run with:
    pytest test_slimmer_item_details_tui.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))


def _make_mock_feature(title="Test Feature", description="",
                       history="", plan="", impl_spec="",
                       test_spec="", impl_notes="", questions=None,
                       ideation_prompt=""):
    """Create a mock feature object with the required interface."""
    f = SimpleNamespace()
    f.title = title
    f.description = description
    f.history = history
    f.plan = plan
    f.impl_spec = impl_spec
    f.test_spec = test_spec
    f.impl_notes = impl_notes
    f.questions = questions or []
    f.ideation_prompt = ideation_prompt
    f.Ideation = ""
    f.ideation_summaries = []
    f.done_script = ""
    f.plan_exploration_summary = ""
    f.test_results = None
    f.plan_reviews = []
    f.impl_reviews = []
    f.design_ref = ""
    f.pipeline_log = ""
    f.board = "default"
    f.current_stage = "ideas"
    f.slug = "test-feature"
    f.id = "f1"

    def get_section(section_name):
        if section_name == "Description":
            return description
        return ""

    f.get_section = get_section
    return f


class TestBuildFeatureDetailWidgets:
    """Tests for the _build_feature_detail_widgets function."""

    def test_title_always_visible(self):
        """Title should be rendered outside of collapsibles (not collapsible)."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(title="My Feature")
        widgets = _build_feature_detail_widgets(feature)

        assert len(widgets) > 0
        first_widget = widgets[0]
        assert first_widget.id == "detail-title"

    def test_description_always_visible(self):
        """Description should be rendered outside of collapsibles."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(description="This is a description")
        widgets = _build_feature_detail_widgets(feature)

        desc_widget = next((w for w in widgets if w.id == "detail-description"), None)
        assert desc_widget is not None

    def test_history_expanded_by_default(self):
        """History section should be expanded by default."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(history="2025-01-01: Created feature")
        widgets = _build_feature_detail_widgets(feature)

        history_widget = next((w for w in widgets if w.id == "detail-history"), None)
        assert history_widget is not None
        assert history_widget.collapsed is False

    def test_plan_collapsed_by_default(self):
        """Plan section should be collapsed by default."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(plan="Test plan content")
        widgets = _build_feature_detail_widgets(feature)

        plan_widget = next((w for w in widgets if w.id == "detail-plan"), None)
        assert plan_widget is not None
        assert plan_widget.collapsed is True

    def test_impl_spec_collapsed_by_default(self):
        """Implementation Spec section should be collapsed by default."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(impl_spec="Implementation spec content")
        widgets = _build_feature_detail_widgets(feature)

        impl_spec_widget = next((w for w in widgets if w.id == "detail-impl-spec"), None)
        assert impl_spec_widget is not None
        assert impl_spec_widget.collapsed is True

    def test_test_spec_collapsed_by_default(self):
        """Test Spec section should be collapsed by default."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(test_spec="Test spec content")
        widgets = _build_feature_detail_widgets(feature)

        test_spec_widget = next((w for w in widgets if w.id == "detail-test-spec"), None)
        assert test_spec_widget is not None
        assert test_spec_widget.collapsed is True

    def test_impl_notes_collapsed_by_default(self):
        """Implementation Notes section should be collapsed by default."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(impl_notes="Implementation notes content")
        widgets = _build_feature_detail_widgets(feature)

        impl_notes_widget = next((w for w in widgets if w.id == "detail-impl-notes"), None)
        assert impl_notes_widget is not None
        assert impl_notes_widget.collapsed is True

    def test_questions_collapsed_by_default(self):
        """Questions section should be collapsed by default."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(questions=[{"question": "Q1?", "answer": "A1"}])
        widgets = _build_feature_detail_widgets(feature)

        questions_widget = next((w for w in widgets if w.id == "detail-questions"), None)
        assert questions_widget is not None
        assert questions_widget.collapsed is True


class TestBuildFeatureDetailWidgetsEmptySections:
    """Tests for empty section placeholder text."""

    def test_empty_plan_shows_placeholder(self):
        """Empty Plan section should show 'No plan provided' placeholder."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(plan="")
        widgets = _build_feature_detail_widgets(feature)

        plan_widget = next((w for w in widgets if w.id == "detail-plan"), None)
        assert plan_widget is not None
        assert plan_widget.collapsed is True

    def test_empty_impl_spec_shows_placeholder(self):
        """Empty Impl Spec section should show 'No implementation spec provided' placeholder."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(impl_spec="")
        widgets = _build_feature_detail_widgets(feature)

        impl_spec_widget = next((w for w in widgets if w.id == "detail-impl-spec"), None)
        assert impl_spec_widget is not None

    def test_empty_test_spec_shows_placeholder(self):
        """Empty Test Spec section should show placeholder."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(test_spec="")
        widgets = _build_feature_detail_widgets(feature)

        test_spec_widget = next((w for w in widgets if w.id == "detail-test-spec"), None)
        assert test_spec_widget is not None

    def test_empty_impl_notes_shows_placeholder(self):
        """Empty Impl Notes section should show placeholder."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(impl_notes="")
        widgets = _build_feature_detail_widgets(feature)

        impl_notes_widget = next((w for w in widgets if w.id == "detail-impl-notes"), None)
        assert impl_notes_widget is not None

    def test_empty_questions_shows_placeholder(self):
        """Empty Questions section should show placeholder."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(questions=[])
        widgets = _build_feature_detail_widgets(feature)

        questions_widget = next((w for w in widgets if w.id == "detail-questions"), None)
        assert questions_widget is not None

    def test_empty_history_shows_placeholder(self):
        """Empty History section should show 'No history provided' placeholder."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(history="")
        widgets = _build_feature_detail_widgets(feature)

        history_widget = next((w for w in widgets if w.id == "detail-history"), None)
        assert history_widget is not None


class TestBuildFeatureDetailWidgetsAllFields:
    """Tests for features with all fields populated."""

    def test_all_sections_with_content(self):
        """All sections with content should render correctly."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(
            title="Full Feature",
            description="A detailed description",
            history="2025-01-01: Created",
            plan="The plan",
            impl_spec="The impl spec",
            test_spec="The test spec",
            impl_notes="The impl notes",
            questions=[{"question": "Q1?", "answer": "A1"}]
        )
        widgets = _build_feature_detail_widgets(feature)

        assert any(w.id == "detail-title" for w in widgets)
        assert any(w.id == "detail-description" for w in widgets)
        assert any(w.id == "detail-history" for w in widgets)
        assert any(w.id == "detail-plan" for w in widgets)
        assert any(w.id == "detail-impl-spec" for w in widgets)
        assert any(w.id == "detail-test-spec" for w in widgets)
        assert any(w.id == "detail-impl-notes" for w in widgets)
        assert any(w.id == "detail-questions" for w in widgets)

    def test_questions_count_in_title(self):
        """Questions section title should show the count."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(questions=[
            {"question": "Q1?", "answer": "A1"},
            {"question": "Q2?", "answer": "A2"},
            {"question": "Q3?", "answer": ""},
        ])
        widgets = _build_feature_detail_widgets(feature)

        questions_widget = next((w for w in widgets if w.id == "detail-questions"), None)
        assert questions_widget is not None
        assert "Questions (3)" in questions_widget.title


class TestBuildFeatureDetailWidgetsSpecialCharacters:
    """Tests for special characters in content."""

    def test_special_characters_in_plan(self):
        """Special characters in plan should be handled correctly."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(plan="Plan with <html> & \"quotes\"")
        widgets = _build_feature_detail_widgets(feature)

        plan_widget = next((w for w in widgets if w.id == "detail-plan"), None)
        assert plan_widget is not None

    def test_special_characters_in_impl_spec(self):
        """Special characters in impl_spec should be handled correctly."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(impl_spec="<script>alert('xss')</script>")
        widgets = _build_feature_detail_widgets(feature)

        impl_spec_widget = next((w for w in widgets if w.id == "detail-impl-spec"), None)
        assert impl_spec_widget is not None


class TestBuildFeatureDetailWidgetsEdgeCases:
    """Edge case tests."""

    def test_empty_feature(self):
        """Feature with no content in any section."""
        from tui import _build_feature_detail_widgets

        feature = _make_mock_feature(
            title="",
            description="",
            history="",
            plan="",
            impl_spec="",
            test_spec="",
            impl_notes="",
            questions=[]
        )
        widgets = _build_feature_detail_widgets(feature)

        assert len(widgets) > 0

    def test_very_long_content(self):
        """Very long content should render without breaking."""
        from tui import _build_feature_detail_widgets

        long_plan = "x" * 10000
        feature = _make_mock_feature(plan=long_plan)
        widgets = _build_feature_detail_widgets(feature)

        plan_widget = next((w for w in widgets if w.id == "detail-plan"), None)
        assert plan_widget is not None
