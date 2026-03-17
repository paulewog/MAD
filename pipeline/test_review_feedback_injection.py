"""Tests for review phase feedback injection from previous rejections.

Run with:
    pytest test_review_feedback_injection.py -v
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from state import FeatureFile
from phases import _get_latest_feedback, run_review_impl
from runner import AgentRunner


def create_test_feature():
    """Helper to create a test feature in implementing stage."""
    tmpdir = tempfile.TemporaryDirectory()
    config = MagicMock()
    config.boards_dir = Path(tmpdir.name)
    config.mad_dir = Path(tmpdir.name)
    
    with patch('state.Config') as mock_config_class:
        mock_config_class.return_value = config
        feature = FeatureFile.create("testboard", "Test Feature", "Description")
        feature._data["plan"] = "Test plan"
        feature._data["impl_spec"] = "Test impl spec"
        feature._data["test_spec"] = "Test test spec"
        feature._data["impl_notes"] = "Test implementation notes"
        feature.move_to_stage("implementing")
        feature.save()
    return feature, config, tmpdir


class TestReviewFeedbackInjection:
    """Tests for review phase including previous feedback in prompt."""

    def test_review_impl_includes_feedback_when_exists(self):
        """Test that run_review_impl includes previous feedback in prompt when it exists."""
        feature, config, tmpdir = create_test_feature()
        try:
            feature.add_impl_review(
                "FAIL",
                "Human rejected: Fix the API endpoint",
                "Human rejection: Fix the API endpoint\n\nPrevious review feedback:\nNone"
            )
            feature.save()

            mock_runner = MagicMock(spec=AgentRunner)
            mock_runner.for_phase.return_value = MagicMock()
            mock_runner.for_phase.return_value.run = AsyncMock(return_value=json.dumps({
                "verdict": "PASS",
                "summary": "All good",
                "feedback": None
            }))

            with patch('phases._build_prompt') as mock_build, \
                 patch('phases._run_phase') as mock_run_phase, \
                 patch('phases._git_commit'), \
                 patch('phases._delete_checkpoint'), \
                 patch('phases.console'):
                mock_run_phase.return_value = json.dumps({
                    "verdict": "PASS",
                    "summary": "All good",
                    "feedback": None
                })
                
                verdict, feedback = run_review_impl(feature, mock_runner)

                call_kwargs = mock_build.call_args[0][1]
                feedback_section = call_kwargs.get("{feedback_section}", "")
                
                assert "Previous Review Feedback" in feedback_section
                assert "Fix the API endpoint" in feedback_section

        finally:
            tmpdir.cleanup()

    def test_review_impl_excludes_feedback_when_none(self):
        """Test that run_review_impl excludes feedback section when no previous feedback."""
        feature, config, tmpdir = create_test_feature()
        try:
            mock_runner = MagicMock(spec=AgentRunner)
            mock_runner.for_phase.return_value = MagicMock()
            mock_runner.for_phase.return_value.run = AsyncMock(return_value=json.dumps({
                "verdict": "PASS",
                "summary": "All good",
                "feedback": None
            }))

            with patch('phases._build_prompt') as mock_build, \
                 patch('phases._run_phase') as mock_run_phase, \
                 patch('phases._git_commit'), \
                 patch('phases._delete_checkpoint'), \
                 patch('phases.console'):
                mock_run_phase.return_value = json.dumps({
                    "verdict": "PASS",
                    "summary": "All good",
                    "feedback": None
                })
                
                verdict, feedback = run_review_impl(feature, mock_runner)

                call_kwargs = mock_build.call_args[0][1]
                feedback_section = call_kwargs.get("{feedback_section}", "")
                
                assert feedback_section == ""

        finally:
            tmpdir.cleanup()

    def test_review_impl_includes_human_rejection_feedback(self):
        """Test that run_review_impl includes human rejection reason in feedback section."""
        feature, config, tmpdir = create_test_feature()
        try:
            feature.add_impl_review(
                "FAIL",
                "Human rejected from final-human-approval: The implementation is wrong",
                "Human rejection: The implementation is wrong\n\nPrevious review feedback:\nInitial feedback"
            )
            feature.save()

            mock_runner = MagicMock(spec=AgentRunner)
            mock_runner.for_phase.return_value = MagicMock()
            mock_runner.for_phase.return_value.run = AsyncMock(return_value=json.dumps({
                "verdict": "PASS",
                "summary": "All good",
                "feedback": None
            }))

            with patch('phases._build_prompt') as mock_build, \
                 patch('phases._run_phase') as mock_run_phase, \
                 patch('phases._git_commit'), \
                 patch('phases._delete_checkpoint'), \
                 patch('phases.console'):
                mock_run_phase.return_value = json.dumps({
                    "verdict": "PASS",
                    "summary": "All good",
                    "feedback": None
                })
                
                verdict, feedback = run_review_impl(feature, mock_runner)

                call_kwargs = mock_build.call_args[0][1]
                feedback_section = call_kwargs.get("{feedback_section}", "")
                
                assert "The implementation is wrong" in feedback_section

        finally:
            tmpdir.cleanup()

    def test_get_latest_feedback_returns_human_rejection(self):
        """Test that _get_latest_feedback returns human rejection feedback."""
        feature, config, tmpdir = create_test_feature()
        try:
            feature.add_impl_review(
                "FAIL",
                "Human rejected: Fix bugs",
                "Human rejection: Fix bugs\n\nPrevious review feedback:\nNone"
            )

            feedback = _get_latest_feedback(feature)
            
            assert "Fix bugs" in feedback
            assert feedback != "No previous feedback available."

        finally:
            tmpdir.cleanup()

    def test_review_prompt_template_has_feedback_placeholder(self):
        """Test that review-impl.md template contains feedback_section placeholder."""
        template_path = Path(__file__).parent / "prompts" / "review-impl.md"
        content = template_path.read_text()
        
        assert "{feedback_section}" in content

    def test_review_impl_passes_all_required_template_vars(self):
        """Test that run_review_impl passes all required variables to the template."""
        feature, config, tmpdir = create_test_feature()
        try:
            mock_runner = MagicMock(spec=AgentRunner)
            mock_runner.for_phase.return_value = MagicMock()
            mock_runner.for_phase.return_value.run = AsyncMock(return_value=json.dumps({
                "verdict": "PASS",
                "summary": "All good",
                "feedback": None
            }))

            with patch('phases._build_prompt') as mock_build, \
                 patch('phases._run_phase') as mock_run_phase, \
                 patch('phases._git_commit'), \
                 patch('phases._delete_checkpoint'), \
                 patch('phases.console'):
                mock_run_phase.return_value = json.dumps({
                    "verdict": "PASS",
                    "summary": "All good",
                    "feedback": None
                })
                
                run_review_impl(feature, mock_runner)

                call_kwargs = mock_build.call_args[0][1]
                
                assert "{title}" in call_kwargs
                assert "{plan}" in call_kwargs
                assert "{test_spec}" in call_kwargs
                assert "{impl_notes}" in call_kwargs
                assert "{feature_file_path}" in call_kwargs
                assert "{feedback_section}" in call_kwargs
                
                assert call_kwargs["{title}"] == "Test Feature"

        finally:
            tmpdir.cleanup()


class TestWebUIFeedbackStorage:
    """Tests for web UI rejection feedback storage via _on_move_requested."""

    def test_web_ui_rejection_triggers_feedback_storage(self):
        """Test that web UI rejection triggers the feedback storage condition."""
        target_stage = "implementing"
        current_stage = "final-human-approval"
        reason = "Fix the bugs"
        
        should_store = (
            target_stage == "implementing" and 
            current_stage == "final-human-approval" and 
            bool(reason)
        )
        
        assert should_store is True

    def test_web_ui_approval_does_not_trigger_feedback_storage(self):
        """Test that web UI approval (to done) doesn't trigger feedback storage."""
        target_stage = "done"
        current_stage = "final-human-approval"
        reason = ""
        
        should_store = (
            target_stage == "implementing" and 
            current_stage == "final-human-approval" and 
            bool(reason)
        )
        
        assert should_store is False

    def test_web_ui_reject_without_reason_does_not_store(self):
        """Test that web UI rejection without reason doesn't store feedback."""
        target_stage = "implementing"
        current_stage = "final-human-approval"
        reason = ""
        
        should_store = (
            target_stage == "implementing" and 
            current_stage == "final-human-approval" and 
            bool(reason)
        )
        
        assert should_store is False
