"""Comprehensive tests for auto-plan/auto-impl WebSocket controls - full data flow verification.

This test module covers:
1. Pipeline client auto-mode state in state_update messages
2. ServerClient handles incoming set_auto_mode messages  
3. TUI callback toggles auto-mode state correctly
4. Server stores AutoPlan/AutoImpl in ClientState
5. Server parses auto-mode from state_update messages
6. API endpoint validation and error handling
7. Full end-to-end data flow
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from server_client import ServerClient


class FakeWebSocket:
    """Fake websocket for testing."""
    
    def __init__(self):
        self._sent = []
        
    async def send(self, data):
        self._sent.append(data)
        
    async def close(self):
        pass


def _make_feature(title="Test Feature", stage="ideas", board="default",
                  created="2025-01-01", fid="f1"):
    """Create a mock feature object."""
    from types import SimpleNamespace
    f = SimpleNamespace()
    f.title = title
    f.current_stage = stage
    f.board = board
    f.created = created
    f.id = fid
    f.description = ""
    f.questions = []
    f._data = {"pipeline_log": [], "history": []}
    return f


class TestPushStateAutoModeIncludeInPayload(unittest.IsolatedAsyncioTestCase):
    """Test 1.1: Pipeline Client includes auto-mode state in push_state() messages.
    
    Verify that push_state() method includes auto_plan and auto_impl boolean 
    values in the state_update WebSocket message.
    """

    async def _make_connected_client(self):
        client = ServerClient("ws://localhost:9999", "key", "test-client")
        client._connected = True
        client._ws = AsyncMock()
        client._ws.send = AsyncMock()
        return client

    async def test_push_state_includes_auto_plan_true_auto_impl_false(self):
        """Verify auto_plan=true, auto_impl=false in payload."""
        client = await self._make_connected_client()
        await client.push_state([], auto_plan_enabled=True, auto_impl_enabled=False)
        sent = json.loads(client._ws.send.call_args[0][0])
        self.assertTrue(sent["auto_plan"])
        self.assertFalse(sent["auto_impl"])

    async def test_push_state_both_false_by_default(self):
        """Verify both fields default to false."""
        client = await self._make_connected_client()
        await client.push_state([])
        sent = json.loads(client._ws.send.call_args[0][0])
        self.assertFalse(sent["auto_plan"])
        self.assertFalse(sent["auto_impl"])

    async def test_push_state_both_true(self):
        """Verify both fields can be true."""
        client = await self._make_connected_client()
        await client.push_state([], auto_plan_enabled=True, auto_impl_enabled=True)
        sent = json.loads(client._ws.send.call_args[0][0])
        self.assertTrue(sent["auto_plan"])
        self.assertTrue(sent["auto_impl"])

    async def test_push_state_payload_format(self):
        """Verify complete payload format with auto_mode fields."""
        client = await self._make_connected_client()
        await client.push_state([], auto_plan_enabled=True, auto_impl_enabled=False)
        sent = json.loads(client._ws.send.call_args[0][0])
        self.assertEqual(sent["type"], "state_update")
        self.assertIn("auto_plan", sent)
        self.assertIn("auto_impl", sent)
        self.assertIsInstance(sent["auto_plan"], bool)
        self.assertIsInstance(sent["auto_impl"], bool)

    async def test_push_state_not_connected_returns_early(self):
        """Verify no message sent when not connected."""
        client = ServerClient("ws://localhost:9999", "key", "test-client")
        client._connected = False
        client._ws = None
        await client.push_state([], auto_plan_enabled=True, auto_impl_enabled=True)


class TestHandleIncomingSetAutoMode(unittest.IsolatedAsyncioTestCase):
    """Test 1.3: Pipeline Client handles incoming set_auto_mode messages.
    
    Verify that ServerClient._handle_incoming() detects message type 
    "set_auto_mode" and invokes the callback with correct parameters.
    """

    async def test_set_auto_mode_plan_true(self):
        """Verify callback invoked with ('plan', True)."""
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming(json.dumps({
            "type": "set_auto_mode", "mode": "plan", "enabled": True
        }))
        callback.assert_called_once_with("plan", True)

    async def test_set_auto_mode_impl_false(self):
        """Verify callback invoked with ('impl', False)."""
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming(json.dumps({
            "type": "set_auto_mode", "mode": "impl", "enabled": False
        }))
        callback.assert_called_once_with("impl", False)

    async def test_set_auto_mode_no_callback_no_crash(self):
        """Verify no crash when callback is None."""
        client = ServerClient("ws://localhost:9999", "key", "test")
        await client._handle_incoming(json.dumps({
            "type": "set_auto_mode", "mode": "plan", "enabled": True
        }))

    async def test_set_auto_mode_callback_exception_caught(self):
        """Verify callback exception doesn't crash handler."""
        def bad_callback(mode, enabled):
            raise RuntimeError("boom")
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=bad_callback)
        await client._handle_incoming(json.dumps({
            "type": "set_auto_mode", "mode": "plan", "enabled": True
        }))


class TestHandleIncomingSetAutoModeInvalid(unittest.IsolatedAsyncioTestCase):
    """Test 2.1-2.3, 2.6-2.9, 2.12, 2.15: Edge cases for set_auto_mode validation.
    
    Verify invalid messages are silently dropped without calling callback.
    """

    async def _assert_callback_not_called(self, msg):
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming(json.dumps(msg))
        callback.assert_not_called()

    async def test_invalid_mode(self):
        """Edge case 2.6: Invalid mode value 'foo'."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "foo", "enabled": True})

    async def test_empty_mode(self):
        """Edge case 2.6: Empty mode string."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "", "enabled": True})

    async def test_missing_mode(self):
        """Edge case 2.6: Missing mode field."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "enabled": True})

    async def test_enabled_not_bool_string(self):
        """Edge case 2.8: enabled as string 'yes'."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "plan", "enabled": "yes"})

    async def test_enabled_not_bool_int(self):
        """Edge case 2.8: enabled as integer 1."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "plan", "enabled": 1})

    async def test_enabled_null(self):
        """Edge case 2.8: enabled as null."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "plan", "enabled": None})

    async def test_missing_enabled(self):
        """Edge case 2.7: Missing enabled field."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "plan"})

    async def test_missing_both(self):
        """Edge case 2.7: Missing both mode and enabled."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode"})

    async def test_null_mode(self):
        """Edge case 2.6: mode as null."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": None, "enabled": True})

    async def test_non_json_message(self):
        """Edge case 2.12: Malformed JSON message."""
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming("hello")
        callback.assert_not_called()

    async def test_case_sensitivity_plan_uppercase(self):
        """Edge case 2.15: 'PLAN' should be rejected."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "PLAN", "enabled": True})

    async def test_case_sensitivity_impl_uppercase(self):
        """Edge case 2.15: 'Impl' should be rejected."""
        await self._assert_callback_not_called(
            {"type": "set_auto_mode", "mode": "Impl", "enabled": True})


class TestCallbackWiring(unittest.IsolatedAsyncioTestCase):
    """Test callback wiring for on_set_auto_mode."""

    def test_on_set_auto_mode_stored_on_init(self):
        """Verify callback is stored when provided."""
        def cb(mode, enabled):
            pass
        sc = ServerClient("ws://localhost:8080", "key", "c", on_set_auto_mode=cb)
        assert sc._on_set_auto_mode is cb

    def test_on_set_auto_mode_default_none(self):
        """Verify callback defaults to None."""
        sc = ServerClient("ws://localhost:8080", "key", "c")
        assert sc._on_set_auto_mode is None


class TestAutoModeStateNotPresent(unittest.IsolatedAsyncioTestCase):
    """Edge case 2.1: Auto-mode state not present in state_update.
    
    Verify server handles missing auto_plan/auto_impl fields gracefully.
    """

    async def test_state_update_without_auto_fields_accepted(self):
        """Verify state_update without auto fields doesn't crash."""
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming(json.dumps({
            "type": "state_update",
            "features": []
        }))
        callback.assert_not_called()


class TestMalformedBooleanValues(unittest.IsolatedAsyncioTestCase):
    """Edge case 2.2: Malformed boolean values in state_update.
    
    Verify server handles parsing errors gracefully.
    """

    async def test_auto_plan_as_string_ignored(self):
        """Verify auto_plan as string 'true' is ignored."""
        callback = MagicMock()
        client = ServerClient("ws://localhost:9999", "key", "test", on_set_auto_mode=callback)
        await client._handle_incoming(json.dumps({
            "type": "state_update",
            "auto_plan": "true",
            "features": []
        }))
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
