"""Tests for specific-review-slots feature (plan_reviews / impl_reviews).

Run with:
    pytest test_specific_review_slots.py -v
"""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from state import FeatureFile
from phases import _get_latest_feedback


class TestFeatureCreationWithReviewArrays:
    """Tests for Section 1.1: Feature Creation receives empty plan_reviews and impl_reviews arrays."""

    def test_featurefile_create_has_empty_plan_reviews(self):
        """Test that FeatureFile.create() initializes plan_reviews as empty array."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                
                assert "plan_reviews" in feature._data
                assert feature._data["plan_reviews"] == []
                assert isinstance(feature._data["plan_reviews"], list)

    def test_featurefile_create_has_empty_impl_reviews(self):
        """Test that FeatureFile.create() initializes impl_reviews as empty array."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                
                assert "impl_reviews" in feature._data
                assert feature._data["impl_reviews"] == []
                assert isinstance(feature._data["impl_reviews"], list)


class TestPropertyAccessors:
    """Tests for Section 1.2: Property Accessors return lists."""

    def test_plan_reviews_property_returns_list(self):
        """Test that plan_reviews property returns a list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                result = feature.plan_reviews
                assert isinstance(result, list)
                assert result == []

    def test_impl_reviews_property_returns_list(self):
        """Test that impl_reviews property returns a list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                result = feature.impl_reviews
                assert isinstance(result, list)
                assert result == []

    def test_plan_reviews_defaults_to_empty_list(self):
        """Test that plan_reviews returns [] when key not present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            feature_path = Path(tmpdir) / "testboard" / "ideas" / "test.json"
            feature_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(feature_path, "w") as f:
                json.dump({"id": "abc", "board": "test", "title": "Test"}, f)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile(feature_path)
                
                result = feature.plan_reviews
                assert result == []
                assert isinstance(result, list)

    def test_impl_reviews_defaults_to_empty_list(self):
        """Test that impl_reviews returns [] when key not present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            feature_path = Path(tmpdir) / "testboard" / "ideas" / "test.json"
            feature_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(feature_path, "w") as f:
                json.dump({"id": "abc", "board": "test", "title": "Test"}, f)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile(feature_path)
                
                result = feature.impl_reviews
                assert result == []
                assert isinstance(result, list)


class TestAddPlanReview:
    """Tests for Section 1.3: add_plan_review() Method."""

    def test_add_plan_review_creates_entry(self):
        """Test that add_plan_review() appends entry with correct structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("PASS", "Looks good!")
                
                assert len(feature.plan_reviews) == 1
                entry = feature.plan_reviews[0]
                assert "ts" in entry
                assert "verdict" in entry
                assert "feedback" in entry
                assert entry["verdict"] == "PASS"
                assert entry["feedback"] == "Looks good!"

    def test_add_plan_review_timestamp_is_iso_format(self):
        """Test that add_plan_review() uses ISO timestamp format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("PASS", "Good")
                
                entry = feature.plan_reviews[0]
                ts = entry["ts"]
                datetime.fromisoformat(ts)

    def test_add_plan_review_multiple_entries(self):
        """Test that add_plan_review() appends multiple entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("PASS", "First review")
                feature.add_plan_review("FAIL", "Second review")
                
                assert len(feature.plan_reviews) == 2
                assert feature.plan_reviews[0]["verdict"] == "PASS"
                assert feature.plan_reviews[1]["verdict"] == "FAIL"


class TestAddImplReview:
    """Tests for Section 1.4: add_impl_review() Method."""

    def test_add_impl_review_creates_entry(self):
        """Test that add_impl_review() appends entry with correct structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_impl_review("PASS", "Implementation looks good!")
                
                assert len(feature.impl_reviews) == 1
                entry = feature.impl_reviews[0]
                assert "ts" in entry
                assert "verdict" in entry
                assert "feedback" in entry
                assert entry["verdict"] == "PASS"
                assert entry["feedback"] == "Implementation looks good!"

    def test_add_impl_review_timestamp_is_iso_format(self):
        """Test that add_impl_review() uses ISO timestamp format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_impl_review("PASS", "Good")
                
                entry = feature.impl_reviews[0]
                ts = entry["ts"]
                datetime.fromisoformat(ts)

    def test_add_impl_review_multiple_entries(self):
        """Test that add_impl_review() appends multiple entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_impl_review("PASS", "First review")
                feature.add_impl_review("FAIL", "Second review")
                
                assert len(feature.impl_reviews) == 2
                assert feature.impl_reviews[0]["verdict"] == "PASS"
                assert feature.impl_reviews[1]["verdict"] == "FAIL"


class TestVerdictNormalization:
    """Tests for Section 1.5: Verdict Normalization."""

    @pytest.mark.parametrize("input_verdict,expected", [
        ("PASS", "PASS"),
        ("pass", "PASS"),
        ("Pass", "PASS"),
        ("pAsS", "PASS"),
    ])
    def test_pass_verdict_normalized_correctly(self, input_verdict, expected):
        """Test that 'PASS' (any case) stays PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review(input_verdict, "feedback")
                
                assert feature.plan_reviews[0]["verdict"] == expected

    @pytest.mark.parametrize("input_verdict", [
        "FAIL", "fail", "Fail",
        "MAYBE", "maybe", "Maybe",
        "INVALID", "invalid", "",
        "UNKNOWN", "random", "No",
    ])
    def test_non_pass_verdict_normalized_to_fail(self, input_verdict):
        """Test that non-PASS verdicts become FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review(input_verdict, "feedback")
                
                assert feature.plan_reviews[0]["verdict"] == "FAIL"


class TestEmptyNoneFeedbackHandling:
    """Tests for Section 1.6: Empty/None Feedback Handling."""

    def test_empty_feedback_creates_entry_with_empty_string(self):
        """Test that empty feedback creates entry with empty string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("FAIL", "")
                
                assert len(feature.plan_reviews) == 1
                assert feature.plan_reviews[0]["feedback"] == ""

    def test_none_feedback_creates_entry_with_empty_string(self):
        """Test that None feedback creates entry with empty string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("FAIL", None)
                
                assert len(feature.plan_reviews) == 1
                assert feature.plan_reviews[0]["feedback"] == ""


class TestGetLatestPlanReview:
    """Tests for Section 1.7: get_latest_plan_review() Method."""

    def test_get_latest_plan_review_returns_last_entry(self):
        """Test that get_latest_plan_review() returns last entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("PASS", "First")
                feature.add_plan_review("FAIL", "Second")
                
                latest = feature.get_latest_plan_review()
                assert latest["verdict"] == "FAIL"
                assert latest["feedback"] == "Second"

    def test_get_latest_plan_review_returns_none_when_empty(self):
        """Test that get_latest_plan_review() returns None when no reviews."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                latest = feature.get_latest_plan_review()
                assert latest is None


class TestGetLatestImplReview:
    """Tests for Section 1.8: get_latest_impl_review() Method."""

    def test_get_latest_impl_review_returns_last_entry(self):
        """Test that get_latest_impl_review() returns last entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_impl_review("PASS", "First")
                feature.add_impl_review("FAIL", "Second")
                
                latest = feature.get_latest_impl_review()
                assert latest["verdict"] == "FAIL"
                assert latest["feedback"] == "Second"

    def test_get_latest_impl_review_returns_none_when_empty(self):
        """Test that get_latest_impl_review() returns None when no reviews."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                latest = feature.get_latest_impl_review()
                assert latest is None


class TestGetLatestFeedbackPhases:
    """Tests for Section 1.9: _get_latest_feedback() rewrite in phases.py."""

    def test_get_latest_feedback_prefers_impl_over_plan(self):
        """Test that _get_latest_feedback() prefers impl_reviews over plan_reviews."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("FAIL", "Plan feedback")
                feature.add_impl_review("FAIL", "Impl feedback")
                
                feedback = _get_latest_feedback(feature)
                assert feedback == "Impl feedback"

    def test_get_latest_feedback_returns_plan_when_no_impl_fail(self):
        """Test that _get_latest_feedback() returns plan feedback when impl has no FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("FAIL", "Plan feedback")
                feature.add_impl_review("PASS", "Impl passed")
                
                feedback = _get_latest_feedback(feature)
                assert feedback == "Plan feedback"

    def test_get_latest_feedback_default_message_when_no_failures(self):
        """Test that _get_latest_feedback() returns default when no FAIL verdicts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("PASS", "Plan passed")
                feature.add_impl_review("PASS", "Impl passed")
                
                feedback = _get_latest_feedback(feature)
                assert feedback == "No previous feedback available."

    def test_get_latest_feedback_empty_feature(self):
        """Test that _get_latest_feedback() handles feature with no reviews."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feedback = _get_latest_feedback(feature)
                assert feedback == "No previous feedback available."


class TestCleanupFunctionRemoved:
    """Tests for Section 1.12: _cleanup_impl_notes_for_retry() Removal."""

    def test_cleanup_function_does_not_exist(self):
        """Test that _cleanup_impl_notes_for_retry function was removed."""
        import phases
        assert not hasattr(phases, '_cleanup_impl_notes_for_retry')


class TestServerClientIncludesReviews:
    """Tests for Section 1.14: server_client.py Update."""

    def test_push_state_includes_plan_reviews(self):
        """Test that push_state includes plan_reviews in feature data."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from server_client import ServerClient
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            config.mad_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                feature.add_plan_review("FAIL", "Plan feedback")
                
            feature_summaries = []
            
            with patch('server_client._get_checkpoint_info', return_value=None):
                sc = ServerClient("ws://localhost:8080", "key", "client1")
                sc._connected = True
                sc._ws = AsyncMock()
                
                async def mock_send(msg):
                    nonlocal feature_summaries
                    data = json.loads(msg)
                    feature_summaries = data.get("features", [])
                
                sc._ws.send = mock_send
                
                asyncio.get_event_loop().run_until_complete(
                    sc.push_state([feature])
                )
                
                assert len(feature_summaries) == 1
                assert "plan_reviews" in feature_summaries[0]
                assert len(feature_summaries[0]["plan_reviews"]) == 1

    def test_push_state_includes_impl_reviews(self):
        """Test that push_state includes impl_reviews in feature data."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from server_client import ServerClient
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            config.mad_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                feature.add_impl_review("FAIL", "Impl feedback")
                
            feature_summaries = []
            
            with patch('server_client._get_checkpoint_info', return_value=None):
                sc = ServerClient("ws://localhost:8080", "key", "client1")
                sc._connected = True
                sc._ws = AsyncMock()
                
                async def mock_send(msg):
                    nonlocal feature_summaries
                    data = json.loads(msg)
                    feature_summaries = data.get("features", [])
                
                sc._ws.send = mock_send
                
                asyncio.get_event_loop().run_until_complete(
                    sc.push_state([feature])
                )
                
                assert len(feature_summaries) == 1
                assert "impl_reviews" in feature_summaries[0]
                assert len(feature_summaries[0]["impl_reviews"]) == 1


class TestBackwardCompatibility:
    """Tests for Section 1.15: Backward Compatibility."""

    def test_existing_feature_without_review_arrays_works(self):
        """Test that features without plan_reviews/impl_reviews work seamlessly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            feature_path = Path(tmpdir) / "testboard" / "ideas" / "old-feature.json"
            feature_path.parent.mkdir(parents=True, exist_ok=True)
            
            old_data = {
                "id": "old123",
                "board": "testboard",
                "title": "Old Feature",
                "description": "Created before reviews feature",
                "plan": "Some plan",
                "impl_spec": "",
                "test_spec": "",
                "impl_notes": "",
                "history": [],
                "pipeline_log": [],
            }
            
            with open(feature_path, "w") as f:
                json.dump(old_data, f)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile(feature_path)
                
                assert feature.plan_reviews == []
                assert feature.impl_reviews == []
                assert feature.get_latest_plan_review() is None
                assert feature.get_latest_impl_review() is None


class TestEdgeCases:
    """Tests for Section 2: Edge Cases."""

    def test_empty_verdict_normalizes_to_fail(self):
        """Test that empty verdict string normalizes to FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("", "feedback")
                
                assert feature.plan_reviews[0]["verdict"] == "FAIL"

    def test_none_verdict_normalizes_to_fail(self):
        """Test that None verdict normalizes to FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review(None, "feedback")
                
                assert feature.plan_reviews[0]["verdict"] == "FAIL"

    def test_get_latest_on_feature_with_malformed_review_data(self):
        """Test graceful handling of corrupted/malformed review data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            feature_path = Path(tmpdir) / "testboard" / "ideas" / "bad-feature.json"
            feature_path.parent.mkdir(parents=True, exist_ok=True)
            
            bad_data = {
                "id": "bad123",
                "board": "testboard",
                "title": "Bad Feature",
                "plan_reviews": [{"ts": "2024-01-01", "verdict": "PASS"}],
                "impl_reviews": "not a list",
            }
            
            with open(feature_path, "w") as f:
                json.dump(bad_data, f)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile(feature_path)
                
                latest = feature.get_latest_plan_review()
                assert latest is not None

    def test_very_long_feedback_text(self):
        """Test that very long feedback text (10KB+) is stored without truncation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            long_feedback = "x" * 15000
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("FAIL", long_feedback)
                
                stored = feature.plan_reviews[0]["feedback"]
                assert len(stored) == 15000
                assert stored == long_feedback

    def test_unicode_characters_in_verdict_and_feedback(self):
        """Test that unicode characters in verdict/feedback are stored correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            unicode_feedback = "Feedback with émojis 🎉 and 日本語"
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "Desc")
                
                feature.add_plan_review("PASS", unicode_feedback)
                
                stored = feature.plan_reviews[0]["feedback"]
                assert stored == unicode_feedback


class TestIntegrationScenarios:
    """Tests for Section 5: Integration Test Scenarios."""

    def test_plan_review_feedback_loop(self):
        """Test Plan Review Feedback Loop: create -> review (FAIL) -> fix -> review again."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                
                feature.add_plan_review("FAIL", "Plan needs improvement: missing details")
                assert feature.get_latest_plan_review()["feedback"] == "Plan needs improvement: missing details"
                
                latest = _get_latest_feedback(feature)
                assert latest == "Plan needs improvement: missing details"
                
                feature.add_plan_review("PASS", "Plan looks good now")
                assert len(feature.plan_reviews) == 2

    def test_impl_review_feedback_loop(self):
        """Test Impl Review Feedback Loop: implement -> review (FAIL) -> fix -> review again."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                
                feature.add_impl_review("FAIL", "Implementation has bugs in step 3")
                assert feature.get_latest_impl_review()["feedback"] == "Implementation has bugs in step 3"
                
                latest = _get_latest_feedback(feature)
                assert latest == "Implementation has bugs in step 3"
                
                feature.add_impl_review("PASS", "Implementation fixed")
                assert len(feature.impl_reviews) == 2

    def test_mixed_review_types(self):
        """Test Mixed Review Types: plan FAIL -> fix -> plan PASS -> impl FAIL -> fix -> impl PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                
                feature.add_plan_review("FAIL", "Plan issue")
                feature.add_plan_review("PASS", "Plan fixed")
                feature.add_impl_review("FAIL", "Impl issue")
                
                assert len(feature.plan_reviews) == 2
                assert len(feature.impl_reviews) == 1
                
                feedback = _get_latest_feedback(feature)
                assert feedback == "Impl issue"


class TestRunFixFeedbackFallback:
    """Tests for Section 1.13: run_fix_feedback() Fallback."""

    def test_run_fix_feedback_uses_latest_impl_review_as_fallback(self):
        """Test that run_fix_feedback() fetches from get_latest_impl_review() when no feedback param."""
        import asyncio
        from unittest.mock import MagicMock as MockRunner, patch
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test Feature", "Description")
                feature.add_impl_review("FAIL", "Bug in login function")
                feature.save()
            
            with patch('phases._build_prompt', return_value="test"), \
                 patch('phases._run_phase', return_value="fixed"), \
                 patch('phases.console'):
                
                from phases import run_fix_feedback
                
                mock_runner = MockRunner()
                mock_runner.for_phase.return_value = MockRunner()
                mock_runner.for_phase.return_value.headless = MockRunner(return_value="DONE")
                
                run_fix_feedback(feature, mock_runner, feedback="")
                
                latest = feature.get_latest_impl_review()
                assert latest["feedback"] == "Bug in login function"
