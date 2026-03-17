"""Unit tests for agent_status.py — AgentStatus dataclass."""

import unittest

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from agent_status import AgentStatus


class TestAgentStatus(unittest.TestCase):
    """Tests for AgentStatus dataclass."""

    def test_defaults(self):
        """All fields have correct default values."""
        status = AgentStatus()

        self.assertEqual(status.agent, "")
        self.assertEqual(status.feature_slug, "")
        self.assertEqual(status.phase, "")
        self.assertEqual(status.started_at, 0.0)
        self.assertEqual(status.lines, [])
        self.assertEqual(status.running, False)
        self.assertIsNone(status.pid)
        self.assertIsNone(status.last_activity_at)
        self.assertEqual(status.kill_requested, False)
        self.assertEqual(status.restart_requested, False)

    def test_mutable_lines(self):
        """lines is a unique list per instance (not shared)."""
        status1 = AgentStatus()
        status2 = AgentStatus()

        status1.lines.append("test line")

        self.assertEqual(len(status1.lines), 1)
        self.assertEqual(len(status2.lines), 0)

    def test_field_assignment(self):
        """All fields assignable."""
        status = AgentStatus()
        status.agent = "claude"
        status.feature_slug = "test-feature"
        status.phase = "planning"
        status.started_at = 1234567890.0
        status.lines = ["line1", "line2"]
        status.running = True
        status.pid = 12345
        status.last_activity_at = 1234567891.0
        status.kill_requested = True
        status.restart_requested = True

        self.assertEqual(status.agent, "claude")
        self.assertEqual(status.feature_slug, "test-feature")
        self.assertEqual(status.phase, "planning")
        self.assertEqual(status.started_at, 1234567890.0)
        self.assertEqual(status.lines, ["line1", "line2"])
        self.assertEqual(status.running, True)
        self.assertEqual(status.pid, 12345)
        self.assertEqual(status.last_activity_at, 1234567891.0)
        self.assertEqual(status.kill_requested, True)
        self.assertEqual(status.restart_requested, True)


if __name__ == '__main__':
    unittest.main()
