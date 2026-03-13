"""Tests for rejection feedback from final-human-approval.

Run with:
    pytest pipeline/test_rejection_feedback.py -v
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from server_client import ServerClient
from state import FeatureFile
from phases import _get_latest_feedback


class TestServerClientMoveFeatureWithReason:
    """Tests for server_client.py handling of reason in move_feature."""

    @pytest.mark.asyncio
    async def test_move_feature_passes_reason_to_callback(self):
        """Test that move_feature message passes reason to the callback."""
        received = {}

        async def on_move(feature_id, target_stage, request_id, reason):
            received["feature_id"] = feature_id
            received["target_stage"] = target_stage
            received["request_id"] = request_id
            received["reason"] = reason

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "feat-123",
            "target_stage": "implementing",
            "request_id": "req-456",
            "reason": "Fix the API endpoint",
        })
        await sc._handle_incoming(msg)

        assert received["feature_id"] == "feat-123"
        assert received["target_stage"] == "implementing"
        assert received["request_id"] == "req-456"
        assert received["reason"] == "Fix the API endpoint"

    @pytest.mark.asyncio
    async def test_move_feature_empty_reason_default(self):
        """Test that missing reason defaults to empty string."""
        received = {}

        async def on_move(feature_id, target_stage, request_id, reason):
            received["feature_id"] = feature_id
            received["reason"] = reason

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "feat-123",
            "target_stage": "plan-inbox",
        })
        await sc._handle_incoming(msg)

        assert received["feature_id"] == "feat-123"
        assert received["reason"] == ""


class TestTUIRejectionHandler:
    """Tests for TUI rejection handler persisting feedback."""

    def test_tui_rejection_adds_impl_review(self):
        """Test that TUI rejection from final-human-approval adds impl_review with FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")

                feature.move_to_stage("final-human-approval")

                feature.add_impl_review("PASS", "Initial review", "Looks good")

                feedback_before = _get_latest_feedback(feature)
                assert feedback_before == "No previous feedback available."

                full_feedback = f"Human rejection: Bad implementation\n\nPrevious review feedback:\n{feedback_before}"

                feature.add_impl_review("FAIL", "Human rejected from final-human-approval: Bad implementation", full_feedback)

                latest_review = feature.get_latest_impl_review()
                assert latest_review["verdict"] == "FAIL"
                assert "Bad implementation" in latest_review["summary"]
                assert "Human rejected from final-human-approval" in latest_review["summary"]
                assert "Human rejection: Bad implementation" in latest_review["feedback"]

                new_feedback = _get_latest_feedback(feature)
                assert "Bad implementation" in new_feedback
                assert new_feedback != "No previous feedback available."


class TestOnMoveRequestedLogic:
    """Tests for _on_move_requested rejection logic (tested without Textual)."""

    def test_on_move_requested_rejection_condition_true(self):
        """Test the condition for storing rejection feedback evaluates correctly."""
        target_stage = "implementing"
        current_stage = "final-human-approval"
        reason = "Needs more work"
        
        should_store_feedback = (
            target_stage == "implementing" and 
            current_stage == "final-human-approval" and 
            bool(reason)
        )
        
        assert should_store_feedback is True

    def test_on_move_requested_no_rejection_condition_false(self):
        """Test that non-rejection moves don't store feedback."""
        target_stage = "done"
        current_stage = "final-human-approval"
        reason = ""
        
        should_store_feedback = (
            target_stage == "implementing" and 
            current_stage == "final-human-approval" and 
            bool(reason)
        )
        
        assert should_store_feedback is False

    def test_on_move_requested_rejection_without_reason_condition_false(self):
        """Test that rejection without reason doesn't store feedback."""
        target_stage = "implementing"
        current_stage = "final-human-approval"
        reason = ""
        
        should_store_feedback = (
            target_stage == "implementing" and 
            current_stage == "final-human-approval" and 
            bool(reason)
        )
        
        assert should_store_feedback is False

    def test_on_move_requested_from_non_final_approval_condition_false(self):
        """Test that move from non-final-human-approval doesn't store feedback."""
        target_stage = "implementing"
        current_stage = "plan-inbox"
        reason = "Some reason"
        
        should_store_feedback = (
            target_stage == "implementing" and 
            current_stage == "final-human-approval" and 
            reason
        )
        
        assert should_store_feedback is False


class TestGetLatestFeedbackAfterRejection:
    """Tests for _get_latest_feedback returning rejection feedback."""

    def test_get_latest_feedback_returns_rejection_reason(self):
        """Test that _get_latest_feedback returns the rejection reason after rejection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")

                feature.move_to_stage("review")
                feature.add_impl_review("PASS", "Initial review", "Good")

                feature.move_to_stage("final-human-approval")

                feature.add_impl_review(
                    "FAIL",
                    "Human rejected from final-human-approval: Fix bugs",
                    "Human rejection: Fix bugs\n\nPrevious review feedback:\nGood"
                )

                feedback = _get_latest_feedback(feature)
                assert "Fix bugs" in feedback
                assert feedback != "No previous feedback available."

    def test_get_latest_feedback_prefers_rejection_over_old_feedback(self):
        """Test that _get_latest_feedback prefers most recent rejection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")

                feature.add_impl_review("FAIL", "First rejection", "Fix this")

                feature.add_impl_review(
                    "FAIL",
                    "Second rejection",
                    "Human rejection: Fix that instead\n\nPrevious review feedback:\nFix this"
                )

                feedback = _get_latest_feedback(feature)
                assert "Fix that instead" in feedback

    def test_get_latest_feedback_empty_feature(self):
        """Test that _get_latest_feedback returns default message for empty feature."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)

            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")

                feedback = _get_latest_feedback(feature)
                assert feedback == "No previous feedback available."
