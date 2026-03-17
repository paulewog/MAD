"""Unit tests for run_ideating function - cycle-based ideation loop."""

import json
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from typing import Optional

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from config import Config, IdeatingRound
from state import FeatureFile
from phases import run_ideating


class MockAgentStatus:
    """Mock AgentStatus for testing status updates."""
    def __init__(self):
        self.phase = ""
        self.agent = ""


class MockRunner:
    """Mock AgentRunner that returns configurable responses."""
    def __init__(self, round_responses=None, synthesis_response=None, per_call_overrides=None):
        self.round_responses = round_responses or []
        self.synthesis_response = synthesis_response or '{"Ideation": "Test ideation", "verdict": "progress", "verdict_reason": "test"}'
        # per_call_overrides is a dict with keys 'rounds': {call_num: response} and 'synthesis': {call_num: response}
        self.per_call_overrides = per_call_overrides or {'rounds': {}, 'synthesis': {}}
        self.call_count = 0
        self.round_call_count = 0
        self.synthesis_call_count = 0
        self.last_cli = None
        self.last_model = None
    
    def for_cli_model(self, cli, model):
        self.last_cli = cli
        self.last_model = model
        return self
    
    def headless(self, prompt, status=None, phase_key=None, output_schema=None):
        self.call_count += 1
        
        # Synthesis call (identified by output_schema containing verdict)
        if output_schema and "verdict" in output_schema:
            self.synthesis_call_count += 1
            # Check for per-call override by synthesis call number (1-indexed)
            if self.synthesis_call_count in self.per_call_overrides.get('synthesis', {}):
                return self.per_call_overrides['synthesis'][self.synthesis_call_count]
            return self.synthesis_response
        
        # Round call - increment count
        self.round_call_count += 1
        
        # Check for per-call override by round call number (1-indexed)
        if self.round_call_count in self.per_call_overrides.get('rounds', {}):
            return self.per_call_overrides['rounds'][self.round_call_count]
        
        # Return next in sequence or default
        if self.round_call_count <= len(self.round_responses):
            idx = self.round_call_count - 1
            return self.round_responses[idx]
        
        return '{"summary": "Default round response"}'


class TestIdeatingMaxRoundsConfig(unittest.TestCase):
    """Tests for Task 1.1: Config.ideating_max_rounds property."""

    def test_max_rounds_from_config(self):
        """When config has max_rounds, returns that value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps({
                "ideating": {"max_rounds": 8}
            }))
            config = Config(config_path)
            self.assertEqual(config.ideating_max_rounds, 8)

    def test_max_rounds_default_12(self):
        """When config has no max_rounds, defaults to 12."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps({
                "ideating": {"rounds": [{"cli": "claude"}]}
            }))
            config = Config(config_path)
            self.assertEqual(config.ideating_max_rounds, 12)

    def test_max_rounds_no_ideating_section(self):
        """When config has no ideating section, defaults to 12."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps({}))
            config = Config(config_path)
            self.assertEqual(config.ideating_max_rounds, 12)


class TestIdeationCycleVerdicts(unittest.TestCase):
    """Tests for Task 1.2: Cycle verdict tracking in FeatureFile."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.feature_path = self.board_dir / "test.json"
        self.feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
        }))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_ideation_cycle_verdicts_empty_initially(self):
        """New feature returns empty list."""
        feature = FeatureFile(self.feature_path)
        self.assertEqual(feature.ideation_cycle_verdicts, [])

    def test_add_ideation_cycle_verdict(self):
        """add_ideation_cycle_verdict appends a record."""
        feature = FeatureFile(self.feature_path)
        feature.add_ideation_cycle_verdict(1, "consensus", "Agents agree", 4)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["cycle"], 1)
        self.assertEqual(verdicts[0]["verdict"], "consensus")
        self.assertEqual(verdicts[0]["reason"], "Agents agree")
        self.assertEqual(verdicts[0]["rounds_completed"], 4)

    def test_add_multiple_verdicts(self):
        """Multiple calls append, not overwrite."""
        feature = FeatureFile(self.feature_path)
        feature.add_ideation_cycle_verdict(1, "progress", "Round 1 done", 4)
        feature.add_ideation_cycle_verdict(2, "consensus", "Round 2 done", 8)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(len(verdicts), 2)
        self.assertEqual(verdicts[0]["verdict"], "progress")
        self.assertEqual(verdicts[1]["verdict"], "consensus")

    def test_clear_ideation_cycle_verdicts(self):
        """clear_ideation_cycle_verdicts resets to empty list."""
        feature = FeatureFile(self.feature_path)
        feature.add_ideation_cycle_verdict(1, "progress", "test", 4)
        feature.clear_ideation_cycle_verdicts()
        
        self.assertEqual(feature.ideation_cycle_verdicts, [])

    def test_verdicts_persist_to_disk(self):
        """Verdicts persist after save/reload."""
        feature = FeatureFile(self.feature_path)
        feature.add_ideation_cycle_verdict(1, "consensus", "Agents agree", 4)
        
        # Reload
        feature2 = FeatureFile(self.feature_path)
        self.assertEqual(len(feature2.ideation_cycle_verdicts), 1)
        self.assertEqual(feature2.ideation_cycle_verdicts[0]["verdict"], "consensus")


class TestIdeationMaxRoundsOverride(unittest.TestCase):
    """Tests for Task 1.3: Per-item ideation_max_rounds override."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.feature_path = self.board_dir / "test.json"
        self.feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
        }))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_ideation_max_rounds_none_initially(self):
        """Feature without field returns None."""
        feature = FeatureFile(self.feature_path)
        self.assertIsNone(feature.ideation_max_rounds)

    def test_set_ideation_max_rounds(self):
        """set_ideation_max_rounds sets the value."""
        feature = FeatureFile(self.feature_path)
        feature.set_ideation_max_rounds(24)
        
        self.assertEqual(feature.ideation_max_rounds, 24)

    def test_set_ideation_max_rounds_none_removes_key(self):
        """set_ideation_max_rounds(None) removes the key."""
        feature = FeatureFile(self.feature_path)
        feature.set_ideation_max_rounds(24)
        feature.set_ideation_max_rounds(None)
        
        self.assertIsNone(feature.ideation_max_rounds)
        
        # Verify removed from JSON
        data = json.loads(self.feature_path.read_text())
        self.assertNotIn("ideation_max_rounds", data)

    def test_ideation_max_rounds_persists(self):
        """Per-item override persists after save/reload."""
        feature = FeatureFile(self.feature_path)
        feature.set_ideation_max_rounds(24)
        
        feature2 = FeatureFile(self.feature_path)
        self.assertEqual(feature2.ideation_max_rounds, 24)


class TestCompactMdPrompt(unittest.TestCase):
    """Tests for Task 1.4: compact.md includes verdict instructions."""

    def test_compact_md_has_cycle_number_placeholder(self):
        """Template contains {cycle_number}."""
        prompts_dir = Path(__file__).parent.parent / "pipeline" / "prompts"
        compact_md = (prompts_dir / "compact.md").read_text()
        self.assertIn("{cycle_number}", compact_md)

    def test_compact_md_has_total_rounds_placeholder(self):
        """Template contains {total_rounds_completed}."""
        prompts_dir = Path(__file__).parent.parent / "pipeline" / "prompts"
        compact_md = (prompts_dir / "compact.md").read_text()
        self.assertIn("{total_rounds_completed}", compact_md)

    def test_compact_md_has_previous_verdicts_section(self):
        """Template contains {previous_verdicts_section}."""
        prompts_dir = Path(__file__).parent.parent / "pipeline" / "prompts"
        compact_md = (prompts_dir / "compact.md").read_text()
        self.assertIn("{previous_verdicts_section}", compact_md)

    def test_compact_md_output_schema(self):
        """Template instructs output with verdict field."""
        prompts_dir = Path(__file__).parent.parent / "pipeline" / "prompts"
        compact_md = (prompts_dir / "compact.md").read_text()
        self.assertIn("verdict", compact_md)
        self.assertIn("verdict_reason", compact_md)

    def test_compact_md_anti_optimism_bias(self):
        """Template includes anti-optimism-bias instruction."""
        prompts_dir = Path(__file__).parent.parent / "pipeline" / "prompts"
        compact_md = (prompts_dir / "compact.md").read_text()
        self.assertIn("NOT present in previous cycles", compact_md)
        self.assertIn("20%", compact_md)


class TestCycleBasedExecutionLoop(unittest.TestCase):
    """Tests for Task 1.5: Cycle-based execution loop."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)
        
        # Config file with 4 rounds per cycle
        self.config_path = Path(self.tmpdir) / "config.json"
        self.config_path.write_text(json.dumps({
            "boards": ["default"],
            "ideating": {
                "rounds": [
                    {"cli": "claude"},
                    {"cli": "claude"},
                    {"cli": "claude"},
                    {"cli": "claude"}
                ],
                "max_rounds": 12
            }
        }))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self, max_rounds_override=None):
        """Helper to create a test feature."""
        feature_path = self.board_dir / "test.json"
        data = {
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }
        if max_rounds_override is not None:
            data["ideation_max_rounds"] = max_rounds_override
        feature_path.write_text(json.dumps(data))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_single_cycle_consensus(self, mock_config_class):
        """Consensus verdict after first cycle terminates."""
        feature = self._create_feature()
        
        # Mock config
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 12
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        # Mock runner - 4 rounds then consensus
        runner = MockRunner(
            round_responses=['{"summary": "Round 1"}'] * 4,
            synthesis_response='{"Ideation": "Final ideation", "verdict": "consensus", "verdict_reason": "Agents agree"}'
        )
        
        run_ideating(feature, runner)
        
        # Should have 4 rounds + 1 synthesis = 5 calls
        self.assertEqual(runner.call_count, 5)
        # Should be in ideas stage
        self.assertEqual(feature.current_stage, "ideas")

    @patch('phases.Config')
    def test_single_cycle_stall(self, mock_config_class):
        """Stall verdict after first cycle terminates."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 12
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            round_responses=['{"summary": "Round 1"}'] * 4,
            synthesis_response='{"Ideation": "Final ideation", "verdict": "stall", "verdict_reason": "No new points"}'
        )
        
        run_ideating(feature, runner)
        
        self.assertEqual(feature.current_stage, "ideas")
        # Check stall note in ideation
        self.assertIn("stall", feature.Ideation.lower())

    @patch('phases.Config')
    def test_two_cycles_then_consensus(self, mock_config_class):
        """Progress verdict after cycle 1, consensus after cycle 2 terminates."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 12
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        # 8 rounds (2 cycles) then consensus
        # Override first synthesis call (synthesis_call_count=1) to return progress
        runner = MockRunner(
            round_responses=['{"summary": "Round 1"}'] * 8,
            synthesis_response='{"Ideation": "Final ideation", "verdict": "consensus", "verdict_reason": "Agents agree"}',
            per_call_overrides={
                'synthesis': {
                    1: '{"Ideation": "Cycle 1 synthesis", "verdict": "progress", "verdict_reason": "More to explore"}'  # First synthesis call returns progress
                }
            }
        )
        
        run_ideating(feature, runner)
        
        # 8 rounds + 2 synthesis = 10 calls
        self.assertEqual(runner.call_count, 10)
        self.assertEqual(feature.current_stage, "ideas")


class TestHardCapTermination(unittest.TestCase):
    """Tests for Task 1.6: Hard cap termination."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)
        
        self.config_path = Path(self.tmpdir) / "config.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self, max_rounds_override=None):
        feature_path = self.board_dir / "test.json"
        data = {
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }
        if max_rounds_override is not None:
            data["ideation_max_rounds"] = max_rounds_override
        feature_path.write_text(json.dumps(data))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_hard_cap_stops_at_max_rounds(self, mock_config_class):
        """When max_rounds reached, ideation stops."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 8  # Only 2 cycles
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        # Always return progress - should still stop at hard cap
        runner = MockRunner(
            round_responses=['{"summary": "Round 1"}'] * 8,
            synthesis_response='{"Ideation": "Final ideation", "verdict": "progress", "verdict_reason": "Still going"}'
        )
        
        run_ideating(feature, runner)
        
        # 8 rounds + 2 synthesis = 10 calls (not 12)
        self.assertEqual(runner.call_count, 10)
        self.assertEqual(feature.current_stage, "ideas")

    @patch('phases.Config')
    def test_hard_cap_mid_cycle(self, mock_config_class):
        """Hard cap triggers mid-cycle correctly."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 6  # Only 1.5 cycles
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            round_responses=['{"summary": "Round 1"}'] * 6,
            synthesis_response='{"Ideation": "Final ideation", "verdict": "progress", "verdict_reason": "test"}'
        )
        
        run_ideating(feature, runner)
        
        # Cycle 1: 4 rounds + 1 synthesis = 5 calls
        # Cycle 2: 2 rounds + 1 synthesis = 3 calls
        # Total: 8 calls (hard cap still runs synthesis for the partial cycle)
        self.assertEqual(runner.call_count, 8)

    @patch('phases.Config')
    def test_per_item_override_priority(self, mock_config_class):
        """Per-item override takes priority over config."""
        feature = self._create_feature(max_rounds_override=16)
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 12  # Config default
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        # Provide enough responses for 4 cycles (16 rounds)
        runner = MockRunner(
            round_responses=['{"summary": "Round 1"}'] * 16,
            synthesis_response='{"Ideation": "Final ideation", "verdict": "progress", "verdict_reason": "test"}'
        )
        
        run_ideating(feature, runner)
        
        # Per-item override is 16, so runs 4 cycles = 16 rounds + 4 synthesis = 20 calls
        self.assertEqual(runner.call_count, 20)


class TestVerdictParsing(unittest.TestCase):
    """Tests for Task 1.7: Verdict parsing and normalization."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self):
        feature_path = self.board_dir / "test.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_exact_lowercase_verdict(self, mock_config_class):
        """Exact lowercase verdict accepted."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(verdicts[0]["verdict"], "consensus")

    @patch('phases.Config')
    def test_mixed_case_verdict_normalized(self, mock_config_class):
        """Mixed case verdict normalized to lowercase."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "Consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(verdicts[0]["verdict"], "consensus")

    @patch('phases.Config')
    def test_uppercase_verdict_normalized(self, mock_config_class):
        """Uppercase verdict normalized."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "STALL", "verdict_reason": "stuck"}'
        )
        
        run_ideating(feature, runner)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(verdicts[0]["verdict"], "stall")

    @patch('phases.Config')
    def test_unknown_verdict_defaults_to_progress(self, mock_config_class):
        """Unknown verdict defaults to progress."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "maybe", "verdict_reason": "unsure"}'
        )
        
        run_ideating(feature, runner)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(verdicts[0]["verdict"], "progress")

    @patch('phases.Config')
    def test_non_json_output_defaults_to_progress(self, mock_config_class):
        """Non-JSON output defaults to progress."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='This is just plain text, not JSON'
        )
        
        run_ideating(feature, runner)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(verdicts[0]["verdict"], "progress")

    @patch('phases.Config')
    def test_empty_output_handled_gracefully(self, mock_config_class):
        """Empty output handled without crash."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(synthesis_response="")
        
        # Should not raise
        run_ideating(feature, runner)
        
        self.assertEqual(feature.current_stage, "ideas")


class TestTerminationReasonInIdeation(unittest.TestCase):
    """Tests for Task 1.8: Termination reason in final Ideation text."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self):
        feature_path = self.board_dir / "test.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_consensus_termination_note(self, mock_config_class):
        """Consensus termination adds note to Ideation."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Base ideation text", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        self.assertIn("consensus", feature.Ideation.lower())
        self.assertIn("concluded", feature.Ideation.lower())

    @patch('phases.Config')
    def test_stall_termination_note(self, mock_config_class):
        """Stall termination adds note with 'Full convergence was not achieved'."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Base ideation text", "verdict": "stall", "verdict_reason": "stuck"}'
        )
        
        run_ideating(feature, runner)
        
        self.assertIn("stall", feature.Ideation.lower())
        self.assertIn("Full convergence was not achieved", feature.Ideation)

    @patch('phases.Config')
    def test_hard_cap_termination_note(self, mock_config_class):
        """Hard cap termination adds note with 'hard cap'."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 2  # Force hard cap
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Base ideation", "verdict": "progress", "verdict_reason": "more"}'
        )
        
        run_ideating(feature, runner)
        
        self.assertIn("hard cap", feature.Ideation.lower())


class TestVerdictLoggingInHistory(unittest.TestCase):
    """Tests for Task 1.9: Verdict logging in feature history."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self):
        feature_path = self.board_dir / "test.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
            "history": [],
        }))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_history_logs_cycle_starts(self, mock_config_class):
        """History includes round start entries."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude"), IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        history_str = feature.history
        self.assertIn("Starting round", history_str)

    @patch('phases.Config')
    def test_history_logs_synthesis(self, mock_config_class):
        """History includes synthesis run entries."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        history_str = feature.history
        self.assertIn("synthesis", history_str.lower())

    @patch('phases.Config')
    def test_history_logs_termination(self, mock_config_class):
        """History includes termination event with reason."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        history_str = feature.history
        self.assertIn("Consensus", history_str)

    @patch('phases.Config')
    def test_history_logs_continuation(self, mock_config_class):
        """History includes continuation entries for progress verdict."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 8  # Need 2 cycles
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}',
            per_call_overrides={
                'synthesis': {
                    1: '{"Ideation": "Cycle 1", "verdict": "progress", "verdict_reason": "more"}'  # First synthesis call returns progress
                }
            }
        )
        
        run_ideating(feature, runner)
        
        history_str = feature.history
        self.assertIn("progress", history_str.lower())


class TestCycleVerdictRecords(unittest.TestCase):
    """Tests for Task 1.10: Cycle verdict records in feature JSON."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self):
        feature_path = self.board_dir / "test.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_one_cycle_one_record(self, mock_config_class):
        """One cycle produces one verdict record."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["cycle"], 1)

    @patch('phases.Config')
    def test_two_cycles_two_records(self, mock_config_class):
        """Two cycles produce two verdict records."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 8
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}',
            per_call_overrides={
                'synthesis': {
                    1: '{"Ideation": "Cycle 1", "verdict": "progress", "verdict_reason": "more"}'
                }
            }
        )
        
        run_ideating(feature, runner)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertEqual(len(verdicts), 2)
        self.assertEqual(verdicts[0]["verdict"], "progress")
        self.assertEqual(verdicts[1]["verdict"], "consensus")

    @patch('phases.Config')
    def test_verdict_record_fields(self, mock_config_class):
        """Verdict record has correct fields."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "stall", "verdict_reason": "No movement"}'
        )
        
        run_ideating(feature, runner)
        
        verdicts = feature.ideation_cycle_verdicts
        self.assertIn("cycle", verdicts[0])
        self.assertIn("verdict", verdicts[0])
        self.assertIn("reason", verdicts[0])
        self.assertIn("rounds_completed", verdicts[0])


class TestStatusUpdates(unittest.TestCase):
    """Tests for Task 1.11: Status updates during execution."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self):
        feature_path = self.board_dir / "test.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_status_phase_includes_cycle_number(self, mock_config_class):
        """Status.phase includes cycle number during rounds."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        status = MockAgentStatus()
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner, status=status)
        
        self.assertIn("cycle", status.phase.lower())

    @patch('phases.Config')
    def test_status_phase_during_synthesis(self, mock_config_class):
        """Status.phase set to synthesis during synthesis."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        status = MockAgentStatus()
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner, status=status)
        
        self.assertIn("synthesis", status.phase.lower())


class TestPostTerminationBehavior(unittest.TestCase):
    """Tests for Task 1.12: Post-termination behavior."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self):
        feature_path = self.board_dir / "test.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_moves_to_ideas_stage(self, mock_config_class):
        """After ideation, feature moves to ideas stage."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test ideation", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        self.assertEqual(feature.current_stage, "ideas")

    @patch('phases.Config')
    def test_ideation_field_set(self, mock_config_class):
        """Ideation field is set after termination."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 4
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Final synthesized ideation", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        self.assertTrue(len(feature.Ideation) > 0)

    @patch('phases.Config')
    def test_summaries_accumulated_across_cycles(self, mock_config_class):
        """Summaries from all rounds across all cycles are accumulated."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 8
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            round_responses=['{"summary": "Round 1 summary"}', '{"summary": "Round 2 summary"}'],
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}',
            per_call_overrides={
                'synthesis': {
                    1: '{"Ideation": "Cycle 1", "verdict": "progress", "verdict_reason": "more"}'
                }
            }
        )
        
        run_ideating(feature, runner)
        
        summaries = feature.ideation_summaries
        self.assertEqual(len(summaries), 2)


class TestBackwardCompatibility(unittest.TestCase):
    """Tests for Task 1.13: Backward compatibility."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self):
        feature_path = self.board_dir / "test.json"
        feature_path.write_text(json.dumps({
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_single_cycle_matches_old_behavior(self, mock_config_class):
        """First-cycle consensus behaves like old fixed-round system."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        # 4 rounds per cycle
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 12  # Default
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            round_responses=['{"summary": "Round 1"}'] * 4,
            synthesis_response='{"Ideation": "Final ideation", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        # Should be 4 rounds + 1 synthesis = 5 calls (same as old behavior)
        self.assertEqual(runner.call_count, 5)
        self.assertEqual(feature.current_stage, "ideas")

    @patch('phases.Config')
    def test_feature_without_ideation_max_rounds_loads(self, mock_config_class):
        """Feature JSON without ideation_max_rounds loads without error."""
        feature = self._create_feature()
        
        # This should not raise
        self.assertIsNone(feature.ideation_max_rounds)

    @patch('phases.Config')
    def test_feature_without_ideation_cycle_verdicts_loads(self, mock_config_class):
        """Feature JSON without ideation_cycle_verdicts loads without error."""
        feature = self._create_feature()
        
        # This should not raise
        self.assertEqual(feature.ideation_cycle_verdicts, [])


class TestEdgeCases(unittest.TestCase):
    """Tests for Edge Cases 2.1-2.5."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.board_dir = Path(self.tmpdir) / "boards" / "default" / "ideating"
        self.board_dir.mkdir(parents=True)
        self.conversations_dir = Path(self.tmpdir) / "boards" / "default" / ".conversations"
        self.conversations_dir.mkdir(parents=True)
        self.ideas_dir = Path(self.tmpdir) / "boards" / "default" / "ideas"
        self.ideas_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_feature(self, extra_data=None):
        feature_path = self.board_dir / "test.json"
        data = {
            "id": "test123",
            "board": "default",
            "title": "Test Feature",
            "description": "Test description",
            "created": "2024-01-01T00:00:00Z",
            "ideation_summaries": [],
        }
        if extra_data:
            data.update(extra_data)
        feature_path.write_text(json.dumps(data))
        return FeatureFile(feature_path)

    @patch('phases.Config')
    def test_empty_rounds_returns_early(self, mock_config_class):
        """With no rounds configured, returns early."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = []  # Empty
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner()
        
        # Should return without error
        run_ideating(feature, runner)
        
        # No history entries for rounds
        history_str = feature.history
        self.assertNotIn("Starting round", history_str)

    @patch('phases.Config')
    def test_max_rounds_equals_rounds_per_cycle(self, mock_config_class):
        """max_rounds equals rounds_per_cycle = exactly 1 cycle."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 4  # Exactly one cycle
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "progress", "verdict_reason": "more"}'
        )
        
        run_ideating(feature, runner)
        
        # Should run 4 rounds + 1 synthesis = 5 calls, then stop due to hard cap
        self.assertEqual(runner.call_count, 5)

    @patch('phases.Config')
    def test_max_rounds_less_than_rounds_per_cycle(self, mock_config_class):
        """max_rounds less than rounds_per_cycle runs partial cycle."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude") for _ in range(4)]
        mock_config.ideating_max_rounds = 2  # Less than full cycle
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "progress", "verdict_reason": "more"}'
        )
        
        run_ideating(feature, runner)
        
        # Should run 2 rounds + 1 synthesis = 3 calls
        self.assertEqual(runner.call_count, 3)

    @patch('phases.Config')
    def test_max_rounds_of_zero(self, mock_config_class):
        """max_rounds of 0 runs no rounds."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]
        mock_config.ideating_max_rounds = 0
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner()
        
        run_ideating(feature, runner)
        
        # No calls should be made
        self.assertEqual(runner.call_count, 0)

    @patch('phases.Config')
    def test_single_round_per_cycle(self, mock_config_class):
        """Single round in rounds list = single cycle of 1 round."""
        feature = self._create_feature()
        
        mock_config = MagicMock()
        mock_config.ideating_rounds = [IdeatingRound(cli="claude")]  # 1 round
        mock_config.ideating_max_rounds = 3  # 3 cycles
        mock_config.boards_dir = Path(self.tmpdir) / "boards"
        mock_config.mad_dir = Path(self.tmpdir)
        mock_config_class.return_value = mock_config
        
        runner = MockRunner(
            synthesis_response='{"Ideation": "Test", "verdict": "consensus", "verdict_reason": "agree"}'
        )
        
        run_ideating(feature, runner)
        
        # Should be 1 round + 1 synthesis = 2 calls
        self.assertEqual(runner.call_count, 2)


if __name__ == '__main__':
    unittest.main()
