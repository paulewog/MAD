"""Tests for edit-feature CLI commands in pipeline.py.

Run with:
    pytest test_edit_feature.py -v
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import pipeline as pipeline_module
from state import FeatureFile


def run_cli_command(cli, mock_config, args):
    """Helper to run CLI with proper mocks."""
    runner = CliRunner()
    with patch("pipeline.pipeline.get_config", return_value=mock_config):
        with patch("state.Config", return_value=mock_config):
            return runner.invoke(cli, args)


def find_feature_with_mock(mock_config, slug):
    """Find a feature using the mock config."""
    with patch("state.Config", return_value=mock_config):
        return FeatureFile.find(slug)


class TestEditFeatureCLI:
    """Test cases for pipeline edit-feature CLI commands."""

    @pytest.fixture
    def temp_board_dir(self):
        """Create a temporary board directory with a test feature."""
        with tempfile.TemporaryDirectory() as tmpdir:
            board_dir = Path(tmpdir) / "testboard"
            ideas_dir = board_dir / "ideas"
            ideas_dir.mkdir(parents=True)
            
            feature_path = ideas_dir / "test-feature.json"
            feature_data = {
                "id": "test123",
                "board": "testboard",
                "title": "Test Feature",
                "type": "feature",
                "created": "2024-01-01T00:00:00Z",
                "description": "A test feature description",
                "done_script": "",
                "plan": "Original plan",
                "impl_spec": "",
                "test_spec": "",
                "impl_notes": "",
                "design_ref": "",
                "history": [],
                "pipeline_log": [],
                "plan_reviews": [],
                "impl_reviews": [],
                "questions": [],
                "test_results": {},
            }
            with open(feature_path, "w") as f:
                json.dump(feature_data, f, indent=2)
                f.write("\n")
            
            yield tmpdir, board_dir

    @pytest.fixture
    def mock_config(self, temp_board_dir):
        """Mock Config to use temp directory."""
        tmpdir, board_dir = temp_board_dir
        
        class MockConfig:
            def __init__(self):
                self.boards_dir = Path(tmpdir)
                self.boards = ["testboard"]
                self.mad_dir = Path(tmpdir) / ".mad"
                self.mad_dir.mkdir(exist_ok=True)
                self.agents = {}
                self.current_agent_name = "claude"
                
            def setup_boards(self):
                pass
        
        return MockConfig()

    def test_set_field_title(self, mock_config, temp_board_dir):
        """Test setting title field."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "title", "New Title"
        ])
        
        assert result.exit_code == 0
        assert "Set title on test-feature" in result.output
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.title == "New Title"

    def test_set_field_description(self, mock_config, temp_board_dir):
        """Test setting description field."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "description", "New description"
        ])
        
        assert result.exit_code == 0
        assert "Set description on test-feature" in result.output
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.description == "New description"

    def test_set_field_type_bug(self, mock_config, temp_board_dir):
        """Test setting type to bug."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "type", "bug"
        ])
        
        assert result.exit_code == 0
        assert "Set type on test-feature" in result.output
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.item_type == "bug"

    def test_set_field_type_feature(self, mock_config, temp_board_dir):
        """Test setting type to feature."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "type", "feature"
        ])
        
        assert result.exit_code == 0
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.item_type == "feature"

    def test_set_field_type_invalid(self, mock_config, temp_board_dir):
        """Test setting invalid type value."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "type", "invalid"
        ])
        
        assert result.exit_code == 1
        assert "Type must be" in result.output

    def test_set_field_plan_stdin(self, mock_config, temp_board_dir):
        """Test setting plan field via stdin."""
        cli = pipeline_module.cli
        runner = CliRunner()
        with patch("pipeline.pipeline.get_config", return_value=mock_config):
            with patch("state.Config", return_value=mock_config):
                result = runner.invoke(cli, [
                    "edit-feature", "test-feature", "set-field", "plan", "--stdin"
                ], input="Multi-line\nplan content\nhere")
        
        assert result.exit_code == 0
        assert "Set plan on test-feature" in result.output
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.plan == "Multi-line\nplan content\nhere"

    def test_set_field_impl_spec_file(self, mock_config, temp_board_dir):
        """Test setting impl_spec from file."""
        cli = pipeline_module.cli
        
        spec_file = Path(temp_board_dir[0]) / "spec.md"
        spec_file.write_text("Implementation spec content")
        
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "impl_spec", "--file", str(spec_file)
        ])
        
        assert result.exit_code == 0
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.impl_spec == "Implementation spec content"

    def test_set_field_invalid_field(self, mock_config, temp_board_dir):
        """Test setting invalid field name."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "invalid_field", "value"
        ])
        
        assert result.exit_code == 1
        assert "Invalid field" in result.output

    def test_set_field_questions(self, mock_config, temp_board_dir):
        """Test setting questions field."""
        cli = pipeline_module.cli
        questions_json = json.dumps([
            {"question": "Q1", "answer": ""},
            {"question": "Q2", "answer": ""}
        ])
        
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "questions", questions_json
        ])
        
        assert result.exit_code == 0
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert len(feature.questions) == 2
        assert feature.questions[0]["question"] == "Q1"

    def test_set_field_questions_invalid_json(self, mock_config, temp_board_dir):
        """Test setting questions with invalid JSON."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "questions", "not json"
        ])
        
        assert result.exit_code == 1

    def test_get_field_plan(self, mock_config, temp_board_dir):
        """Test getting plan field."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "get-field", "plan"
        ])
        
        assert result.exit_code == 0
        assert "Original plan" in result.output

    def test_get_field_plan_json(self, mock_config, temp_board_dir):
        """Test getting plan field as JSON."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "get-field", "plan", "--json"
        ])
        
        assert result.exit_code == 0
        assert result.output.strip() == '"Original plan"'

    def test_get_field_questions(self, mock_config, temp_board_dir):
        """Test getting questions field."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "get-field", "questions"
        ])
        
        assert result.exit_code == 0

    def test_set_test_results(self, mock_config, temp_board_dir):
        """Test setting test results."""
        cli = pipeline_module.cli
        test_results = json.dumps({
            "passed": 5,
            "failed": 1,
            "details": "All core tests passed, one edge case failed"
        })
        
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-test-results", test_results
        ])
        
        assert result.exit_code == 0
        assert "Set test_results on test-feature" in result.output
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.test_results["passed"] == 5
        assert feature.test_results["failed"] == 1

    def test_set_test_results_stdin(self, mock_config, temp_board_dir):
        """Test setting test results via stdin."""
        cli = pipeline_module.cli
        runner = CliRunner()
        with patch("pipeline.pipeline.get_config", return_value=mock_config):
            with patch("state.Config", return_value=mock_config):
                result = runner.invoke(cli, [
                    "edit-feature", "test-feature", "set-test-results", "--stdin"
                ], input='{"passed": 3, "failed": 0}')
        
        assert result.exit_code == 0
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.test_results["passed"] == 3

    def test_set_test_results_invalid_json(self, mock_config, temp_board_dir):
        """Test setting test results with invalid JSON."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-test-results", "not json"
        ])
        
        assert result.exit_code == 1

    def test_set_test_results_not_dict(self, mock_config, temp_board_dir):
        """Test setting test results with array instead of dict."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-test-results", "[1,2,3]"
        ])
        
        assert result.exit_code == 1
        assert "must be a JSON object" in result.output

    def test_feature_not_found(self, mock_config, temp_board_dir):
        """Test feature not found error."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "nonexistent-slug", "get-field", "title"
        ])
        
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "try" in result.output.lower()

    def test_set_field_empty_title(self, mock_config, temp_board_dir):
        """Test setting empty title (should fail)."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "title", ""
        ])
        
        assert result.exit_code == 1
        assert "empty" in result.output.lower()

    def test_file_not_found(self, mock_config, temp_board_dir):
        """Test --file with nonexistent path."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "impl_spec", "--file", "/nonexistent/file.md"
        ])
        
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_no_value_provided(self, mock_config, temp_board_dir):
        """Test set-field without value, --stdin, or --file."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "title"
        ])
        
        assert result.exit_code == 1
        assert "No value provided" in result.output or "error" in result.output.lower()

    def test_data_persistence(self, mock_config, temp_board_dir):
        """Test that other fields are untouched after modifying one field."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "title", "Updated Title"
        ])
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.title == "Updated Title"
        assert feature.description == "A test feature description"
        assert feature.plan == "Original plan"

    def test_json_file_valid(self, mock_config, temp_board_dir):
        """Test that JSON file remains valid after modifications."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "description", "New desc"
        ])
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        
        with open(feature.path) as f:
            data = json.load(f)
            assert data["description"] == "New desc"
            assert data["title"] == "Test Feature"

    def test_set_field_ideation_prompt(self, mock_config, temp_board_dir):
        """Test setting ideation_prompt field."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "ideation_prompt", "Focus on security"
        ])
        
        assert result.exit_code == 0
        assert "Set ideation_prompt on test-feature" in result.output
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.ideation_prompt == "Focus on security"

    def test_get_field_ideation_prompt(self, mock_config, temp_board_dir):
        """Test getting ideation_prompt field."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "get-field", "ideation_prompt"
        ])
        
        assert result.exit_code == 0

    def test_set_field_ideation_prompt_stdin(self, mock_config, temp_board_dir):
        """Test setting ideation_prompt field via stdin."""
        cli = pipeline_module.cli
        runner = CliRunner()
        with patch("pipeline.pipeline.get_config", return_value=mock_config):
            with patch("state.Config", return_value=mock_config):
                result = runner.invoke(cli, [
                    "edit-feature", "test-feature", "set-field", "ideation_prompt", "--stdin"
                ], input="Focus on performance")
        
        assert result.exit_code == 0
        assert "Set ideation_prompt on test-feature" in result.output
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.ideation_prompt == "Focus on performance"

    def test_set_field_ideation_prompt_file(self, mock_config, temp_board_dir):
        """Test setting ideation_prompt from file."""
        cli = pipeline_module.cli
        
        direction_file = Path(temp_board_dir[0]) / "direction.txt"
        direction_file.write_text("Focus on security concerns")
        
        result = run_cli_command(cli, mock_config, [
            "edit-feature", "test-feature", "set-field", "ideation_prompt", "--file", str(direction_file)
        ])
        
        assert result.exit_code == 0
        assert "Set ideation_prompt on test-feature" in result.output
        
        feature = find_feature_with_mock(mock_config, "test-feature")
        assert feature.ideation_prompt == "Focus on security concerns"


class TestListFeaturesCLI:
    """Test cases for pipeline ls CLI command."""

    @pytest.fixture
    def temp_board_dir(self):
        """Create a temporary board directory with test features."""
        with tempfile.TemporaryDirectory() as tmpdir:
            board_dir = Path(tmpdir) / "testboard"
            ideas_dir = board_dir / "ideas"
            ideas_dir.mkdir(parents=True)
            
            feature1_path = ideas_dir / "feature-one.json"
            feature1_data = {
                "id": "feat1",
                "board": "testboard",
                "title": "Feature One",
                "type": "feature",
                "created": "2024-01-01T00:00:00Z",
                "description": "",
                "done_script": "",
                "plan": "",
                "impl_spec": "",
                "test_spec": "",
                "impl_notes": "",
                "design_ref": "",
                "history": [],
                "pipeline_log": [],
                "plan_reviews": [],
                "impl_reviews": [],
                "questions": [],
                "test_results": {},
            }
            with open(feature1_path, "w") as f:
                json.dump(feature1_data, f, indent=2)
                f.write("\n")
            
            plan_dir = board_dir / "plan-inbox"
            plan_dir.mkdir(parents=True)
            
            feature2_path = plan_dir / "feature-two.json"
            feature2_data = {
                "id": "feat2",
                "board": "testboard",
                "title": "Feature Two",
                "type": "feature",
                "created": "2024-01-01T00:00:00Z",
                "description": "",
                "done_script": "",
                "plan": "Plan for feature two",
                "impl_spec": "",
                "test_spec": "",
                "impl_notes": "",
                "design_ref": "",
                "history": [],
                "pipeline_log": [],
                "plan_reviews": [],
                "impl_reviews": [],
                "questions": [],
                "test_results": {},
            }
            with open(feature2_path, "w") as f:
                json.dump(feature2_data, f, indent=2)
                f.write("\n")
            
            yield tmpdir, board_dir

    @pytest.fixture
    def mock_config(self, temp_board_dir):
        """Mock Config to use temp directory."""
        tmpdir, board_dir = temp_board_dir
        
        class MockConfig:
            def __init__(self):
                self.boards_dir = Path(tmpdir)
                self.boards = ["testboard"]
                self.mad_dir = Path(tmpdir) / ".mad"
                self.mad_dir.mkdir(exist_ok=True)
                self.agents = {}
                self.current_agent_name = "claude"
                
            def setup_boards(self):
                pass
        
        return MockConfig()

    def test_list_features(self, mock_config, temp_board_dir):
        """Test listing features across all boards."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, ["ls"])
        
        assert result.exit_code == 0
        assert "testboard/" in result.output
        assert "feature-one" in result.output
        assert "feature-two" in result.output

    def test_list_features_with_board_filter(self, mock_config, temp_board_dir):
        """Test listing features filtered by board."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, ["ls", "--board", "testboard"])
        
        assert result.exit_code == 0
        assert "testboard/" in result.output
        assert "feature-one" in result.output
        assert "feature-two" in result.output

    def test_list_features_with_stage_filter(self, mock_config, temp_board_dir):
        """Test listing features filtered by stage."""
        cli = pipeline_module.cli
        result = run_cli_command(cli, mock_config, ["ls", "--stage", "ideas"])
        
        assert result.exit_code == 0
        assert "feature-one" in result.output
        assert "feature-two" not in result.output
