"""Tests for item history cleanup (add_history ANSI stripping and truncation).

Run with:
    pytest test_history_cleanup.py -v
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from state import FeatureFile, MAX_HISTORY_NOTE_LENGTH


class TestAddHistoryCleanup:
    """Tests for add_history() ANSI stripping and truncation."""

    def test_ansi_stripping(self):
        """Test that add_history strips ANSI escape codes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.add_history("IDEAS", "\x1b[31mRed text\x1b[0m")

                stored_note = feature._data["history"][-1]["note"]
                assert stored_note == "Red text"
                assert "\x1b[" not in stored_note

    def test_truncation_over_100_chars(self):
        """Test that notes longer than 100 chars are truncated to 97 + '...'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                long_note = "A" * 150
                feature.add_history("IDEAS", long_note)

                stored_note = feature._data["history"][-1]["note"]
                assert len(stored_note) == 100
                assert stored_note == "A" * 97 + "..."

    def test_note_exactly_100_chars_not_truncated(self):
        """Test that a note exactly 100 chars is stored as-is."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                exact_100 = "B" * 100
                feature.add_history("IDEAS", exact_100)

                stored_note = feature._data["history"][-1]["note"]
                assert stored_note == exact_100
                assert len(stored_note) == 100

    def test_note_101_chars_truncated_to_100(self):
        """Test that a 101-char note is truncated to 100 chars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                note_101 = "C" * 101
                feature.add_history("IDEAS", note_101)

                stored_note = feature._data["history"][-1]["note"]
                assert len(stored_note) == 100
                assert stored_note == "C" * 97 + "..."

    def test_combined_ansi_stripping_and_truncation(self):
        """Test ANSI stripping happens before truncation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                note_with_ansi = "\x1b[31m" + ("D" * 150) + "\x1b[0m"
                feature.add_history("IDEAS", note_with_ansi)

                stored_note = feature._data["history"][-1]["note"]
                assert "\x1b[" not in stored_note
                assert len(stored_note) == 100
                assert stored_note == "D" * 97 + "..."

    def test_short_clean_notes_unchanged(self):
        """Test that short clean notes pass through unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                short_note = "This is a short note"
                feature.add_history("IDEAS", short_note)

                stored_note = feature._data["history"][-1]["note"]
                assert stored_note == short_note

    def test_whitespace_trimming(self):
        """Test that leading/trailing whitespace is stripped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.add_history("IDEAS", "   Some note   ")

                stored_note = feature._data["history"][-1]["note"]
                assert stored_note == "Some note"

    def test_empty_string_note(self):
        """Test that empty string notes are handled without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.add_history("IDEAS", "")

                stored_note = feature._data["history"][-1]["note"]
                assert stored_note == ""

    def test_note_only_ansi_codes(self):
        """Test that a note with only ANSI codes becomes empty after stripping."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.add_history("IDEAS", "\x1b[31m\x1b[0m")

                stored_note = feature._data["history"][-1]["note"]
                assert stored_note == ""

    def test_multiple_ansi_sequences(self):
        """Test stripping multiple ANSI sequences."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.add_history("IDEAS", "\x1b[1m\x1b[31mBold Red\x1b[0m normal \x1b[4munderline\x1b[0m")

                stored_note = feature._data["history"][-1]["note"]
                assert stored_note == "Bold Red normal underline"

    def test_note_with_only_whitespace_after_ansi(self):
        """Test note that becomes only whitespace after ANSI stripping."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.add_history("IDEAS", "\x1b[31m   \x1b[0m")

                stored_note = feature._data["history"][-1]["note"]
                assert stored_note == ""

    def test_unicode_characters(self):
        """Test that unicode characters are handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                unicode_note = "Emoji: 😀 " * 20
                feature.add_history("IDEAS", unicode_note)

                stored_note = feature._data["history"][-1]["note"]
                assert len(stored_note) == 100
                assert stored_note.endswith("...")

    def test_stage_uppercased(self):
        """Test that stage parameter is uppercased."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")

                feature.add_history("ideas", "some note")

                stored_stage = feature._data["history"][-1]["stage"]
                assert stored_stage == "IDEAS"

    def test_max_history_note_length_constant(self):
        """Test that MAX_HISTORY_NOTE_LENGTH is set to 100."""
        assert MAX_HISTORY_NOTE_LENGTH == 100
