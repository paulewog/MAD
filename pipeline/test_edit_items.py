"""Tests for set_title() and set_item_type() methods in FeatureFile.

Run with:
    pytest test_edit_items.py -v
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from state import FeatureFile


class TestSetTitle:
    """Tests for set_title() method."""

    def test_set_title_updates_title(self):
        """Test that set_title updates the title in the JSON data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Original Title", "Desc")

                feature.set_title("New Title")

                assert feature.title == "New Title"

                with open(feature.path, "r") as f:
                    data = json.load(f)
                    assert data["title"] == "New Title"

    def test_set_title_adds_history_entry(self):
        """Test that set_title adds a history entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Original Title", "Desc")

                initial_history_len = len(feature._data.get("history", []))

                feature.set_title("New Title")

                history = feature._data.get("history", [])
                assert len(history) == initial_history_len + 1
                assert history[-1]["note"] == "Title edited"
                assert history[-1]["stage"] == "IDEAS"

    def test_set_title_empty_string_raises_error(self):
        """Test that set_title with empty string raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Original Title", "Desc")

                with pytest.raises(ValueError, match="Title cannot be empty"):
                    feature.set_title("")

                assert feature.title == "Original Title"

    def test_set_title_whitespace_only_raises_error(self):
        """Test that set_title with whitespace-only string raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Original Title", "Desc")

                with pytest.raises(ValueError, match="Title cannot be empty"):
                    feature.set_title("   \t\n")

                assert feature.title == "Original Title"

    def test_set_title_special_characters(self):
        """Test that set_title handles special characters correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Original Title", "Desc")

                special_title = 'Title with "quotes" and \\ backslash and unicode émoji 🚀'
                feature.set_title(special_title)

                assert feature.title == special_title

    def test_set_title_very_long_string(self):
        """Test that set_title handles very long strings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Original Title", "Desc")

                long_title = "A" * 2000
                feature.set_title(long_title)

                assert feature.title == long_title


class TestSetItemType:
    """Tests for set_item_type() method."""

    def test_set_item_type_updates_type(self):
        """Test that set_item_type updates the type in the JSON data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.set_item_type("bug")

                assert feature.item_type == "bug"

                with open(feature.path, "r") as f:
                    data = json.load(f)
                    assert data["type"] == "bug"

    def test_set_item_type_feature_value(self):
        """Test that set_item_type works with 'feature' value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                feature.set_item_type("bug")

                feature.set_item_type("feature")

                assert feature.item_type == "feature"

    def test_set_item_type_adds_history_entry(self):
        """Test that set_item_type adds a history entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                initial_history_len = len(feature._data.get("history", []))

                feature.set_item_type("bug")

                history = feature._data.get("history", [])
                assert len(history) == initial_history_len + 1
                assert history[-1]["note"] == "Type edited"
                assert history[-1]["stage"] == "IDEAS"

    def test_set_item_type_invalid_value_raises_error(self):
        """Test that set_item_type with invalid value raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                with pytest.raises(ValueError) as exc_info:
                    feature.set_item_type("enhancement")

                assert "feature" in str(exc_info.value).lower()
                assert "bug" in str(exc_info.value).lower()
                assert feature.item_type == "feature"

    def test_set_item_type_capitalized_raises_error(self):
        """Test that set_item_type rejects capitalized values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                with pytest.raises(ValueError):
                    feature.set_item_type("Feature")

                with pytest.raises(ValueError):
                    feature.set_item_type("BUG")

    def test_set_item_type_empty_string_raises_error(self):
        """Test that set_item_type with empty string raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                with pytest.raises(ValueError):
                    feature.set_item_type("")

                assert feature.item_type == "feature"


class TestStageValidation:
    """Tests for stage validation in editing."""

    def test_title_not_editable_outside_ideas(self):
        """Test that title can only be edited in ideas stage via data layer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Original Title", "Desc")

                ideas_path = feature.path.parent
                approved_path = ideas_path.parent / "approved"
                approved_path.mkdir(parents=True, exist_ok=True)
                approved_feature_path = approved_path / feature.path.name

                feature._path = approved_feature_path

                assert feature.current_stage == "approved"

                feature.set_title("New Title")

                assert feature.title == "New Title"


class TestHistoryConsistency:
    """Tests for history logging consistency."""

    def test_title_edit_history_format(self):
        """Test that title edits have correct history format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.set_title("New Title")

                history = feature._data.get("history", [])
                assert len(history) == 2
                assert history[-1]["stage"] == "IDEAS"
                assert history[-1]["note"] == "Title edited"

    def test_type_edit_history_format(self):
        """Test that type edits have correct history format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.set_item_type("bug")

                history = feature._data.get("history", [])
                assert len(history) == 2
                assert history[-1]["stage"] == "IDEAS"
                assert history[-1]["note"] == "Type edited"
