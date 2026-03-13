"""Tests for move_feature message handling in server_client.py.

Run with:
    pytest test_move_feature.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from server_client import ServerClient


class FakeWebSocket:
    """Fake websocket connection for unit tests."""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self._sent = []
        self._closed = False

    async def send(self, data):
        self._sent.append(data)

    async def recv(self):
        if self._responses:
            return self._responses.pop(0)
        import asyncio
        await asyncio.sleep(3600)

    async def close(self):
        self._closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._responses:
            raise StopAsyncIteration
        return self._responses.pop(0)


class TestHandleMoveFeature:

    @pytest.mark.asyncio
    async def test_move_feature_dispatches_to_callback(self):
        received = {}

        async def on_move(feature_id, target_stage, request_id, reason):
            received["feature_id"] = feature_id
            received["target_stage"] = target_stage
            received["reason"] = reason

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "feat-123",
            "target_stage": "plan-inbox",
        })
        await sc._handle_incoming(msg)

        assert received["feature_id"] == "feat-123"
        assert received["target_stage"] == "plan-inbox"
        assert received["reason"] == ""

    @pytest.mark.asyncio
    async def test_move_feature_no_callback_no_crash(self):
        sc = ServerClient("ws://localhost:8080", "key", "c")
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "f1",
            "target_stage": "plan-inbox",
        })
        await sc._handle_incoming(msg)

    @pytest.mark.asyncio
    async def test_move_feature_empty_feature_id_ignored(self):
        called = False

        async def on_move(feature_id, target_stage, request_id, reason):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "",
            "target_stage": "plan-inbox",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_move_feature_missing_feature_id_ignored(self):
        called = False

        async def on_move(feature_id, target_stage, request_id, reason):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "target_stage": "plan-inbox",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_move_feature_empty_target_stage_ignored(self):
        called = False

        async def on_move(feature_id, target_stage, request_id, reason):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "f1",
            "target_stage": "",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_move_feature_missing_target_stage_ignored(self):
        called = False

        async def on_move(feature_id, target_stage, request_id, reason):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "f1",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_move_feature_null_feature_id_ignored(self):
        called = False

        async def on_move(feature_id, target_stage, request_id, reason):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": None,
            "target_stage": "plan-inbox",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_move_feature_null_target_stage_ignored(self):
        called = False

        async def on_move(feature_id, target_stage, request_id, reason):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "f1",
            "target_stage": None,
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_move_feature_callback_error_does_not_crash(self):
        async def on_move(feature_id, target_stage, request_id, reason):
            raise ValueError("handler exploded")

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": "f1",
            "target_stage": "plan-inbox",
        })
        await sc._handle_incoming(msg)

    @pytest.mark.asyncio
    async def test_move_feature_special_characters_preserved(self):
        received = {}

        async def on_move(feature_id, target_stage, request_id, reason):
            received["feature_id"] = feature_id
            received["target_stage"] = target_stage

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=on_move)
        special_id = "feat-with-special/chars&more"
        special_stage = "plan-inbox.with.dots"
        msg = json.dumps({
            "type": "move_feature",
            "feature_id": special_id,
            "target_stage": special_stage,
        })
        await sc._handle_incoming(msg)
        assert received["feature_id"] == special_id
        assert received["target_stage"] == special_stage


class TestOnMoveRequestedCallbackWiring:

    def test_callback_stored_on_init(self):
        async def cb(fid, stage, rid, r):
            pass

        sc = ServerClient("ws://localhost:8080", "key", "c", on_move_requested=cb)
        assert sc._on_move_requested is cb

    def test_callback_default_none(self):
        sc = ServerClient("ws://localhost:8080", "key", "c")
        assert sc._on_move_requested is None


class TestReconnectLoopMoveFeature:

    @pytest.mark.asyncio
    async def test_reconnect_loop_dispatches_move_feature(self):
        received = {}

        async def on_move(feature_id, target_stage, request_id, reason):
            received["feature_id"] = feature_id
            received["target_stage"] = target_stage

        ack = json.dumps({"type": "ack"})
        move_msg = json.dumps({
            "type": "move_feature",
            "feature_id": "f1",
            "target_stage": "plan-inbox",
        })
        fake_ws = FakeWebSocket(responses=[ack, move_msg])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            import asyncio
            mock_ws.connect = AsyncMock(return_value=fake_ws)

            sc = ServerClient("ws://localhost:8080", "key", "c",
                              on_move_requested=on_move)
            task = asyncio.create_task(sc.reconnect_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert received.get("feature_id") == "f1"
        assert received.get("target_stage") == "plan-inbox"
