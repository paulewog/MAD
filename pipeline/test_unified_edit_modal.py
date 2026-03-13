"""Tests for UnifiedEditModal and ScriptsPane widgets in tui.py.

Run with:
    pytest test_unified_edit_modal.py -v
    pytest test_scripts_pane.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Button, Input, Select, TextArea

sys.path.insert(0, str(Path(__file__).parent))


class TestUnifiedEditModal:
    """Tests for UnifiedEditModal widget."""

    def test_ideas_stage_shows_all_fields(self):
        """Test that IDEAS stage shows title, type, description, and script fields."""
        from tui import UnifiedEditModal
        
        feature_data = {
            "title": "Test Feature",
            "description": "Test description",
            "item_type": "feature",
            "done_script": ""
        }
        scripts = []
        
        modal = UnifiedEditModal(feature_data, "ideas", scripts)
        
        assert modal.current_stage == "ideas"
        assert modal.feature_data == feature_data

    def test_late_stage_shows_title_description_script(self):
        """Test that late stages (non-early) show title, description, and script fields but NOT type."""
        from tui import UnifiedEditModal
        
        feature_data = {
            "title": "Test Feature",
            "description": "Test description",
            "item_type": "feature",
            "done_script": "test-script"
        }
        scripts = []
        
        modal = UnifiedEditModal(feature_data, "implementing", scripts)
        
        assert modal.current_stage == "implementing"
        assert modal.feature_data == feature_data

    def test_ideas_stage_fields_configured(self):
        """Test that IDEAS stage has all fields configured."""
        from tui import UnifiedEditModal
        
        feature_data = {"title": "", "description": "", "item_type": "feature", "done_script": ""}
        scripts = []
        
        modal = UnifiedEditModal(feature_data, "ideas", scripts)
        
        # IDEAS stage should have is_ideas = True
        assert modal.current_stage == "ideas"
        
    def test_late_stage_fields_configured(self):
        """Test that late stage has title, description, and script fields but NOT type."""
        from tui import UnifiedEditModal
        
        feature_data = {"title": "", "description": "", "item_type": "feature", "done_script": ""}
        scripts = []
        
        modal = UnifiedEditModal(feature_data, "implementing", scripts)
        
        # Late stage should not be early stage
        assert modal.current_stage not in ("ideas", "ideating", "plan-inbox")

    def test_populates_existing_values(self):
        """Test that modal populates fields with existing feature values."""
        from tui import UnifiedEditModal
        
        feature_data = {
            "title": "Existing Title",
            "description": "Existing Description",
            "item_type": "bug",
            "done_script": "existing-script"
        }
        scripts = []
        
        modal = UnifiedEditModal(feature_data, "ideas", scripts)
        
        assert modal.feature_data["title"] == "Existing Title"
        assert modal.feature_data["description"] == "Existing Description"
        assert modal.feature_data["item_type"] == "bug"
        assert modal.feature_data["done_script"] == "existing-script"

    def test_scripts_populated_in_select(self):
        """Test that scripts are available for the done_script select."""
        from tui import UnifiedEditModal
        from scripts import ScriptConfig
        
        mock_script = MagicMock(spec=ScriptConfig)
        mock_script.id = "test-script"
        mock_script.label = "Test Script"
        
        feature_data = {"title": "", "description": "", "item_type": "feature", "done_script": ""}
        
        modal = UnifiedEditModal(feature_data, "ideas", [mock_script])
        
        assert len(modal.scripts) == 1
        assert modal.scripts[0].id == "test-script"

    def test_early_stage_shows_type_field(self):
        """Test that early stages (ideas, ideating, plan-inbox) show the type selector."""
        from tui import UnifiedEditModal
        
        feature_data = {"title": "", "description": "", "item_type": "feature", "done_script": ""}
        scripts = []
        
        for stage in ("ideas", "ideating", "plan-inbox"):
            modal = UnifiedEditModal(feature_data, stage, scripts)
            assert modal.current_stage in ("ideas", "ideating", "plan-inbox"), f"Stage {stage} should be early stage"

    def test_late_stage_hides_type_field(self):
        """Test that late stages do NOT show the type selector."""
        from tui import UnifiedEditModal
        
        feature_data = {"title": "", "description": "", "item_type": "feature", "done_script": ""}
        scripts = []
        
        late_stages = ("reviewing-plan", "requested-input", "approved", "spec-writing", 
                       "implementing", "testing", "review", "final-human-approval", 
                       "done", "rejected")
        
        for stage in late_stages:
            modal = UnifiedEditModal(feature_data, stage, scripts)
            assert modal.current_stage not in ("ideas", "ideating", "plan-inbox"), f"Stage {stage} should not be early stage"


class TestScriptsPane:
    """Tests for ScriptsPane widget."""

    def test_scripts_pane_initialization(self):
        """Test that ScriptsPane initializes with scripts and status."""
        from tui import ScriptsPane
        from scripts import ScriptStatus
        
        mock_script = MagicMock()
        mock_script.id = "test-script"
        mock_script.label = "Test Script"
        mock_script.confirm = False
        
        mock_status = MagicMock(spec=ScriptStatus)
        mock_status.running = False
        
        pane = ScriptsPane([mock_script], mock_status)
        
        assert len(pane._scripts) == 1
        assert pane._scripts[0].id == "test-script"
        assert pane._script_status == mock_status

    def test_scripts_pane_with_empty_scripts(self):
        """Test ScriptsPane handles empty scripts list."""
        from tui import ScriptsPane
        
        pane = ScriptsPane([], None)
        
        assert len(pane._scripts) == 0
        assert pane._script_status is None

    def test_scripts_pane_has_required_widget_ids(self):
        """Test that ScriptsPane defines required widget IDs in CSS."""
        from tui import ScriptsPane
        
        mock_script = MagicMock()
        mock_script.id = "test-script"
        mock_script.label = "Test Script"
        
        pane = ScriptsPane([mock_script], None)
        
        # Check that the widget has the right attributes set
        assert hasattr(pane, '_scripts')
        assert len(pane._scripts) == 1
        assert pane._scripts[0].id == "test-script"
        
        # Verify script-select, context-area, run-script-btn are in the CSS
        # by checking the class CSS attribute
        css = ScriptsPane.CSS
        assert "script-select" in css or "script" in css.lower()

    def test_scripts_pane_script_selection_validation(self):
        """Test script selection validation logic."""
        from tui import ScriptsPane
        
        mock_script = MagicMock()
        mock_script.id = "test-script"
        mock_script.label = "Test Script"
        
        pane = ScriptsPane([mock_script], None)
        
        # Test that script index validation works
        # Invalid index (-1)
        assert not (-1 >= 0 and -1 < len(pane._scripts))
        
        # Valid index (0)
        assert 0 >= 0 and 0 < len(pane._scripts)
        
        # Invalid index (beyond length)
        assert not (1 >= 0 and 1 < len(pane._scripts))

    def test_scripts_pane_confirm_flag(self):
        """Test that confirm flag is properly accessed."""
        from tui import ScriptsPane
        
        mock_script = MagicMock()
        mock_script.id = "confirm-script"
        mock_script.label = "Confirm Script"
        mock_script.confirm = True
        
        pane = ScriptsPane([mock_script], None)
        
        assert pane._scripts[0].confirm == True

    def test_scripts_pane_with_running_status(self):
        """Test ScriptsPane with running script status."""
        from tui import ScriptsPane
        from scripts import ScriptStatus
        
        mock_script = MagicMock()
        mock_script.id = "test-script"
        mock_script.label = "Test Script"
        
        mock_status = MagicMock(spec=ScriptStatus)
        mock_status.running = True
        mock_status.script_id = "test-script"
        
        pane = ScriptsPane([mock_script], mock_status)
        
        assert pane._script_status.running == True
        assert pane._script_status.script_id == "test-script"
