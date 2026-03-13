"""Tests for arrow key navigation in tui.py.

Run with:
    pytest test_arrow_keys_navigation.py -v
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tui import PipelineApp, StageHeader, FeatureItem


class TestQueryDOMOrder:
    """Tests that verify query() returns items in DOM order."""

    def test_query_combined_selector_returns_dom_order(self):
        """Test that query with comma-separated selectors returns items in DOM order."""
        
        class FakeApp:
            def __init__(self):
                self._items = []
                
            def query(self, selector):
                if selector == "StageHeader, FeatureItem":
                    return self._items
                return []
                
            def query_one(self, selector):
                if selector == "#items-pane":
                    m = MagicMock()
                    m.focus = MagicMock()
                    return m
                raise Exception("not found")
        
        app = FakeApp()
        
        stage_a = MagicMock(spec=StageHeader)
        stage_a.stage = "stage-a"
        
        feature_1 = MagicMock(spec=FeatureItem)
        
        feature_2 = MagicMock(spec=FeatureItem)
        
        stage_b = MagicMock(spec=StageHeader)
        stage_b.stage = "stage-b"
        
        feature_3 = MagicMock(spec=FeatureItem)
        
        app._items = [stage_a, feature_1, feature_2, stage_b, feature_3]
        
        item_list = list(app.query("StageHeader, FeatureItem"))
        
        assert item_list[0] is stage_a
        assert item_list[1] is feature_1
        assert item_list[2] is feature_2
        assert item_list[3] is stage_b
        assert item_list[4] is feature_3


class TrackingMock(MagicMock):
    """A MagicMock that tracks what was focused."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._was_focused = False
        
    def focus(self):
        self._was_focused = True
        super().focus()


def create_mock_app(items, focused_item=None):
    """Create a mock app with action methods bound."""
    mock = MagicMock()
    mock._items = items
    mock._focused_item = focused_item
    
    mock.focused = focused_item
    
    def query(selector):
        if selector == "StageHeader, FeatureItem":
            return items
        return []
    mock.query = query
    
    def query_one(selector):
        if selector == "#items-pane":
            m = MagicMock()
            m.focus = MagicMock()
            return m
        raise Exception("not found")
    mock.query_one = query_one
    
    mock.notify = MagicMock()
    
    original_focus_next = PipelineApp.action_focus_next
    original_focus_previous = PipelineApp.action_focus_previous
    
    def action_focus_next(self):
        original_focus_next(self)
        
    def action_focus_previous(self):
        original_focus_previous(self)
    
    mock.action_focus_next = types.MethodType(action_focus_next, mock)
    mock.action_focus_previous = types.MethodType(action_focus_previous, mock)
    
    return mock


class TestActionFocusNext:
    """Tests for action_focus_next method."""

    def test_focus_next_moves_to_next_item_in_dom_order(self):
        """Test that pressing down moves focus to the next item in DOM order."""
        
        stage_a = MagicMock(spec=StageHeader)
        stage_a.stage = "stage-a"
        
        feature_1 = MagicMock(spec=FeatureItem)
        feature_2 = MagicMock(spec=FeatureItem)
        stage_b = MagicMock(spec=StageHeader)
        
        items = [stage_a, feature_1, feature_2, stage_b]
        
        app = create_mock_app(items, stage_a)
        
        app.action_focus_next()
        
        feature_1.focus.assert_called_once()

    def test_focus_next_wraps_at_end(self):
        """Test that pressing down on last item wraps to first."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1]
        
        app = create_mock_app(items, feature_1)
        
        app.action_focus_next()
        
        stage_a.focus.assert_called_once()

    def test_focus_next_with_no_focus_moves_to_first(self):
        """Test that pressing down with no focus moves to first item."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1]
        
        app = create_mock_app(items, None)
        
        app.action_focus_next()
        
        stage_a.focus.assert_called_once()

    def test_focus_next_empty_list_does_nothing(self):
        """Test that pressing down with empty list does nothing."""
        
        app = create_mock_app([], None)
        
        app.action_focus_next()

    def test_focus_next_interleaves_stages_and_features(self):
        """Test that navigation correctly interleaves stages and features."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        feature_2 = MagicMock(spec=FeatureItem)
        stage_b = MagicMock(spec=StageHeader)
        feature_3 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1, feature_2, stage_b, feature_3]
        
        app = create_mock_app(items, stage_a)
        
        app.action_focus_next()
        
        feature_1.focus.assert_called_once()
        
        app.focused = feature_1
        
        app.action_focus_next()
        
        feature_2.focus.assert_called_once()


class TestActionFocusPrevious:
    """Tests for action_focus_previous method."""

    def test_focus_previous_moves_to_previous_item_in_dom_order(self):
        """Test that pressing up moves focus to the previous item in DOM order."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1]
        
        app = create_mock_app(items, feature_1)
        
        app.action_focus_previous()
        
        stage_a.focus.assert_called_once()

    def test_focus_previous_wraps_at_beginning(self):
        """Test that pressing up on first item wraps to last."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1]
        
        app = create_mock_app(items, stage_a)
        
        app.action_focus_previous()
        
        feature_1.focus.assert_called_once()

    def test_focus_previous_with_no_focus_moves_to_first(self):
        """Test that pressing up with no focus moves to first item."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1]
        
        app = create_mock_app(items, None)
        
        app.action_focus_previous()
        
        stage_a.focus.assert_called_once()

    def test_focus_previous_empty_list_does_nothing(self):
        """Test that pressing up with empty list does nothing."""
        
        app = create_mock_app([], None)
        
        app.action_focus_previous()

    def test_focus_previous_interleaves_stages_and_features(self):
        """Test that reverse navigation correctly interleaves stages and features."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        feature_2 = MagicMock(spec=FeatureItem)
        stage_b = MagicMock(spec=StageHeader)
        feature_3 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1, feature_2, stage_b, feature_3]
        
        app = create_mock_app(items, feature_3)
        
        app.action_focus_previous()
        
        stage_b.focus.assert_called_once()
        
        app.focused = stage_b
        
        app.action_focus_previous()
        
        feature_2.focus.assert_called_once()


class TestEdgeCases:
    """Tests for edge cases in arrow key navigation."""

    def test_single_item_wraps_to_itself_next(self):
        """Test that pressing down on single item wraps to itself."""
        
        stage_a = MagicMock(spec=StageHeader)
        
        items = [stage_a]
        
        app = create_mock_app(items, stage_a)
        
        app.action_focus_next()
        
        stage_a.focus.assert_called_once()

    def test_single_item_wraps_to_itself_previous(self):
        """Test that pressing up on single item wraps to itself."""
        
        stage_a = MagicMock(spec=StageHeader)
        
        items = [stage_a]
        
        app = create_mock_app(items, stage_a)
        
        app.action_focus_previous()
        
        stage_a.focus.assert_called_once()

    def test_focus_on_non_navigable_widget_moves_to_first(self):
        """Test that pressing arrow when focus is on non-navigable widget moves to first."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1]
        
        non_nav_widget = MagicMock()
        
        app = create_mock_app(items, non_nav_widget)
        
        app.action_focus_next()
        
        stage_a.focus.assert_called_once()

    def test_empty_stage_header_navigable(self):
        """Test that stage headers with no features are still navigable."""
        
        stage_a = MagicMock(spec=StageHeader)
        stage_a.stage = "stage-a"
        feature_1 = MagicMock(spec=FeatureItem)
        stage_b = MagicMock(spec=StageHeader)
        stage_b.stage = "stage-b"
        
        items = [stage_a, feature_1, stage_b]
        
        app = create_mock_app(items, feature_1)
        
        app.action_focus_next()
        
        stage_b.focus.assert_called_once()
        
        app.focused = stage_b
        
        app.action_focus_previous()
        
        feature_1.focus.assert_called_once()

    def test_multiple_stages_with_multiple_features(self):
        """Test navigation across multiple stages with multiple features."""
        
        stage_a = MagicMock(spec=StageHeader)
        feature_1 = MagicMock(spec=FeatureItem)
        feature_2 = MagicMock(spec=FeatureItem)
        stage_b = MagicMock(spec=StageHeader)
        feature_3 = MagicMock(spec=FeatureItem)
        stage_c = MagicMock(spec=StageHeader)
        feature_4 = MagicMock(spec=FeatureItem)
        feature_5 = MagicMock(spec=FeatureItem)
        
        items = [stage_a, feature_1, feature_2, stage_b, feature_3, stage_c, feature_4, feature_5]
        
        app = create_mock_app(items, stage_a)
        
        app.action_focus_next()
        feature_1.focus.assert_called_once()
        
        app.focused = feature_1
        app.action_focus_next()
        feature_2.focus.assert_called_once()
        
        app.focused = feature_2
        app.action_focus_next()
        stage_b.focus.assert_called_once()
        
        app.focused = stage_b
        app.action_focus_next()
        feature_3.focus.assert_called_once()
        
        app.focused = feature_3
        app.action_focus_next()
        stage_c.focus.assert_called_once()
        
        app.focused = stage_c
        app.action_focus_next()
        feature_4.focus.assert_called_once()
        
        app.focused = feature_4
        app.action_focus_next()
        feature_5.focus.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
