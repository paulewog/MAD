"""Tests for requires_human_approval feature.

Run with:
    pytest test_human_approval.py -v
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from state import FeatureFile, STAGES


class TestAwaitingHumanApprovalPhaseRegistration:
    """Test that awaiting-human-approval is properly registered as a stage."""

    def test_awaiting_human_approval_in_stages_list(self):
        """Test that awaiting-human-approval is in the STAGES list."""
        assert "awaiting-human-approval" in STAGES

    def test_awaiting_human_approval_position(self):
        """Test that awaiting-human-approval is between requested-input and approved."""
        idx_requested = STAGES.index("requested-input")
        idx_awaiting = STAGES.index("awaiting-human-approval")
        idx_approved = STAGES.index("approved")
        assert idx_requested < idx_awaiting < idx_approved


class TestRequiresHumanApprovalProperty:
    """Test the requires_human_approval property on FeatureFile."""

    def test_property_returns_false_when_absent(self, tmp_path):
        """Test that requires_human_approval returns False when not set."""
        feature_data = {
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "history": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }
        feature_file = tmp_path / "test.json"
        feature_file.write_text(json.dumps(feature_data))
        ff = FeatureFile(feature_file)
        assert ff.requires_human_approval is False

    def test_property_returns_true_when_explicit_true(self, tmp_path):
        """Test that requires_human_approval returns True when explicitly set to true."""
        feature_data = {
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "requires_human_approval": True,
            "history": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }
        feature_file = tmp_path / "test.json"
        feature_file.write_text(json.dumps(feature_data))
        ff = FeatureFile(feature_file)
        assert ff.requires_human_approval is True

    def test_property_returns_false_for_string_true(self, tmp_path):
        """Test that requires_human_approval returns False for string 'true' (identity check)."""
        feature_data = {
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "requires_human_approval": "true",
            "history": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }
        feature_file = tmp_path / "test.json"
        feature_file.write_text(json.dumps(feature_data))
        ff = FeatureFile(feature_file)
        assert ff.requires_human_approval is False

    def test_property_returns_false_for_integer_1(self, tmp_path):
        """Test that requires_human_approval returns False for integer 1."""
        feature_data = {
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "requires_human_approval": 1,
            "history": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }
        feature_file = tmp_path / "test.json"
        feature_file.write_text(json.dumps(feature_data))
        ff = FeatureFile(feature_file)
        assert ff.requires_human_approval is False

    def test_property_returns_false_for_null(self, tmp_path):
        """Test that requires_human_approval returns False for null."""
        feature_data = {
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "requires_human_approval": None,
            "history": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }
        feature_file = tmp_path / "test.json"
        feature_file.write_text(json.dumps(feature_data))
        ff = FeatureFile(feature_file)
        assert ff.requires_human_approval is False

    def test_property_returns_false_for_empty_string(self, tmp_path):
        """Test that requires_human_approval returns False for empty string."""
        feature_data = {
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "requires_human_approval": "",
            "history": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }
        feature_file = tmp_path / "test.json"
        feature_file.write_text(json.dumps(feature_data))
        ff = FeatureFile(feature_file)
        assert ff.requires_human_approval is False


class TestRequiresHumanApprovalSetter:
    """Test the set_requires_human_approval setter on FeatureFile."""

    def test_setter_stores_boolean_true(self, tmp_path):
        """Test that setter stores boolean True."""
        feature_data = {
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "history": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }
        feature_file = tmp_path / "test.json"
        feature_file.write_text(json.dumps(feature_data))
        ff = FeatureFile(feature_file)
        ff.set_requires_human_approval(True)
        data = json.loads(feature_file.read_text())
        assert data["requires_human_approval"] is True

    def test_setter_stores_boolean_false(self, tmp_path):
        """Test that setter stores boolean False."""
        feature_data = {
            "id": "test123",
            "board": "test",
            "title": "Test Feature",
            "type": "feature",
            "created": "2024-01-01T00:00:00Z",
            "description": "Test description",
            "requires_human_approval": True,
            "history": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }
        feature_file = tmp_path / "test.json"
        feature_file.write_text(json.dumps(feature_data))
        ff = FeatureFile(feature_file)
        ff.set_requires_human_approval(False)
        data = json.loads(feature_file.read_text())
        assert data["requires_human_approval"] is False


class TestFeatureFileCreate:
    """Test FeatureFile.create includes requires_human_approval."""

    def test_create_with_flag_true(self, tmp_path, monkeypatch):
        """Test that create() includes requires_human_approval=True when passed."""
        test_boards_dir = tmp_path / "boards"
        test_boards_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("state.Config.boards_dir", test_boards_dir)
        monkeypatch.setattr("config.Config.boards_dir", test_boards_dir)

        ff = FeatureFile.create(
            "test", "Test Feature", "Test desc",
            item_type="feature", done_script="",
            requires_human_approval=True
        )

        data = json.loads(ff.path.read_text())
        assert data["requires_human_approval"] is True
        assert ff.requires_human_approval is True

    def test_create_with_flag_false(self, tmp_path, monkeypatch):
        """Test that create() includes requires_human_approval=False when passed."""
        test_boards_dir = tmp_path / "boards"
        test_boards_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("state.Config.boards_dir", test_boards_dir)
        monkeypatch.setattr("config.Config.boards_dir", test_boards_dir)

        ff = FeatureFile.create(
            "test", "Test Feature", "Test desc",
            item_type="feature", done_script="",
            requires_human_approval=False
        )

        data = json.loads(ff.path.read_text())
        assert data["requires_human_approval"] is False
        assert ff.requires_human_approval is False

    def test_create_default_flag_false(self, tmp_path, monkeypatch):
        """Test that create() defaults requires_human_approval to False."""
        test_boards_dir = tmp_path / "boards"
        test_boards_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("state.Config.boards_dir", test_boards_dir)
        monkeypatch.setattr("config.Config.boards_dir", test_boards_dir)

        ff = FeatureFile.create(
            "test", "Test Feature", "Test desc"
        )

        data = json.loads(ff.path.read_text())
        assert data["requires_human_approval"] is False
        assert ff.requires_human_approval is False


class TestMoveToStageAwaitingHumanApproval:
    """Test move_to_stage with awaiting-human-approval."""

    def test_move_to_awaiting_human_approval_succeeds(self, tmp_path, monkeypatch):
        """Test that move_to_stage('awaiting-human-approval') works."""
        test_boards_dir = tmp_path / "boards"
        test_boards_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("state.Config.boards_dir", test_boards_dir)
        monkeypatch.setattr("config.Config.boards_dir", test_boards_dir)

        ideas_dir = test_boards_dir / "test" / "ideas"
        awaiting_dir = test_boards_dir / "test" / "awaiting-human-approval"
        ideas_dir.mkdir(parents=True)
        awaiting_dir.mkdir(parents=True)

        ff = FeatureFile.create("test", "Test Feature")
        ff.move_to_stage("awaiting-human-approval")
        assert ff.current_stage == "awaiting-human-approval"


class TestPlanReviewRouting:
    """Test plan review routing logic for requires_human_approval."""

    def test_conditional_routing_exists_in_phases(self):
        """Test that phases.py has conditional routing for requires_human_approval."""
        phases_file = Path(__file__).parent / "phases.py"
        content = phases_file.read_text()
        assert "requires_human_approval" in content
        assert 'if feature.requires_human_approval:' in content
        assert 'awaiting-human-approval' in content


class TestHumanStagesIncludeAwaitingHumanApproval:
    """Test that HUMAN_STAGES includes awaiting-human-approval."""

    def test_tui_human_stages_includes_awaiting(self):
        """Test that tui.py HUMAN_STAGES includes awaiting-human-approval."""
        tui_file = Path(__file__).parent / "tui.py"
        content = tui_file.read_text()
        assert '"awaiting-human-approval"' in content
        human_stages_line = None
        for line in content.split('\n'):
            if 'HUMAN_STAGES' in line and '=' in line:
                human_stages_line = line
                break
        assert human_stages_line is not None
        assert 'awaiting-human-approval' in human_stages_line

    def test_pipeline_human_stages_includes_awaiting(self):
        """Test that pipeline.py human_stages includes awaiting-human-approval."""
        pipeline_file = Path(__file__).parent / "pipeline.py"
        content = pipeline_file.read_text()
        human_stages_line = None
        for line in content.split('\n'):
            if 'human_stages' in line and '=' in line and '{' in line:
                human_stages_line = line
                break
        assert human_stages_line is not None
        assert 'awaiting-human-approval' in human_stages_line


class TestWebUIStageRegistration:
    """Test that web UI has awaiting-human-approval registered."""

    def test_all_stages_in_index_html(self):
        """Test that index.html ALL_STAGES includes awaiting-human-approval."""
        html_file = Path(__file__).parent.parent / "server" / "templates" / "index.html"
        content = html_file.read_text()
        assert "awaiting-human-approval" in content
        assert "ALL_STAGES" in content


class TestServerAPIGoStageOrder:
    """Test that api.go stageOrder includes awaiting-human-approval."""

    def test_stage_order_includes_awaiting(self):
        """Test that api.go stageOrder includes awaiting-human-approval."""
        api_file = Path(__file__).parent.parent / "server" / "api.go"
        content = api_file.read_text()
        assert 'awaiting-human-approval' in content
        assert 'stageOrder' in content
