"""Tests for start_agent feature end-to-end.

Run with:
    pytest test_start_agent_e2e.py -v
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from state import STAGE_ACTIONS
from server_client import ServerClient


class FakeWebSocket:
    def __init__(self, responses=None, fail_send=False, fail_recv=False):
        self._responses = list(responses or [])
        self._sent = []
        self._closed = False
        self._fail_send = fail_send
        self._fail_recv = fail_recv

    async def send(self, data):
        if self._fail_send:
            raise ConnectionError("send failed")
        self._sent.append(data)

    async def recv(self):
        if self._fail_recv:
            raise ConnectionError("recv failed")
        if self._responses:
            return self._responses.pop(0)
        await asyncio.sleep(3600)

    async def close(self):
        self._closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._fail_recv or not self._responses:
            raise StopAsyncIteration
        return self._responses.pop(0)


def _make_feature(title="Test Feature", stage="ideas", board="default",
                  created="2025-01-01", fid="f1", logs=None):
    f = SimpleNamespace()
    f.title = title
    f.current_stage = stage
    f.board = board
    f.created = created
    f.id = fid
    f.description = ""
    f.questions = []
    f._data = {"pipeline_log": logs or [], "history": []}
    return f


class TestStageActionsConfig:
    def test_plan_allowed_stages(self):
        expected = ["plan-inbox", "reviewing-plan", "requested-input", "approved"]
        assert STAGE_ACTIONS["plan"] == expected

    def test_implement_allowed_stages(self):
        expected = ["approved", "spec-writing"]
        assert STAGE_ACTIONS["implement"] == expected

    def test_approved_allows_both_actions(self):
        assert "approved" in STAGE_ACTIONS["plan"]
        assert "approved" in STAGE_ACTIONS["implement"]

    def test_plan_inbox_only_allows_plan(self):
        assert "plan-inbox" in STAGE_ACTIONS["plan"]
        assert "plan-inbox" not in STAGE_ACTIONS["implement"]

    def test_spec_writing_only_allows_implement(self):
        assert "spec-writing" in STAGE_ACTIONS["implement"]
        assert "spec-writing" not in STAGE_ACTIONS["plan"]

    def test_ideas_stage_allows_neither(self):
        assert "ideas" not in STAGE_ACTIONS["plan"]
        assert "ideas" not in STAGE_ACTIONS["implement"]

    def test_in_progress_allows_neither(self):
        assert "in-progress" not in STAGE_ACTIONS["plan"]
        assert "in-progress" not in STAGE_ACTIONS["implement"]

    def test_done_allows_neither(self):
        assert "done" not in STAGE_ACTIONS["plan"]
        assert "done" not in STAGE_ACTIONS["implement"]


class TestAvailableActionsComputation:
    @pytest.mark.asyncio
    async def test_reviewing_plan_stage_has_plan_action(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "reviewing-plan", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert "plan" in payload["features"][0]["available_actions"]

    @pytest.mark.asyncio
    async def test_requested_input_stage_has_plan_action(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "requested-input", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert "plan" in payload["features"][0]["available_actions"]

    @pytest.mark.asyncio
    async def test_plan_agent_busy_excludes_plan(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        plan_status = SimpleNamespace(running=True, phase="planning", feature_slug="f1", agent="claude")
        feature = _make_feature("F1", "plan-inbox", "board1", "2025-01-01", "f1")
        await sc.push_state([feature], plan_status=plan_status)

        payload = json.loads(fake_ws._sent[0])
        assert "plan" not in payload["features"][0]["available_actions"]

    @pytest.mark.asyncio
    async def test_impl_agent_busy_excludes_implement(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        impl_status = SimpleNamespace(running=True, phase="implementing", feature_slug="f1", agent="claude")
        feature = _make_feature("F1", "approved", "board1", "2025-01-01", "f1")
        await sc.push_state([feature], impl_status=impl_status)

        payload = json.loads(fake_ws._sent[0])
        assert "implement" not in payload["features"][0]["available_actions"]

    @pytest.mark.asyncio
    async def test_plan_busy_but_impl_available(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        plan_status = SimpleNamespace(running=True, phase="planning", feature_slug="f1", agent="claude")
        feature = _make_feature("F1", "approved", "board1", "2025-01-01", "f1")
        await sc.push_state([feature], plan_status=plan_status)

        payload = json.loads(fake_ws._sent[0])
        actions = payload["features"][0]["available_actions"]
        assert "plan" not in actions
        assert "implement" in actions

    @pytest.mark.asyncio
    async def test_impl_busy_but_plan_available(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        impl_status = SimpleNamespace(running=True, phase="implementing", feature_slug="f1", agent="claude")
        feature = _make_feature("F1", "approved", "board1", "2025-01-01", "f1")
        await sc.push_state([feature], impl_status=impl_status)

        payload = json.loads(fake_ws._sent[0])
        actions = payload["features"][0]["available_actions"]
        assert "implement" not in actions
        assert "plan" in actions


class TestStartAgentMessageHandling:
    @pytest.mark.asyncio
    async def test_start_agent_message_format(self):
        received = {}

        def on_start(feature_id, action):
            received["feature_id"] = feature_id
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "feature-abc-123",
            "action": "plan",
        })
        await sc._handle_incoming(msg)

        assert received["feature_id"] == "feature-abc-123"
        assert received["action"] == "plan"

    @pytest.mark.asyncio
    async def test_start_agent_with_special_chars_in_id(self):
        received = {}

        def on_start(feature_id, action):
            received["feature_id"] = feature_id
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "feature-with-dashes_underscores.123",
            "action": "implement",
        })
        await sc._handle_incoming(msg)

        assert received["feature_id"] == "feature-with-dashes_underscores.123"
        assert received["action"] == "implement"

    @pytest.mark.asyncio
    async def test_start_agent_plan_action_validation(self):
        received_actions = []

        def on_start(feature_id, action):
            received_actions.append(action)

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "plan",
        }))
        
        assert "plan" in received_actions

    @pytest.mark.asyncio
    async def test_start_agent_implement_action_validation(self):
        received_actions = []

        def on_start(feature_id, action):
            received_actions.append(action)

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "implement",
        }))
        
        assert "implement" in received_actions

    @pytest.mark.asyncio
    async def test_start_agent_case_sensitive(self):
        received = {}

        def on_start(feature_id, action):
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "PLAN",
        }))
        
        assert "action" not in received

    @pytest.mark.asyncio
    async def test_start_agent_mixed_case_rejected(self):
        received = {}

        def on_start(feature_id, action):
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "Plan",
        }))
        
        assert "action" not in received


class TestStartAgentEdgeCases:
    @pytest.mark.asyncio
    async def test_start_agent_empty_action_rejected(self):
        received = {}

        def on_start(feature_id, action):
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "",
        }))
        
        assert "action" not in received

    @pytest.mark.asyncio
    async def test_start_agent_null_action_rejected(self):
        received = {}

        def on_start(feature_id, action):
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": None,
        }))
        
        assert "action" not in received

    @pytest.mark.asyncio
    async def test_start_agent_invalid_action_rejected(self):
        received = {}

        def on_start(feature_id, action):
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "delete",
        }))
        
        assert "action" not in received

    @pytest.mark.asyncio
    async def test_start_agent_number_action_rejected(self):
        received = {}

        def on_start(feature_id, action):
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": 123,
        }))
        
        assert "action" not in received

    @pytest.mark.asyncio
    async def test_start_agent_boolean_action_rejected(self):
        received = {}

        def on_start(feature_id, action):
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        
        await sc._handle_incoming(json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": True,
        }))
        
        assert "action" not in received


class TestPushStatePayloadStructure:
    @pytest.mark.asyncio
    async def test_push_state_has_stage_actions_key(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        await sc.push_state([_make_feature()])

        payload = json.loads(fake_ws._sent[0])
        assert "stage_actions" in payload
        assert isinstance(payload["stage_actions"], dict)

    @pytest.mark.asyncio
    async def test_push_state_has_available_actions_per_feature(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "approved", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert "available_actions" in payload["features"][0]

    @pytest.mark.asyncio
    async def test_push_state_stage_actions_matches_config(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        await sc.push_state([_make_feature()])

        payload = json.loads(fake_ws._sent[0])
        assert payload["stage_actions"] == STAGE_ACTIONS


class TestStatePushWithDifferentStages:
    @pytest.mark.asyncio
    async def test_feature_plan_inbox_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "plan-inbox", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        actions = payload["features"][0]["available_actions"]
        assert "plan" in actions
        assert "implement" not in actions

    @pytest.mark.asyncio
    async def test_feature_reviewing_plan_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "reviewing-plan", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        actions = payload["features"][0]["available_actions"]
        assert "plan" in actions
        assert "implement" not in actions

    @pytest.mark.asyncio
    async def test_feature_approved_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "approved", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        actions = payload["features"][0]["available_actions"]
        assert "plan" in actions
        assert "implement" in actions

    @pytest.mark.asyncio
    async def test_feature_spec_writing_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "spec-writing", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        actions = payload["features"][0]["available_actions"]
        assert "implement" in actions
        assert "plan" not in actions


class TestConcurrentRequests:
    @pytest.mark.asyncio
    async def test_multiple_features_different_stages(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        f1 = _make_feature("F1", "plan-inbox", "b", "2025-01-01", "f1")
        f2 = _make_feature("F2", "approved", "b", "2025-01-01", "f2")
        f3 = _make_feature("F3", "spec-writing", "b", "2025-01-01", "f3")
        f4 = _make_feature("F4", "done", "b", "2025-01-01", "f4")
        f5 = _make_feature("F5", "ideas", "b", "2025-01-01", "f5")
        await sc.push_state([f1, f2, f3, f4, f5])

        payload = json.loads(fake_ws._sent[0])
        
        assert "plan" in payload["features"][0]["available_actions"]
        
        assert "plan" in payload["features"][1]["available_actions"]
        assert "implement" in payload["features"][1]["available_actions"]
        
        assert "implement" in payload["features"][2]["available_actions"]
        assert "plan" not in payload["features"][2]["available_actions"]
        
        assert payload["features"][3]["available_actions"] == []
        
        assert payload["features"][4]["available_actions"] == []


class TestAgentStateInPayload:
    @pytest.mark.asyncio
    async def test_push_state_includes_plan_agent_status(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        plan_status = SimpleNamespace(running=True, phase="planning", feature_slug="my-feature", agent="claude")
        await sc.push_state([_make_feature()], plan_status=plan_status)

        payload = json.loads(fake_ws._sent[0])
        assert payload["plan_agent"]["running"] is True
        assert payload["plan_agent"]["phase"] == "planning"
        assert payload["plan_agent"]["feature"] == "my-feature"
        assert payload["plan_agent"]["agent"] == "claude"

    @pytest.mark.asyncio
    async def test_push_state_includes_impl_agent_status(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        impl_status = SimpleNamespace(running=True, phase="implementing", feature_slug="my-feature", agent="claude")
        await sc.push_state([_make_feature()], impl_status=impl_status)

        payload = json.loads(fake_ws._sent[0])
        assert payload["impl_agent"]["running"] is True
        assert payload["impl_agent"]["phase"] == "implementing"
        assert payload["impl_agent"]["feature"] == "my-feature"

    @pytest.mark.asyncio
    async def test_push_state_includes_both_agents(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        plan_status = SimpleNamespace(running=True, phase="planning", feature_slug="plan-feat", agent="claude")
        impl_status = SimpleNamespace(running=True, phase="implementing", feature_slug="impl-feat", agent="claude")
        await sc.push_state([_make_feature()], plan_status=plan_status, impl_status=impl_status)

        payload = json.loads(fake_ws._sent[0])
        assert payload["plan_agent"]["running"] is True
        assert payload["impl_agent"]["running"] is True
