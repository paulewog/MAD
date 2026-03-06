"""Tests for auto-plan/auto-impl WebSocket controls in ServerClient."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from server_client import ServerClient


class TestPushStateAutoMode(unittest.IsolatedAsyncioTestCase):
    """Test that push_state() includes auto_plan and auto_impl booleans."""

    async def _make_connected_client(self):
        client = ServerClient("ws://localhost:9999", "key", "test-client")
        client._connected = True
        client._ws = AsyncMock()
        client._ws.send = AsyncMock()
        return client

    async def test_push_state_includes_auto_plan_true_auto_impl_false(self):
        client = await self._make_connected_client()
        await client.push_state([], auto_plan_enabled=True, auto_impl_enabled=False)
        sent = json.loads(client._ws.send.call_args[0][0])
        self.assertTrue(sent["auto_plan"])
        self.assertFalse(sent["auto_impl"])

    async def test_push_state_both_false_by_default(self):
        client = await self._make_connected_client()
        await client.push_state([])
        sent = json.loads(client._ws.send.call_args[0][0])
        self.assertFalse(sent["auto_plan"])
        self.assertFalse(sent["auto_impl"])

    async def test_push_state_both_true(self):
        client = await self._make_connected_client()
        await client.push_state([], auto_plan_enabled=True, auto_impl_enabled=True)
        sent = json.loads(client._ws.send.call_args[0][0])
        self.assertTrue(sent["auto_plan"])
        self.assertTrue(sent["auto_impl"])

    async def test_push_state_not_connected_returns_early(self):
        client = ServerClient("ws://localhost:9999", "key", "test-client")
        client._connected = False
        client._ws = None
        # Should not raise
        await client.push_state([], auto_plan_enabled=True, auto_impl_enabled=True)


class TestHandleIncomingSetAutoMode(unittest.IsolatedAsyncioTestCase):
    """Test _handle_incoming() for set_auto_mode messages."""

    async def test_set_auto_mode_plan_true(self):
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming(json.dumps({
            "type": "set_auto_mode", "mode": "plan", "enabled": True
        }))
        callback.assert_called_once_with("plan", True)

    async def test_set_auto_mode_impl_false(self):
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming(json.dumps({
            "type": "set_auto_mode", "mode": "impl", "enabled": False
        }))
        callback.assert_called_once_with("impl", False)

    async def test_set_auto_mode_no_callback_no_crash(self):
        client = ServerClient("ws://localhost:9999", "key", "test")
        # Should not raise
        await client._handle_incoming(json.dumps({
            "type": "set_auto_mode", "mode": "plan", "enabled": True
        }))

    async def test_set_auto_mode_callback_exception_caught(self):
        def bad_callback(mode, enabled):
            raise RuntimeError("boom")
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=bad_callback)
        # Should not raise
        await client._handle_incoming(json.dumps({
            "type": "set_auto_mode", "mode": "plan", "enabled": True
        }))


class TestHandleIncomingSetAutoModeInvalid(unittest.IsolatedAsyncioTestCase):
    """Test that invalid set_auto_mode messages are silently dropped."""

    async def _assert_callback_not_called(self, msg):
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming(json.dumps(msg))
        callback.assert_not_called()

    async def test_invalid_mode(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "foo", "enabled": True})

    async def test_empty_mode(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "", "enabled": True})

    async def test_missing_mode(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "enabled": True})

    async def test_enabled_not_bool_string(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "plan", "enabled": "yes"})

    async def test_enabled_not_bool_int(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "plan", "enabled": 1})

    async def test_enabled_null(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "plan", "enabled": None})

    async def test_missing_enabled(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "plan"})

    async def test_missing_both(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode"})

    async def test_null_mode(self):
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": None, "enabled": True})

    async def test_non_json_message(self):
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming("hello")
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
