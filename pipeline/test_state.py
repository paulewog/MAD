"""Unit tests for state.py — FeatureFile module."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from state import FeatureFile, _slugify, STAGES


class MockConfig:
    """Mock Config for testing."""
    def __init__(self, boards_dir: Path):
        self._boards_dir = boards_dir

    @property
    def boards_dir(self) -> Path:
        return self._boards_dir

    @property
    def boards(self) -> list:
        return ["default"]

    @property
    def mad_dir(self) -> Path:
        return self._boards_dir.parent


class TestSlugify(unittest.TestCase):
    """Tests for _slugify helper function."""

    def test_slugify_basic(self):
        """"My Feature" → "my-feature" """
        result = _slugify("My Feature")
        self.assertEqual(result, "my-feature")

    def test_slugify_special_chars(self):
        """Unicode characters handled correctly."""
        result = _slugify("Hello! World @#$")
        self.assertEqual(result, "hello-world")

    def test_slugify_multiple_spaces(self):
        """Multiple spaces collapsed to single hyphen."""
        result = _slugify("foo   bar")
        self.assertEqual(result, "foo-bar")

    def test_slugify_leading_trailing_hyphens(self):
        """Leading/trailing hyphens stripped."""
        result = _slugify("-foo-")
        self.assertEqual(result, "foo")

    def test_slugify_empty_string(self):
        """Empty string returns empty string."""
        result = _slugify("")
        self.assertEqual(result, "")


class TestFeatureFileCreate(unittest.TestCase):
    """Tests for FeatureFile.create() method."""

    def setUp(self):
        """Create temp directory structure."""
        self.temp_dir = Path("/tmp/test-state-create")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.boards_dir = self.temp_dir / "boards"  # boards_dir is parent of board directories
        self.boards_dir.mkdir(parents=True, exist_ok=True)
        self.mock_config = MockConfig(self.boards_dir)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_create_basic(self):
        """Create feature, verify JSON on disk has correct id, board, title, type, history entry."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.create("default", "My Test Feature")

        self.assertTrue(feature.path.exists())
        data = json.loads(feature.path.read_text())

        self.assertIn("id", data)
        self.assertEqual(len(data["id"]), 8)
        self.assertEqual(data["board"], "default")
        self.assertEqual(data["title"], "My Test Feature")
        self.assertEqual(data["type"], "feature")
        self.assertIn("created", data)
        self.assertEqual(len(data["history"]), 1)
        self.assertEqual(data["history"][0]["note"], "Idea created")
        self.assertEqual(data["ideation_prompt"], "")

    def test_create_with_description(self):
        """Verify description field is set."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.create("default", "My Feature", description="Test description")

        data = json.loads(feature.path.read_text())
        self.assertEqual(data["description"], "Test description")

    def test_create_default_description(self):
        """Empty description → "No description provided."."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.create("default", "My Feature")

        data = json.loads(feature.path.read_text())
        self.assertEqual(data["description"], "No description provided.")

    def test_create_bug_type(self):
        """item_type="bug" sets type field correctly."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.create("default", "Bug Report", item_type="bug")

        data = json.loads(feature.path.read_text())
        self.assertEqual(data["type"], "bug")

    def test_create_with_done_script(self):
        """done_script param persisted."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.create("default", "My Feature", done_script="test-script")

        data = json.loads(feature.path.read_text())
        self.assertEqual(data["done_script"], "test-script")

    def test_create_with_human_approval(self):
        """requires_human_approval=True persisted."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.create("default", "My Feature", requires_human_approval=True)

        data = json.loads(feature.path.read_text())
        self.assertEqual(data["requires_human_approval"], True)

    def test_create_directory_structure(self):
        """ideas/ dir created if missing."""
        # Remove the default board directory to simulate missing structure
        default_board_dir = self.boards_dir / "default"
        if default_board_dir.exists():
            import shutil
            shutil.rmtree(default_board_dir)
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.create("default", "My Feature")

        ideas_dir = self.boards_dir / "default" / "ideas"
        self.assertTrue(ideas_dir.exists())

    def test_create_slug_from_title(self):
        """Filename matches _slugify(title)."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.create("default", "My Test Feature")

        self.assertEqual(feature.slug, "my-test-feature")


class TestFeatureFileProperties(unittest.TestCase):
    """Tests for FeatureFile property accessors."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-props")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.temp_dir / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "Test description",
            "plan": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "ideation_summaries": [],
            "Ideation": "",
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.mock_config = MockConfig(self.temp_dir / "boards" / "default")
        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_properties_basic(self):
        """id, board, title, slug, created, current_stage all return correct values."""
        self.assertEqual(self.feature.id, "abc12345")
        self.assertEqual(self.feature.board, "default")
        self.assertEqual(self.feature.title, "Test Feature")
        self.assertEqual(self.feature.slug, "test-feature")
        self.assertEqual(self.feature.created, "2026-01-01T00:00Z")

    def test_current_stage_from_directory(self):
        """Stage derived from parent dir name."""
        self.assertEqual(self.feature.current_stage, "ideas")

    def test_item_type_default(self):
        """Missing "type" key → "feature"."""
        del self.feature_data["type"]
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        feature = FeatureFile(self.feature_path)
        self.assertEqual(feature.item_type, "feature")

    def test_requires_human_approval_default(self):
        """Missing key → False."""
        self.assertEqual(self.feature.requires_human_approval, False)

    def test_requires_human_approval_non_bool(self):
        """Non-True value → False."""
        self.feature_data["requires_human_approval"] = "yes"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        feature = FeatureFile(self.feature_path)
        self.assertEqual(feature.requires_human_approval, False)


class TestFeatureFileSetters(unittest.TestCase):
    """Tests for FeatureFile field setters."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-setters")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.temp_dir / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "Test description",
            "plan": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.mock_config = MockConfig(self.temp_dir / "boards" / "default")
        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_set_title(self):
        """Updates title in JSON, adds history entry."""
        self.feature.set_title("New Title")

        data = json.loads(self.feature.path.read_text())
        self.assertEqual(data["title"], "New Title")
        self.assertTrue(len(data["history"]) > 0)

    def test_set_title_empty_raises(self):
        """ValueError on empty title."""
        with self.assertRaises(ValueError):
            self.feature.set_title("")

    def test_set_item_type_valid(self):
        """"bug" accepted."""
        self.feature.set_item_type("bug")
        self.assertEqual(self.feature.item_type, "bug")

    def test_set_item_type_invalid_raises(self):
        """ValueError on "epic"."""
        with self.assertRaises(ValueError):
            self.feature.set_item_type("epic")

    def test_set_description(self):
        """Persists to disk."""
        self.feature.set_description("New description")
        data = json.loads(self.feature.path.read_text())
        self.assertEqual(data["description"], "New description")

    def test_set_plan(self):
        """Persists to disk."""
        self.feature.set_plan("The plan")
        self.assertEqual(self.feature.plan, "The plan")

    def test_set_impl_spec(self):
        """Persists to disk."""
        self.feature.set_impl_spec("impl spec content")
        self.assertEqual(self.feature.impl_spec, "impl spec content")

    def test_set_test_spec(self):
        """Persists to disk."""
        self.feature.set_test_spec("test spec content")
        self.assertEqual(self.feature.test_spec, "test spec content")

    def test_set_impl_notes(self):
        """Persists to disk."""
        self.feature.set_impl_notes("impl notes content")
        self.assertEqual(self.feature.impl_notes, "impl notes content")

    def test_set_design_ref(self):
        """Persists to disk."""
        self.feature.set_design_ref("https://example.com")
        self.assertEqual(self.feature.design_ref, "https://example.com")


class TestHistoryAndPipelineLog(unittest.TestCase):
    """Tests for history and pipeline log operations."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-history")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.temp_dir / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "",
            "plan": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_add_history(self):
        """Appends entry with ts, stage, note."""
        self.feature.add_history("IDEAS", "Test note")

        data = json.loads(self.feature.path.read_text())
        self.assertEqual(len(data["history"]), 1)
        self.assertIn("ts", data["history"][0])
        self.assertEqual(data["history"][0]["stage"], "IDEAS")
        self.assertEqual(data["history"][0]["note"], "Test note")

    def test_add_history_ansi_strip(self):
        """ANSI codes removed from note."""
        self.feature.add_history("IDEAS", "Test \x1b[31mred\x1b[0m note")

        data = json.loads(self.feature.path.read_text())
        self.assertEqual(data["history"][0]["note"], "Test red note")

    def test_add_history_truncation(self):
        """Notes > 100 chars truncated with "..."."""
        long_note = "A" * 150
        self.feature.add_history("IDEAS", long_note)

        data = json.loads(self.feature.path.read_text())
        self.assertEqual(len(data["history"][0]["note"]), 100)

    def test_history_property_format(self):
        """Returns formatted string with "- ts | STAGE | note" lines."""
        self.feature.add_history("IDEAS", "Test note")
        formatted = self.feature.history

        self.assertIn("|", formatted)
        self.assertIn("IDEAS", formatted)
        self.assertIn("Test note", formatted)

    def test_append_pipeline_log(self):
        """Appends entry with ts, phase, output."""
        self.feature.append_pipeline_log("planning", "Some output")

        data = json.loads(self.feature.path.read_text())
        self.assertEqual(len(data["pipeline_log"]), 1)
        self.assertIn("ts", data["pipeline_log"][0])
        self.assertEqual(data["pipeline_log"][0]["phase"], "planning")
        self.assertEqual(data["pipeline_log"][0]["output"], "Some output")

    def test_pipeline_log_property_format(self):
        """Returns formatted string with "### ts — phase" sections."""
        self.feature.append_pipeline_log("planning", "Some output")
        formatted = self.feature.pipeline_log

        self.assertIn("###", formatted)
        self.assertIn("planning", formatted)


class TestReviews(unittest.TestCase):
    """Tests for review management."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-reviews")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.temp_dir / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "",
            "plan": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_add_plan_review_pass(self):
        """verdict normalized to "PASS"."""
        self.feature.add_plan_review("pass", "Looks good", None)
        self.assertEqual(self.feature.get_latest_plan_review()["verdict"], "PASS")

    def test_add_plan_review_fail(self):
        """verdict normalized to "FAIL" for non-"PASS" input."""
        self.feature.add_plan_review("fail", "Needs work", "Fix this")
        self.assertEqual(self.feature.get_latest_plan_review()["verdict"], "FAIL")

    def test_add_impl_review(self):
        """Appends to impl_reviews array."""
        self.feature.add_impl_review("PASS", "Looks good", None)
        self.assertEqual(len(self.feature.impl_reviews), 1)

    def test_add_spec_review(self):
        """Appends to spec_reviews array."""
        self.feature.add_spec_review("PASS", "Looks good", None)
        self.assertEqual(len(self.feature.spec_reviews), 1)

    def test_get_latest_plan_review_empty(self):
        """Returns None when no reviews."""
        self.assertIsNone(self.feature.get_latest_plan_review())

    def test_get_latest_plan_review(self):
        """Returns last entry."""
        self.feature.add_plan_review("PASS", "First", None)
        self.feature.add_plan_review("FAIL", "Second", "Fix")

        latest = self.feature.get_latest_plan_review()
        self.assertEqual(latest["summary"], "Second")

    def test_get_latest_impl_review(self):
        """Returns last entry."""
        self.feature.add_impl_review("PASS", "First", None)
        self.feature.add_impl_review("FAIL", "Second", "Fix")

        latest = self.feature.get_latest_impl_review()
        self.assertEqual(latest["summary"], "Second")

    def test_get_latest_spec_review(self):
        """Returns last entry."""
        self.feature.add_spec_review("PASS", "First", None)
        self.feature.add_spec_review("FAIL", "Second", "Fix")

        latest = self.feature.get_latest_spec_review()
        self.assertEqual(latest["summary"], "Second")


class TestQuestions(unittest.TestCase):
    """Tests for question management."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-questions")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.temp_dir / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "",
            "plan": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
            "questions": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_add_question(self):
        """Appends {question, answer: ""}."""
        self.feature.add_question("What is this?")

        self.assertEqual(len(self.feature.questions), 1)
        self.assertEqual(self.feature.questions[0]["question"], "What is this?")
        self.assertEqual(self.feature.questions[0]["answer"], "")

    def test_answer_question(self):
        """Sets answer by index."""
        self.feature.add_question("What is this?")
        self.feature.answer_question(0, "It's a feature")

        self.assertEqual(self.feature.questions[0]["answer"], "It's a feature")

    def test_answer_question_out_of_range(self):
        """No crash on invalid index."""
        self.feature.add_question("What is this?")
        self.feature.answer_question(5, "Answer")

        self.assertEqual(self.feature.questions[0]["answer"], "")

    def test_set_questions_preserves_answers(self):
        """Existing answers merged into new question set."""
        self.feature.add_question("Q1")
        self.feature.answer_question(0, "A1")

        self.feature.set_questions([{"question": "Q1"}, {"question": "Q2"}])

        self.assertEqual(len(self.feature.questions), 2)
        self.assertEqual(self.feature.questions[0]["answer"], "A1")
        self.assertEqual(self.feature.questions[1]["answer"], "")

    def test_set_questions_new_question(self):
        """New question gets empty answer."""
        self.feature.set_questions([{"question": "New Q?"}])

        self.assertEqual(self.feature.questions[0]["answer"], "")


class TestIdeation(unittest.TestCase):
    """Tests for ideation operations."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-ideation")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.temp_dir / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "",
            "plan": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_add_ideation_summary(self):
        """Appends to list."""
        self.feature.add_ideation_summary("Summary 1")

        self.assertEqual(len(self.feature.ideation_summaries), 1)
        self.assertEqual(self.feature.ideation_summaries[0], "Summary 1")

    def test_set_Ideation(self):
        """Sets Ideation field."""
        self.feature.set_Ideation("Ideation content")

        self.assertEqual(self.feature.Ideation, "Ideation content")

    def test_ideation_summaries_default(self):
        """Returns [] when missing."""
        self.assertEqual(self.feature.ideation_summaries, [])

    def test_ideation_prompt_default(self):
        """Missing ideation_prompt key → empty string."""
        self.assertEqual(self.feature.ideation_prompt, "")

    def test_set_ideation_prompt_persists(self):
        """set_ideation_prompt writes to disk."""
        self.feature.set_ideation_prompt("Focus on security")

        data = json.loads(self.feature.path.read_text())
        self.assertEqual(data["ideation_prompt"], "Focus on security")

    def test_set_ideation_prompt_round_trip(self):
        """Set, reload from disk, verify value."""
        self.feature.set_ideation_prompt("Focus on security")

        reloaded = FeatureFile(self.feature.path)
        self.assertEqual(reloaded.ideation_prompt, "Focus on security")

    def test_get_section_ideation_prompt(self):
        """get_section('Ideation Prompt') returns ideation_prompt."""
        self.feature.set_ideation_prompt("Focus on security")
        result = self.feature.get_section("Ideation Prompt")
        self.assertEqual(result, "Focus on security")

    def test_set_section_ideation_prompt(self):
        """set_section('Ideation Prompt', ...) sets ideation_prompt."""
        self.feature.set_section("Ideation Prompt", "Focus on performance")
        self.assertEqual(self.feature.ideation_prompt, "Focus on performance")

    def test_set_section_ideation_prompt_round_trip(self):
        """set_section + get_section round-trip for Ideation Prompt."""
        self.feature.set_section("Ideation Prompt", "Focus on performance")
        result = self.feature.get_section("Ideation Prompt")
        self.assertEqual(result, "Focus on performance")

    def test_ideation_cycle_verdicts_default(self):
        """Returns [] when missing."""
        self.assertEqual(self.feature.ideation_cycle_verdicts, [])

    def test_add_ideation_cycle_verdict(self):
        """Appends verdict record to list."""
        self.feature.add_ideation_cycle_verdict(1, "consensus", "Agents agree", 4)

        verdicts = self.feature.ideation_cycle_verdicts
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["cycle"], 1)
        self.assertEqual(verdicts[0]["verdict"], "consensus")
        self.assertEqual(verdicts[0]["reason"], "Agents agree")
        self.assertEqual(verdicts[0]["rounds_completed"], 4)

    def test_add_ideation_cycle_verdict_multiple(self):
        """Multiple calls append, not overwrite."""
        self.feature.add_ideation_cycle_verdict(1, "progress", "New ideas", 4)
        self.feature.add_ideation_cycle_verdict(2, "consensus", "Agents agree", 8)

        verdicts = self.feature.ideation_cycle_verdicts
        self.assertEqual(len(verdicts), 2)
        self.assertEqual(verdicts[0]["verdict"], "progress")
        self.assertEqual(verdicts[1]["verdict"], "consensus")

    def test_clear_ideation_cycle_verdicts(self):
        """Resets list to []."""
        self.feature.add_ideation_cycle_verdict(1, "progress", "New ideas", 4)
        self.feature.clear_ideation_cycle_verdicts()

        self.assertEqual(self.feature.ideation_cycle_verdicts, [])

    def test_ideation_cycle_verdicts_persistence(self):
        """Verdicts persist to disk after save/reload."""
        self.feature.add_ideation_cycle_verdict(1, "consensus", "Agents agree", 4)

        reloaded = FeatureFile(self.feature.path)
        verdicts = reloaded.ideation_cycle_verdicts

        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["cycle"], 1)
        self.assertEqual(verdicts[0]["verdict"], "consensus")

    def test_ideation_max_rounds_default(self):
        """Returns None when missing."""
        self.assertIsNone(self.feature.ideation_max_rounds)

    def test_set_ideation_max_rounds(self):
        """Sets the per-item max rounds override."""
        self.feature.set_ideation_max_rounds(24)

        self.assertEqual(self.feature.ideation_max_rounds, 24)

    def test_set_ideation_max_rounds_none(self):
        """Setting None removes key from JSON."""
        self.feature.set_ideation_max_rounds(24)
        self.feature.set_ideation_max_rounds(None)

        self.assertIsNone(self.feature.ideation_max_rounds)

        data = json.loads(self.feature.path.read_text())
        self.assertNotIn("ideation_max_rounds", data)

    def test_ideation_max_rounds_persistence(self):
        """Per-item override persists to disk after save/reload."""
        self.feature.set_ideation_max_rounds(16)

        reloaded = FeatureFile(self.feature.path)
        self.assertEqual(reloaded.ideation_max_rounds, 16)


class TestStageTransitions(unittest.TestCase):
    """Tests for stage transition operations."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-stage")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.boards_dir = self.temp_dir / "boards"  # boards_dir is parent of board directories
        self.boards_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.boards_dir / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)
        self.plan_dir = self.boards_dir / "default" / "plan-inbox"
        self.plan_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "",
            "plan": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.mock_config = MockConfig(self.boards_dir)
        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_move_to_stage(self):
        """File moved to target stage directory."""
        with patch('state.Config', return_value=self.mock_config):
            self.feature.move_to_stage("plan-inbox")

        new_path = self.plan_dir / "test-feature.json"
        self.assertTrue(new_path.exists())
        self.assertFalse(self.feature_path.exists())
        self.assertEqual(self.feature.current_stage, "plan-inbox")

    def test_move_to_stage_invalid_raises(self):
        """ValueError on unknown stage."""
        with self.assertRaises(ValueError):
            with patch('state.Config', return_value=self.mock_config):
                self.feature.move_to_stage("invalid-stage")

    def test_move_to_stage_creates_dir(self):
        """Target dir created if missing."""
        self.plan_dir.rmdir()

        with patch('state.Config', return_value=self.mock_config):
            self.feature.move_to_stage("plan-inbox")

        self.assertTrue(self.plan_dir.exists())


class TestFinders(unittest.TestCase):
    """Tests for FeatureFile finder methods."""

    def setUp(self):
        """Create temp directory structure with multiple feature files."""
        self.temp_dir = Path("/tmp/test-state-finders")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.boards_dir = self.temp_dir / "boards"  # boards_dir is parent of board directories
        self.boards_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.boards_dir / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "",
            "plan": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.mock_config = MockConfig(self.boards_dir)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_find_by_slug(self):
        """Exact slug match."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.find("test-feature")

        self.assertIsNotNone(feature)
        self.assertEqual(feature.title, "Test Feature")

    def test_find_by_id(self):
        """ID match."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.find("abc12345")

        self.assertIsNotNone(feature)
        self.assertEqual(feature.title, "Test Feature")

    def test_find_by_partial_slug(self):
        """Partial match returns shortest slug."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.find("test")

        self.assertIsNotNone(feature)
        self.assertEqual(feature.slug, "test-feature")

    def test_find_not_found(self):
        """Returns None."""
        with patch('state.Config', return_value=self.mock_config):
            feature = FeatureFile.find("nonexistent")

        self.assertIsNone(feature)

    def test_list_all(self):
        """Lists features across boards/stages."""
        with patch('state.Config', return_value=self.mock_config):
            features = FeatureFile.list_all()

        self.assertEqual(len(features), 1)

    def test_list_all_filtered_by_board(self):
        """Only returns features from specified board."""
        with patch('state.Config', return_value=self.mock_config):
            features = FeatureFile.list_all(board="default")

        self.assertEqual(len(features), 1)

    def test_list_all_filtered_by_stage(self):
        """Only returns features from specified stage."""
        with patch('state.Config', return_value=self.mock_config):
            features = FeatureFile.list_all(stage="ideas")

        self.assertEqual(len(features), 1)

    def test_list_all_skips_invalid_json(self):
        """Malformed JSON files logged and skipped."""
        bad_file = self.ideas_dir / "bad.json"
        bad_file.write_text("not valid json {")

        with patch('state.Config', return_value=self.mock_config):
            with patch('state.logger') as mock_logger:
                features = FeatureFile.list_all()
                self.assertEqual(len(features), 1)
                mock_logger.error.assert_called()


class TestBackwardCompat(unittest.TestCase):
    """Tests for backward compatibility features."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-backcompat")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.temp_dir / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "Test description",
            "plan": "The plan",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [{"ts": "2026-01-01T00:00Z", "stage": "IDEAS", "note": "Created"}],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_get_section_mapping(self):
        """Description → description field, Plan → plan field."""
        self.assertEqual(self.feature.get_section("Description"), "Test description")
        self.assertEqual(self.feature.get_section("Plan"), "The plan")

    def test_set_section_mapping(self):
        """Sets field via mapping."""
        self.feature.set_section("Description", "New desc")

        self.assertEqual(self.feature.description, "New desc")

    def test_get_section_history(self):
        """Returns formatted history string."""
        result = self.feature.get_section("History")
        self.assertIn("IDEAS", result)

    def test_get_section_pipeline_log(self):
        """Returns formatted pipeline log string."""
        result = self.feature.get_section("Pipeline Log")
        self.assertIsInstance(result, str)


class TestJsonRoundTrip(unittest.TestCase):
    """Tests for JSON persistence round-trip."""

    def setUp(self):
        """Create temp directory structure with a feature file."""
        self.temp_dir = Path("/tmp/test-state-roundtrip")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir = self.temp_dir / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        self.feature_data = {
            "id": "abc12345",
            "board": "default",
            "title": "Test Feature",
            "type": "feature",
            "created": "2026-01-01T00:00Z",
            "description": "Test description",
            "plan": "The plan",
            "impl_spec": "impl spec",
            "test_spec": "test spec",
            "impl_notes": "impl notes",
            "design_ref": "",
            "ideation_summaries": [],
            "Ideation": "",
            "done_script": "",
            "requires_human_approval": False,
            "history": [],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
            "spec_reviews": [],
        }

        self.feature_path = self.ideas_dir / "test-feature.json"
        with open(self.feature_path, "w") as f:
            json.dump(self.feature_data, f)
            f.write("\n")

        self.feature = FeatureFile(self.feature_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_json_persistence_round_trip(self):
        """Create, modify fields, reload from disk, verify all fields intact."""
        self.feature.set_description("Modified")
        self.feature.set_plan("New plan")

        reloaded = FeatureFile(self.feature.path)

        self.assertEqual(reloaded.description, "Modified")
        self.assertEqual(reloaded.plan, "New plan")
        self.assertEqual(reloaded.title, "Test Feature")

    def test_save_writes_trailing_newline(self):
        """JSON file ends with newline."""
        self.feature.save()

        content = self.feature.path.read_text()
        self.assertTrue(content.endswith("\n"))


if __name__ == '__main__':
    unittest.main()
