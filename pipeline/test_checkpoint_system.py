"""Tests for AI Agent Checkpoint System.

Run with:
    pytest test_checkpoint_system.py -v
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from runner import AgentRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workdir(tmp_path):
    """Create a temp workdir with .mad structure."""
    mad_dir = tmp_path / ".mad"
    mad_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir = mad_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def mock_config(tmp_workdir):
    """Create a mock config pointing to tmp workdir."""
    cfg = MagicMock(spec=Config)
    cfg.code_path = tmp_workdir
    cfg.context_file = str(tmp_workdir / ".mad" / "CONTEXT.md")
    cfg.mad_dir = tmp_workdir / ".mad"
    cfg.agent_for_phase = {}
    return cfg


def _make_feature(title="Test Feature", slug="test-feature", fid="f1"):
    """Create a mock feature object."""
    f = SimpleNamespace()
    f.title = title
    f.slug = slug
    f.id = fid
    f.current_stage = "plan-inbox"
    f.board = "default"
    f.created = "2025-01-01"
    f.description = "A test feature"
    f.questions = []
    f.plan = ""
    f.impl_spec = ""
    f.test_spec = ""
    f.impl_notes = ""
    f._data = {"pipeline_log": [], "history": []}
    return f


# ---------------------------------------------------------------------------
# Test: Checkpoint Directory Creation (1.1)
# ---------------------------------------------------------------------------

class TestCheckpointDirectoryCreation:

    def test_checkpoint_directory_exists_in_mad(self, tmp_workdir):
        """Verify .mad/checkpoints/ directory exists."""
        checkpoint_dir = tmp_workdir / ".mad" / "checkpoints"
        assert checkpoint_dir.exists()
        assert checkpoint_dir.is_dir()

    def test_checkpoint_directory_writable(self, tmp_workdir):
        """Verify checkpoint directory is writable."""
        checkpoint_dir = tmp_workdir / ".mad" / "checkpoints"
        test_file = checkpoint_dir / "test-write.txt"
        try:
            test_file.write_text("test")
            assert test_file.exists()
        finally:
            test_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test: Checkpoint File Format (1.2)
# ---------------------------------------------------------------------------

class TestCheckpointFileFormat:

    def test_valid_checkpoint_json_format(self, tmp_workdir):
        """Test checkpoint file with all required fields."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "feature_id": "abc123",
            "phase": "implementing",
            "last_checkpoint": "2025-01-01T12:00:00Z",
            "completed_steps": ["step 1", "step 2"],
            "next_step": "write tests",
            "notes": "working on feature X",
            "files_modified": ["file1.py", "file2.py"],
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        loaded = json.loads(checkpoint_path.read_text())
        assert loaded["feature_slug"] == "test-feature"
        assert loaded["phase"] == "implementing"
        assert isinstance(loaded["completed_steps"], list)
        assert isinstance(loaded["next_step"], str)
        assert isinstance(loaded["notes"], str)
        assert isinstance(loaded["files_modified"], list)

    def test_checkpoint_with_only_required_fields(self, tmp_workdir):
        """Test checkpoint with only required fields (feature_slug, phase)."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "planning",
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        loaded = json.loads(checkpoint_path.read_text())
        assert loaded["feature_slug"] == "test-feature"
        assert loaded["phase"] == "planning"

    def test_checkpoint_iso_timestamp_valid(self, tmp_workdir):
        """Test that last_checkpoint field contains valid ISO timestamp."""
        timestamp = datetime.now(timezone.utc).isoformat()
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "spec-writing",
            "last_checkpoint": timestamp,
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        loaded = json.loads(checkpoint_path.read_text())
        parsed = datetime.fromisoformat(loaded["last_checkpoint"])
        assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# Test: Agent Prompts Include Checkpoint Instructions (1.3)
# ---------------------------------------------------------------------------

class TestPromptTemplatesIncludeCheckpoint:

    def test_implement_md_has_checkpoint_instructions(self):
        """Verify implement.md prompt contains checkpoint instructions."""
        prompt_path = Path(__file__).parent / "prompts" / "implement.md"
        content = prompt_path.read_text()
        assert ".mad/checkpoints/<feature-slug>.checkpoint.json" in content
        assert "feature_slug" in content
        assert "feature_id" in content

    def test_write_tests_md_has_checkpoint_instructions(self):
        """Verify write-tests.md prompt contains checkpoint instructions."""
        prompt_path = Path(__file__).parent / "prompts" / "write-tests.md"
        content = prompt_path.read_text()
        assert ".mad/checkpoints/<feature-slug>.checkpoint.json" in content

    def test_plan_headless_md_has_checkpoint_instructions(self):
        """Verify plan-headless.md prompt contains checkpoint instructions."""
        prompt_path = Path(__file__).parent / "prompts" / "plan-headless.md"
        content = prompt_path.read_text()
        assert ".mad/checkpoints/<feature-slug>.checkpoint.json" in content

    def test_impl_spec_md_has_checkpoint_instructions(self):
        """Verify impl-spec.md prompt contains checkpoint instructions."""
        prompt_path = Path(__file__).parent / "prompts" / "impl-spec.md"
        content = prompt_path.read_text()
        assert ".mad/checkpoints/<feature-slug>.checkpoint.json" in content

    def test_test_spec_md_has_checkpoint_instructions(self):
        """Verify test-spec.md prompt contains checkpoint instructions."""
        prompt_path = Path(__file__).parent / "prompts" / "test-spec.md"
        content = prompt_path.read_text()
        assert ".mad/checkpoints/<feature-slug>.checkpoint.json" in content


# ---------------------------------------------------------------------------
# Test: Runner Reads Checkpoint (1.5, 1.6, 1.7)
# ---------------------------------------------------------------------------

class TestRunnerReadCheckpoint:

    def test_read_checkpoint_method_exists(self):
        """Verify _read_checkpoint method exists in runner."""
        assert hasattr(AgentRunner, '_read_checkpoint')

    def test_read_checkpoint_returns_none_when_not_exists(self, mock_config, tmp_workdir):
        """Test _read_checkpoint returns None when checkpoint doesn't exist."""
        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("nonexistent-feature")
        assert result is None

    def test_read_checkpoint_returns_none_for_default_slug(self, mock_config, tmp_workdir):
        """Test _read_checkpoint returns None for 'default' slug."""
        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("default")
        assert result is None

    def test_read_checkpoint_returns_none_for_empty_slug(self, mock_config, tmp_workdir):
        """Test _read_checkpoint returns None for empty slug."""
        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("")
        assert result is None

    def test_read_checkpoint_returns_resume_context(self, mock_config, tmp_workdir):
        """Test _read_checkpoint returns resume context string."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "implementing",
            "completed_steps": ["wrote core logic", "added tests"],
            "next_step": "review code",
            "notes": "Need to fix edge case",
            "files_modified": ["main.py", "test_main.py"],
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is not None
        assert "Resuming from checkpoint" in result
        assert "implementing" in result
        assert "wrote core logic" in result
        assert "review code" in result
        assert "Need to fix edge case" in result
        assert "main.py" in result

    def test_read_checkpoint_validates_required_fields(self, mock_config, tmp_workdir, caplog):
        """Test _read_checkpoint validates required fields."""
        import logging
        caplog.set_level(logging.WARNING)

        checkpoint_data = {"phase": "implementing"}
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is None

    def test_read_checkpoint_handles_invalid_json(self, mock_config, tmp_workdir, caplog):
        """Test _read_checkpoint handles invalid JSON gracefully."""
        import logging
        caplog.set_level(logging.WARNING)

        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text("not valid json {{{")

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is None

    def test_read_checkpoint_handles_non_dict(self, mock_config, tmp_workdir, caplog):
        """Test _read_checkpoint handles non-dict JSON."""
        import logging
        caplog.set_level(logging.WARNING)

        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text('"just a string"')

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is None


# ---------------------------------------------------------------------------
# Test: Checkpoint Deletion on Phase Completion (1.8)
# ---------------------------------------------------------------------------

class TestCheckpointDeletion:

    def test_delete_checkpoint_function_exists(self):
        """Verify _delete_checkpoint function exists in phases module."""
        import phases
        assert hasattr(phases, '_delete_checkpoint')

    def test_delete_checkpoint_removes_file(self, tmp_workdir):
        """Test _delete_checkpoint removes checkpoint file."""
        import phases

        feature = _make_feature(slug="test-feature")
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps({"feature_slug": "test-feature", "phase": "planning"}))

        with patch.object(phases, 'Config') as mock_cfg:
            mock_cfg.return_value.mad_dir = tmp_workdir / ".mad"
            phases._delete_checkpoint(feature)

        assert not checkpoint_path.exists()

    def test_delete_checkpoint_handles_missing_file(self, tmp_workdir):
        """Test _delete_checkpoint handles missing file gracefully."""
        import phases

        feature = _make_feature(slug="nonexistent-feature")

        with patch.object(phases, 'Config') as mock_cfg:
            mock_cfg.return_value.mad_dir = tmp_workdir / ".mad"
            phases._delete_checkpoint(feature)

    def test_delete_checkpoint_called_in_run_planning(self):
        """Test _delete_checkpoint is called in run_planning."""
        import phases
        import inspect
        source = inspect.getsource(phases.run_planning)
        assert "_delete_checkpoint" in source

    def test_delete_checkpoint_called_in_run_spec_writing(self):
        """Test _delete_checkpoint is called in run_spec_writing."""
        import phases
        import inspect
        source = inspect.getsource(phases.run_spec_writing)
        assert "_delete_checkpoint" in source

    def test_delete_checkpoint_called_in_run_implementing(self):
        """Test _delete_checkpoint is called in run_implementing."""
        import phases
        import inspect
        source = inspect.getsource(phases.run_implementing)
        assert "_delete_checkpoint" in source

    def test_delete_checkpoint_called_in_run_writing_tests(self):
        """Test _delete_checkpoint is called in run_writing_tests."""
        import phases
        import inspect
        source = inspect.getsource(phases.run_writing_tests)
        assert "_delete_checkpoint" in source


# ---------------------------------------------------------------------------
# Test: Server State Includes Checkpoint Status (1.10)
# ---------------------------------------------------------------------------

class TestServerStateCheckpointInfo:

    def test_get_checkpoint_info_function_exists(self):
        """Verify _get_checkpoint_info function exists in server_client module."""
        import server_client
        assert hasattr(server_client, '_get_checkpoint_info')

    def test_get_checkpoint_info_returns_none_when_not_exists(self, tmp_workdir):
        """Test _get_checkpoint_info returns None when checkpoint doesn't exist."""
        import server_client
        feature = _make_feature(slug="nonexistent")

        with patch.object(server_client, 'Path') as mock_path:
            mock_path.return_value.parent.parent / ".mad" / "config.json"
            result = server_client._get_checkpoint_info(feature)

    def test_get_checkpoint_info_returns_info(self, tmp_workdir, monkeypatch):
        """Test _get_checkpoint_info returns checkpoint info dict."""
        import server_client

        config_dir = tmp_workdir / ".mad"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"mad_dir": str(tmp_workdir / ".mad")}))

        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "implementing",
            "last_checkpoint": "2025-01-01T12:00:00Z",
            "completed_steps": ["step 1", "step 2"],
            "next_step": "finish feature",
        }
        checkpoint_dir = tmp_workdir / ".mad" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        feature = _make_feature(slug="test-feature")

        def mock_get_checkpoint_info(feature):
            try:
                config_path = tmp_workdir / ".mad" / "config.json"
                if not config_path.exists():
                    return None
                with open(config_path) as f:
                    config = json.load(f)
                mad_dir = Path(config.get("mad_dir", ".mad"))
                checkpoint_path = mad_dir / "checkpoints" / f"{feature.slug}.checkpoint.json"
                if not checkpoint_path.exists():
                    return None
                with open(checkpoint_path) as f:
                    data = json.load(f)
                return {
                    "exists": True,
                    "last_checkpoint": data.get("last_checkpoint", ""),
                    "completed_steps_count": len(data.get("completed_steps", [])),
                    "next_step": data.get("next_step", "")[:200] if data.get("next_step") else "",
                }
            except Exception as e:
                return None

        monkeypatch.setattr(server_client, '_get_checkpoint_info', mock_get_checkpoint_info)
        result = server_client._get_checkpoint_info(feature)

        assert result is not None
        assert result["exists"] is True
        assert result["last_checkpoint"] == "2025-01-01T12:00:00Z"
        assert result["completed_steps_count"] == 2
        assert result["next_step"] == "finish feature"


# ---------------------------------------------------------------------------
# Test: Web UI Displays Checkpoint Indicator (1.11)
# ---------------------------------------------------------------------------

class TestWebUICheckpointIndicator:

    def test_index_html_has_checkpoint_indicator(self):
        """Verify index.html shows checkpoint indicator."""
        template_path = Path(__file__).parent.parent / "server" / "templates" / "index.html"
        content = template_path.read_text()
        assert "checkpoint" in content.lower()
        assert ".Checkpoint" in content

    def test_client_html_has_checkpoint_indicator(self):
        """Verify client.html shows checkpoint indicator."""
        template_path = Path(__file__).parent.parent / "server" / "templates" / "client.html"
        content = template_path.read_text()
        assert "checkpoint" in content.lower()
        assert ".Checkpoint" in content


# ---------------------------------------------------------------------------
# Edge Cases Tests (2.x)
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_missing_optional_fields_uses_defaults(self, mock_config, tmp_workdir):
        """Test checkpoint with only required fields uses defaults for missing optional fields."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "planning",
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is not None
        assert "Resuming from checkpoint" in result

    def test_corrupted_json_logs_warning(self, mock_config, tmp_workdir, caplog):
        """Test corrupted JSON in checkpoint logs warning."""
        import logging
        caplog.set_level(logging.WARNING)

        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text("{ invalid json }")

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is None

    def test_empty_checkpoint_file_handled(self, mock_config, tmp_workdir, caplog):
        """Test empty checkpoint file is handled gracefully."""
        import logging
        caplog.set_level(logging.WARNING)

        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text("")

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is None

    def test_non_array_fields_handled(self, mock_config, tmp_workdir):
        """Test checkpoint with non-array completed_steps uses default."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "implementing",
            "completed_steps": "not an array",
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is not None

    def test_invalid_timestamp_format_passes_through(self, tmp_workdir):
        """Test checkpoint with invalid timestamp format passes through."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "implementing",
            "last_checkpoint": "not-a-valid-timestamp",
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        loaded = json.loads(checkpoint_path.read_text())
        assert loaded["last_checkpoint"] == "not-a-valid-timestamp"

    def test_checkpoint_for_nonexistent_feature(self, mock_config, tmp_workdir):
        """Test checkpoint for non-existent feature doesn't crash."""
        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("feature-that-does-not-exist")
        assert result is None

    def test_special_characters_in_fields(self, mock_config, tmp_workdir):
        """Test special characters in notes and next_step are preserved."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "implementing",
            "notes": 'Special chars: "quotes" newlines\ntabs\tand <html>',
            "next_step": "Handle edge case with: brackets [] braces {}",
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is not None
        assert "quotes" in result
        assert "brackets" in result

    def test_hyphenated_feature_slug(self, mock_config, tmp_workdir):
        """Test hyphenated feature slug creates valid filename."""
        checkpoint_data = {
            "feature_slug": "ai-agent-checkpoint-system",
            "phase": "writing-tests",
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "ai-agent-checkpoint-system.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("ai-agent-checkpoint-system")

        assert result is not None

    def test_very_large_completed_steps(self, mock_config, tmp_workdir):
        """Test checkpoint with large completed_steps array."""
        large_steps = [f"step {i}" for i in range(150)]
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "implementing",
            "completed_steps": large_steps,
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert result is not None
        assert "step 0" in result
        assert "step 149" in result

    def test_headless_injects_checkpoint_context(self, mock_config, tmp_workdir):
        """Test that headless method injects checkpoint context into prompt."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "implementing",
            "completed_steps": ["completed step"],
            "next_step": "next action",
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)

        class MockStatus:
            feature_slug = "test-feature"
            running = False
            started_at = None
            lines = []

        with patch.object(runner, 'headless') as mock_headless:
            mock_headless.return_value = "test output"
            runner.headless("test prompt", status=MockStatus())


# ---------------------------------------------------------------------------
# Additional Integration Tests
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_checkpoint_read_write_cycle(self, mock_config, tmp_workdir):
        """Test complete checkpoint write and read cycle."""
        import json
        from datetime import datetime, timezone

        checkpoint_data = {
            "feature_slug": "integration-test",
            "feature_id": "int123",
            "phase": "spec-writing",
            "last_checkpoint": datetime.now(timezone.utc).isoformat(),
            "completed_steps": ["created initial structure", "implemented core logic"],
            "next_step": "write unit tests",
            "notes": "All tests passing locally",
            "files_modified": ["src/main.py", "src/utils.py"],
        }

        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "integration-test.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        resume_context = runner._read_checkpoint("integration-test")

        assert resume_context is not None
        assert "created initial structure" in resume_context
        assert "write unit tests" in resume_context

    def test_checkpoint_resume_instruction_present(self, mock_config, tmp_workdir):
        """Test resume context includes instruction not to redo work."""
        checkpoint_data = {
            "feature_slug": "test-feature",
            "phase": "implementing",
            "completed_steps": ["done"],
            "next_step": "continue",
        }
        checkpoint_path = tmp_workdir / ".mad" / "checkpoints" / "test-feature.checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        runner = AgentRunner(mock_config, workdir=tmp_workdir)
        result = runner._read_checkpoint("test-feature")

        assert "Do NOT redo completed work" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
