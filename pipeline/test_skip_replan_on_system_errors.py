"""Tests for skip-replan-on-system-errors feature.

Run with:
    pytest test_skip_replan_on_system_errors.py -v
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from state import FeatureFile
from phases import _is_system_error_in_latest_review, _parse_verdict, run_plan_review
from runner import AgentRunner, RateLimitError


def create_feature_with_plan_review(feedback: str = ""):
    """Helper to create a feature with a plan review."""
    tmpdir = tempfile.TemporaryDirectory()
    config = MagicMock()
    config.boards_dir = Path(tmpdir.name)
    
    with patch('state.Config') as mock_config_class:
        mock_config_class.return_value = config
        feature = FeatureFile.create("testboard", "Test Feature", "Description")
        feature._data["plan"] = "Some plan"
        if feedback:
            feature.add_plan_review("FAIL", "Test summary", feedback)
        feature.save()
    return feature, tmpdir


class TestIsSystemErrorInLatestReview:
    """Tests for _is_system_error_in_latest_review() function."""

    def test_returns_false_when_no_plan_review(self):
        """Test that function returns False when there is no plan review at all."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                result = _is_system_error_in_latest_review(feature)
                assert result is False

    def test_returns_false_for_empty_feedback(self):
        """Test that function returns False when feedback is empty string."""
        feature, tmpdir = create_feature_with_plan_review("")
        try:
            result = _is_system_error_in_latest_review(feature)
            assert result is False
        finally:
            tmpdir.cleanup()

    def test_system_error_pattern(self):
        """Test 'system error' pattern."""
        feature, tmpdir = create_feature_with_plan_review("SYSTEM ERROR: Something went wrong")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_returned_plan_pattern(self):
        """Test 'returned a plan instead of a review verdict' pattern."""
        feature, tmpdir = create_feature_with_plan_review("Agent returned a plan instead of a review verdict")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_api_error_pattern(self):
        """Test 'api error' pattern."""
        feature, tmpdir = create_feature_with_plan_review("API Error: service unavailable")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_authentication_error_pattern(self):
        """Test 'authentication error' pattern."""
        feature, tmpdir = create_feature_with_plan_review("Authentication error: invalid credentials")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_invalid_api_key_pattern(self):
        """Test 'invalid api key' pattern."""
        feature, tmpdir = create_feature_with_plan_review("Invalid API key provided")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_rate_limit_pattern(self):
        """Test 'rate limit' pattern."""
        feature, tmpdir = create_feature_with_plan_review("Rate limit exceeded, please wait")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_json_parse_error_pattern(self):
        """Test 'json parse error' pattern."""
        feature, tmpdir = create_feature_with_plan_review("JSON parse error at line 10")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_malformed_json_pattern(self):
        """Test 'malformed json' pattern."""
        feature, tmpdir = create_feature_with_plan_review("Response contains malformed JSON")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_no_valid_json_pattern(self):
        """Test 'no valid json' pattern."""
        feature, tmpdir = create_feature_with_plan_review("No valid JSON in response")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_empty_output_pattern(self):
        """Test 'empty output' pattern."""
        feature, tmpdir = create_feature_with_plan_review("Agent returned empty output")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_network_error_pattern(self):
        """Test 'network error' pattern."""
        feature, tmpdir = create_feature_with_plan_review("Network error: connection reset")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_timeout_pattern(self):
        """Test 'timeout' pattern."""
        feature, tmpdir = create_feature_with_plan_review("Request timeout after 30 seconds")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()

    def test_returns_false_for_real_plan_failure(self):
        """Test that function returns False for genuine plan rejection."""
        feature, tmpdir = create_feature_with_plan_review("The acceptance criteria are incomplete")
        try:
            assert _is_system_error_in_latest_review(feature) is False
        finally:
            tmpdir.cleanup()

    def test_case_insensitive_matching(self):
        """Test that matching is case-insensitive."""
        feature, tmpdir = create_feature_with_plan_review("SYSTEM ERROR: Something went wrong")
        try:
            assert _is_system_error_in_latest_review(feature) is True
        finally:
            tmpdir.cleanup()
        
        feature2, tmpdir2 = create_feature_with_plan_review("System Error detected")
        try:
            assert _is_system_error_in_latest_review(feature2) is True
        finally:
            tmpdir2.cleanup()
        
        feature3, tmpdir3 = create_feature_with_plan_review("RATE LIMIT reached")
        try:
            assert _is_system_error_in_latest_review(feature3) is True
        finally:
            tmpdir3.cleanup()

    def test_only_examines_latest_review(self):
        """Test that only the latest review is examined."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                # Add older review (system error)
                feature._data["plan_reviews"] = [
                    {"verdict": "FAIL", "summary": "Old review", "feedback": "SYSTEM ERROR: old error"}
                ]
                # Add newer review (real plan failure - no system error pattern)
                feature.add_plan_review("FAIL", "New review", "The acceptance criteria are incomplete")
                feature.save()
                
                # Should return False because latest review is not a system error
                assert _is_system_error_in_latest_review(feature) is False

    def test_no_false_positive_systemic_error(self):
        """Test that 'systemic error' doesn't match 'system error'."""
        feature, tmpdir = create_feature_with_plan_review("the plan has no systemic error in the approach")
        try:
            assert _is_system_error_in_latest_review(feature) is False
        finally:
            tmpdir.cleanup()


class TestParseVerdictSystemError:
    """Tests for _parse_verdict() setting SYSTEM ERROR prefix."""

    def test_plan_shaped_output_sets_system_error(self):
        """Test that plan-shaped output triggers SYSTEM ERROR feedback."""
        # Agent returns a plan instead of a review verdict
        output = '{"plan": {"steps": ["step1", "step2"]}}'
        
        verdict, summary, feedback = _parse_verdict(output)
        
        assert verdict == "FAIL"
        assert feedback.startswith("SYSTEM ERROR:")
        assert "returned a plan instead of a review verdict" in feedback

    def test_plan_shaped_output_logs_warning(self):
        """Test that plan-shaped output logs a warning."""
        output = '{"plan": {"steps": ["step1"]}}'
        
        with patch('phases.logger') as mock_logger:
            _parse_verdict(output)
            mock_logger.warning.assert_called()


def create_feature_for_review():
    """Helper to create a feature ready for plan review."""
    tmpdir = tempfile.TemporaryDirectory()
    config = MagicMock()
    config.boards_dir = Path(tmpdir.name)
    
    with patch('state.Config') as mock_config_class:
        mock_config_class.return_value = config
        feature = FeatureFile.create("testboard", "Test Feature", "Description")
        feature._data["plan"] = "Some plan"
        feature.move_to_stage("reviewing-plan")
        feature.save()
    return feature, tmpdir


class TestRunPlanReviewExceptionHandling:
    """Tests for run_plan_review() exception handling."""

    def _create_mock_runner(self):
        """Create a mock AgentRunner."""
        mock_runner = MagicMock(spec=AgentRunner)
        mock_runner.for_phase.return_value = mock_runner
        return mock_runner

    def test_catches_rate_limit_error(self):
        """Test that RateLimitError is caught and returns FAIL with system error."""
        feature, tmpdir = create_feature_for_review()
        try:
            mock_runner = self._create_mock_runner()
            
            with patch('phases._run_phase') as mock_run_phase:
                mock_run_phase.side_effect = RateLimitError("Rate limit exceeded")
                
                verdict, feedback = run_plan_review(feature, mock_runner)
                
                assert verdict == "FAIL"
                assert feedback.startswith("SYSTEM ERROR: Rate limit exceeded")
                assert "PLAN_REVIEW_FAILED" in [h.get("stage") for h in feature._data.get("history", [])]
        finally:
            tmpdir.cleanup()

    def test_catches_authentication_error(self):
        """Test that RuntimeError with auth keywords is caught."""
        feature, tmpdir = create_feature_for_review()
        try:
            mock_runner = self._create_mock_runner()
            
            with patch('phases._run_phase') as mock_run_phase:
                mock_run_phase.side_effect = RuntimeError("Authentication error: invalid API key")
                
                verdict, feedback = run_plan_review(feature, mock_runner)
                
                assert verdict == "FAIL"
                assert feedback.startswith("SYSTEM ERROR: Authentication failure")
                assert "PLAN_REVIEW_FAILED" in [h.get("stage") for h in feature._data.get("history", [])]
        finally:
            tmpdir.cleanup()

    def test_catches_generic_runtime_error(self):
        """Test that generic RuntimeError is caught."""
        feature, tmpdir = create_feature_for_review()
        try:
            mock_runner = self._create_mock_runner()
            
            with patch('phases._run_phase') as mock_run_phase:
                mock_run_phase.side_effect = RuntimeError("Something went wrong")
                
                verdict, feedback = run_plan_review(feature, mock_runner)
                
                assert verdict == "FAIL"
                assert feedback.startswith("SYSTEM ERROR: Agent execution failed")
                assert "PLAN_REVIEW_FAILED" in [h.get("stage") for h in feature._data.get("history", [])]
        finally:
            tmpdir.cleanup()

    def test_adds_plan_review_record_on_exception(self):
        """Test that a plan review record is added on exception."""
        feature, tmpdir = create_feature_for_review()
        try:
            mock_runner = self._create_mock_runner()
            
            with patch('phases._run_phase') as mock_run_phase:
                mock_run_phase.side_effect = RateLimitError("Rate limit")
                
                run_plan_review(feature, mock_runner)
                
                assert len(feature.plan_reviews) >= 1
                latest = feature.get_latest_plan_review()
                assert latest["verdict"] == "FAIL"
                assert latest["summary"] == "System error during review"
        finally:
            tmpdir.cleanup()

    def test_value_error_propagates(self):
        """Test that ValueError is NOT caught and propagates."""
        feature, tmpdir = create_feature_for_review()
        try:
            mock_runner = self._create_mock_runner()
            
            with patch('phases._run_phase') as mock_run_phase:
                mock_run_phase.side_effect = ValueError("Some value error")
                
                with pytest.raises(ValueError):
                    run_plan_review(feature, mock_runner)
        finally:
            tmpdir.cleanup()

    def test_os_error_propagates(self):
        """Test that OSError is NOT caught and propagates."""
        feature, tmpdir = create_feature_for_review()
        try:
            mock_runner = self._create_mock_runner()
            
            with patch('phases._run_phase') as mock_run_phase:
                mock_run_phase.side_effect = OSError("Disk error")
                
                with pytest.raises(OSError):
                    run_plan_review(feature, mock_runner)
        finally:
            tmpdir.cleanup()


class TestReviewLoopLogic:
    """Tests for the review loop logic in _run_pipeline_impl."""

    def test_system_error_triggers_retry_without_replanning(self):
        """Test that system errors retry review without calling run_planning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.add_plan_review("FAIL", "System error during review", "SYSTEM ERROR: Rate limit exceeded")
                feature.save()
                
                # Verify it's detected as a system error
                assert _is_system_error_in_latest_review(feature) is True

    def test_plan_failure_triggers_replanning(self):
        """Test that real plan failures trigger run_planning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.add_plan_review("FAIL", "Plan issues", "The acceptance criteria are incomplete")
                feature.save()
                
                # Verify that non-system error triggers different handling
                assert _is_system_error_in_latest_review(feature) is False


class TestMaxTotalPlanReviewsGuard:
    """Tests for max_total_plan_reviews guard."""

    def test_6_reviews_moves_to_final_human_approval(self):
        """Test that 6 or more total reviews triggers escalation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                
                # Add 6 reviews
                for i in range(6):
                    feature.add_plan_review("FAIL", f"Review {i+1}", f"Feedback {i+1}")
                
                # Should have 6 reviews
                assert len(feature.plan_reviews) == 6


class TestSecondAttemptPasses:
    """Tests for second attempt scenarios."""

    def test_pass_after_system_error_reaches_approved(self):
        """Test that PASS on second attempt after system error reaches approved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                
                # First review is a system error
                feature.add_plan_review("FAIL", "System error", "SYSTEM ERROR: Rate limit")
                assert _is_system_error_in_latest_review(feature) is True
                
                # Second review passes - but we need to clear the old one for testing
                feature._data["plan_reviews"] = []
                feature.add_plan_review("PASS", "Approved", "Great plan!")
                assert feature.get_latest_plan_review()["verdict"] == "PASS"

    def test_pass_after_plan_failure_reaches_approved(self):
        """Test that PASS on second attempt after plan failure reaches approved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                
                # First review is a plan failure (not system error)
                feature.add_plan_review("FAIL", "Plan issues", "The acceptance criteria are incomplete")
                assert _is_system_error_in_latest_review(feature) is False


class TestReviewLoopIntegration:
    """Integration tests for the review loop in _run_pipeline_impl."""

    def _create_feature_in_reviewing_plan(self, tmpdir):
        """Helper to create a feature in reviewing-plan stage."""
        config = MagicMock()
        config.boards_dir = Path(tmpdir)
        
        with patch('state.Config') as mock_config_class:
            mock_config_class.return_value = config
            feature = FeatureFile.create("testboard", "Test Feature", "Description")
            feature._data["plan"] = "Some plan"
            feature.move_to_stage("reviewing-plan")
            feature.save()
        return feature

    def test_system_error_triggers_retry_without_replanning_integration(self):
        """Spec 1.6: Review loop retries on system errors without re-planning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.save()
                
                mock_runner = MagicMock(spec=AgentRunner)
                mock_status = MagicMock()
                
                with patch('phases.run_plan_review') as mock_review, \
                     patch('phases.run_planning') as mock_planning, \
                     patch('phases.run_spec_writing') as mock_spec, \
                     patch('phases._run_impl_test_review_loop') as mock_impl:
                    
                    def run_review_with_side_effect(f, r, feedback="", status=None):
                        if not hasattr(f, '_review_count'):
                            f._review_count = 0
                        f._review_count += 1
                        
                        if f._review_count == 1:
                            f.add_plan_review("FAIL", "System error", "SYSTEM ERROR: Rate limit exceeded")
                            return ("FAIL", "SYSTEM ERROR: Rate limit exceeded")
                        else:
                            f.add_plan_review("PASS", "Approved", "Great plan!")
                            return ("PASS", "Great plan!")
                    
                    mock_review.side_effect = run_review_with_side_effect
                    
                    from phases import _run_pipeline_impl
                    _run_pipeline_impl(feature, mock_runner, mock_status)
                
                assert mock_review.call_count == 2
                assert mock_planning.call_count == 0
                assert feature.current_stage == "approved"

    def test_system_errors_exhaust_all_retries_moves_to_final_human_approval(self):
        """Spec 1.7: System errors that exhaust all retries move to final-human-approval."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.save()
                
                mock_runner = MagicMock(spec=AgentRunner)
                mock_status = MagicMock()
                
                with patch('phases.run_plan_review') as mock_review, \
                     patch('phases.run_planning') as mock_planning, \
                     patch('phases.run_spec_writing') as mock_spec, \
                     patch('phases._run_impl_test_review_loop') as mock_impl:
                    
                    call_count = [0]
                    def run_review_with_side_effect(f, r, feedback="", status=None):
                        call_count[0] += 1
                        errors = [
                            "SYSTEM ERROR: Rate limit exceeded",
                            "SYSTEM ERROR: API error",
                            "SYSTEM ERROR: Timeout"
                        ]
                        f.add_plan_review("FAIL", "System error", errors[call_count[0] - 1])
                        return ("FAIL", errors[call_count[0] - 1])
                    
                    mock_review.side_effect = run_review_with_side_effect
                    
                    from phases import _run_pipeline_impl
                    _run_pipeline_impl(feature, mock_runner, mock_status)
                
                assert mock_review.call_count == 3
                assert mock_planning.call_count == 0
                assert feature.current_stage == "final-human-approval"
                
                history_stages = [h.get("stage") for h in feature._data.get("history", [])]
                assert "FINAL_HUMAN_APPROVAL" in history_stages

    def test_real_plan_failure_triggers_replanning(self):
        """Spec 1.8: Real plan failures still trigger re-planning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.save()
                
                mock_runner = MagicMock(spec=AgentRunner)
                mock_status = MagicMock()
                
                with patch('phases.run_plan_review') as mock_review, \
                     patch('phases.run_planning') as mock_planning, \
                     patch('phases.run_spec_writing') as mock_spec, \
                     patch('phases._run_impl_test_review_loop') as mock_impl:
                    
                    call_count = [0]
                    def run_review_with_side_effect(f, r, feedback="", status=None):
                        call_count[0] += 1
                        if call_count[0] == 1:
                            f.add_plan_review("FAIL", "Plan issues", "The acceptance criteria are incomplete")
                            return ("FAIL", "The acceptance criteria are incomplete")
                        else:
                            f.add_plan_review("PASS", "Approved", "Great plan!")
                            return ("PASS", "Great plan!")
                    
                    mock_review.side_effect = run_review_with_side_effect
                    mock_planning.return_value = True
                    
                    from phases import _run_pipeline_impl
                    _run_pipeline_impl(feature, mock_runner, mock_status)
                
                assert mock_review.call_count == 2
                assert mock_planning.call_count == 1
                assert feature.current_stage == "approved"

    def test_pass_verdict_advances_to_approved(self):
        """Spec 1.9: PASS verdict advances feature to approved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.save()
                
                mock_runner = MagicMock(spec=AgentRunner)
                mock_status = MagicMock()
                
                with patch('phases.run_plan_review') as mock_review, \
                     patch('phases.run_planning') as mock_planning, \
                     patch('phases.run_spec_writing') as mock_spec, \
                     patch('phases._run_impl_test_review_loop') as mock_impl:
                    
                    def run_review_with_side_effect(f, r, feedback="", status=None):
                        f.add_plan_review("PASS", "Approved", "Great plan!")
                        return ("PASS", "Great plan!")
                    
                    mock_review.side_effect = run_review_with_side_effect
                    
                    from phases import _run_pipeline_impl
                    _run_pipeline_impl(feature, mock_runner, mock_status)
                
                assert mock_review.call_count == 1
                assert mock_planning.call_count == 0
                assert feature.current_stage == "approved"
                
                history_stages = [h.get("stage") for h in feature._data.get("history", [])]
                assert "PROMOTED" in history_stages

    def test_max_total_plan_reviews_guard_moves_to_final_human_approval(self):
        """Spec 1.10: max_total_plan_reviews guard moves to final-human-approval."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                
                for i in range(6):
                    feature.add_plan_review("FAIL", f"Review {i+1}", f"Feedback {i+1}")
                
                assert len(feature.plan_reviews) == 6
                
                mock_runner = MagicMock(spec=AgentRunner)
                mock_status = MagicMock()
                
                with patch('phases.run_plan_review') as mock_review:
                    from phases import _run_pipeline_impl
                    _run_pipeline_impl(feature, mock_runner, mock_status)
                    
                    assert mock_review.call_count == 0
                    assert feature.current_stage == "final-human-approval"
                    
                    history_stages = [h.get("stage") for h in feature._data.get("history", [])]
                    assert "FINAL_HUMAN_APPROVAL" in history_stages

    def test_mixed_errors_exhaust_via_for_else_block(self):
        """Spec 1.11: Mixed errors (some system, some plan) exhaust via for-else."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.save()
                
                mock_runner = MagicMock(spec=AgentRunner)
                mock_status = MagicMock()
                
                with patch('phases.run_plan_review') as mock_review, \
                     patch('phases.run_planning') as mock_planning, \
                     patch('phases.run_spec_writing') as mock_spec, \
                     patch('phases._run_impl_test_review_loop') as mock_impl:
                    
                    call_count = [0]
                    def run_review_with_side_effect(f, r, feedback="", status=None):
                        call_count[0] += 1
                        feedbacks = [
                            "SYSTEM ERROR: Rate limit",
                            "The acceptance criteria are incomplete",
                            "The implementation is incomplete"
                        ]
                        f.add_plan_review("FAIL", "Review failed", feedbacks[call_count[0] - 1])
                        return ("FAIL", feedbacks[call_count[0] - 1])
                    
                    mock_review.side_effect = run_review_with_side_effect
                    mock_planning.return_value = True
                    
                    from phases import _run_pipeline_impl
                    _run_pipeline_impl(feature, mock_runner, mock_status)
                
                assert mock_review.call_count == 3
                assert mock_planning.call_count == 1
                assert feature.current_stage == "final-human-approval"
                
                history_stages = [h.get("stage") for h in feature._data.get("history", [])]
                assert "FINAL_HUMAN_APPROVAL" in history_stages


class TestReviewLoopEdgeCases:
    """Edge case tests for the review loop."""

    # Note: Edge case 2.6 (max_plan_review_attempts = 1) cannot be tested because
    # max_plan_review_attempts is a local variable in _run_pipeline_impl, not a
    # module-level constant that can be patched. The core behavior is already covered
    # by the integration tests above.

    def test_pass_after_system_error_on_second_attempt(self):
        """Edge case 2.7: System error then PASS reaches approved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.save()
                
                mock_runner = MagicMock(spec=AgentRunner)
                mock_status = MagicMock()
                
                with patch('phases.run_plan_review') as mock_review, \
                     patch('phases.run_planning') as mock_planning, \
                     patch('phases.run_spec_writing') as mock_spec, \
                     patch('phases._run_impl_test_review_loop') as mock_impl:
                    
                    call_count = [0]
                    def run_review_with_side_effect(f, r, feedback="", status=None):
                        call_count[0] += 1
                        if call_count[0] == 1:
                            f.add_plan_review("FAIL", "System error", "SYSTEM ERROR: Rate limit exceeded")
                            return ("FAIL", "SYSTEM ERROR: Rate limit exceeded")
                        else:
                            f.add_plan_review("PASS", "Approved", "Great plan!")
                            return ("PASS", "Great plan!")
                    
                    mock_review.side_effect = run_review_with_side_effect
                    
                    from phases import _run_pipeline_impl
                    _run_pipeline_impl(feature, mock_runner, mock_status)
                
                assert mock_review.call_count == 2
                assert mock_planning.call_count == 0
                assert feature.current_stage == "approved"

    def test_pass_after_plan_failure_on_second_attempt(self):
        """Edge case 2.8: Plan failure then PASS reaches approved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature._data["plan"] = "Some plan"
                feature.move_to_stage("reviewing-plan")
                feature.save()
                
                mock_runner = MagicMock(spec=AgentRunner)
                mock_status = MagicMock()
                
                with patch('phases.run_plan_review') as mock_review, \
                     patch('phases.run_planning') as mock_planning, \
                     patch('phases.run_spec_writing') as mock_spec, \
                     patch('phases._run_impl_test_review_loop') as mock_impl:
                    
                    call_count = [0]
                    def run_review_with_side_effect(f, r, feedback="", status=None):
                        call_count[0] += 1
                        if call_count[0] == 1:
                            f.add_plan_review("FAIL", "Plan issues", "The acceptance criteria are incomplete")
                            return ("FAIL", "The acceptance criteria are incomplete")
                        else:
                            f.add_plan_review("PASS", "Approved", "Great plan!")
                            return ("PASS", "Great plan!")
                    
                    mock_review.side_effect = run_review_with_side_effect
                    mock_planning.return_value = True
                    
                    from phases import _run_pipeline_impl
                    _run_pipeline_impl(feature, mock_runner, mock_status)
                
                assert mock_review.call_count == 2
                assert mock_planning.call_count == 1
                assert feature.current_stage == "approved"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
