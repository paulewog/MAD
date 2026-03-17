"""Tests for Move to Ideating button in client.html.

Run with:
    pytest test_move_to_ideating_button.py -v
"""

import re
from pathlib import Path

import pytest


class TestMoveToIdeatingButton:
    """Tests for the Move to Ideating button in client.html."""

    @pytest.fixture
    def client_html_path(self):
        """Return path to client.html template."""
        path = Path(__file__).parent.parent / "server" / "templates" / "client.html"
        if not path.exists():
            pytest.skip("client.html template not found")
        return path

    @pytest.fixture
    def client_html_content(self, client_html_path):
        """Return contents of client.html."""
        return client_html_path.read_text()

    def test_move_to_ideating_button_exists(self, client_html_content):
        """Verify that Move to Ideating button exists in client.html."""
        pattern = r'Move to Ideating'
        assert re.search(pattern, client_html_content), \
            "Move to Ideating button must exist in client.html"

    def test_button_hidden_when_in_ideating_stage(self, client_html_content):
        """Verify button only shows when stage is NOT ideating."""
        pattern = r"if\s*\(\s*stage\s*!==\s*['\"]ideating['\"]"
        assert re.search(pattern, client_html_content), \
            "Button should only appear when stage !== 'ideating'"

    def test_button_calls_move_feature_with_ideating(self, client_html_content):
        """Verify button calls moveFeature with 'ideating' target."""
        assert "moveFeature" in client_html_content and "'ideating'" in client_html_content, \
            "moveFeature should be called with 'ideating' target"

    def test_button_has_correct_styling(self, client_html_content):
        """Verify button uses correct amber/gold styling (#9e6a03)."""
        assert "#9e6a03" in client_html_content, \
            "Move to Ideating button must use #9e6a03 background color"

    def test_move_feedback_span_exists(self, client_html_content):
        """Verify move-feedback span exists for feedback messages."""
        pattern = r'<span[^>]*class="move-feedback"'
        matches = re.findall(pattern, client_html_content)
        assert len(matches) >= 2, \
            "move-feedback span should exist for both ideas buttons and new Move to Ideating button"

    def test_feature_id_is_escaped(self, client_html_content):
        """Verify feature ID is escaped to prevent XSS."""
        assert "escapeHtml" in client_html_content, \
            "featureID should be escaped using escapeHtml() to prevent XSS"

    def test_button_in_separate_block(self, client_html_content):
        """Verify Move to Ideating button is in its own conditional block."""
        assert "// Move to Ideating button" in client_html_content, \
            "Move to Ideating button should have its own comment section"

    def test_coexistence_with_ideas_stage_buttons(self, client_html_content):
        """Verify ideas stage buttons exist for coexistence test."""
        assert "Approve Idea" in client_html_content, \
            "Approve Idea button should exist for coexistence test"
        assert "Move for Debate" in client_html_content, \
            "Move for Debate button should exist for coexistence test"
        assert "stage === 'ideas'" in client_html_content, \
            "Ideas stage conditional block should exist"
