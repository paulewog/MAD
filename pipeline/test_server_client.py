"""Tests for server client/config integration (server_client.py + config.py server bits).

Run with:
    pytest test_server_client.py -v
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

from config import Config, ServerConfig
from server_client import ServerClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config(tmp_path):
    """Create a temp config file and return its path."""
    def _make(data):
        config_dir = tmp_path / ".mad"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(data))
        return config_file
    return _make


def _make_feature(title="Test Feature", stage="ideas", board="default",
                  created="2025-01-01", fid="f1", logs=None):
    """Create a mock feature object matching FeatureFile interface."""
    f = SimpleNamespace()
    f.title = title
    f.current_stage = stage
    f.board = board
    f.created = created
    f.id = fid
    f.description = ""
    f.questions = []
    f.plan = ""
    f.impl_spec = ""
    f.test_spec = ""
    f.impl_notes = ""
    f._data = {"pipeline_log": logs or [], "history": []}
    return f


class FakeWebSocket:
    """Fake websocket connection for unit tests."""

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
        # Block forever (simulate long-lived connection)
        await asyncio.sleep(3600)

    async def close(self):
        self._closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._fail_recv or not self._responses:
            raise StopAsyncIteration
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# Config.server property tests
# ---------------------------------------------------------------------------

class TestConfigServer:

    def test_server_disabled_by_default(self, tmp_config):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
        })
        cfg = Config(path=path)
        assert cfg.server is None

    def test_server_disabled_explicitly(self, tmp_config):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {"enabled": False, "url": "ws://localhost:8080", "api_key": "k", "client_id": "c"},
        })
        cfg = Config(path=path)
        assert cfg.server is None

    def test_server_enabled_full_config(self, tmp_config):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {
                "enabled": True,
                "url": "ws://localhost:8080",
                "api_key": "secret",
                "client_id": "my-machine",
                "push_interval_seconds": 5.0,
            },
        })
        cfg = Config(path=path)
        sc = cfg.server
        assert sc is not None
        assert isinstance(sc, ServerConfig)
        assert sc.enabled is True
        assert sc.url == "ws://localhost:8080"
        assert sc.api_key == "secret"
        assert sc.client_id == "my-machine"
        assert sc.push_interval_seconds == 5.0

    def test_server_enabled_default_push_interval(self, tmp_config):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {
                "enabled": True,
                "url": "ws://localhost:8080",
                "api_key": "k",
                "client_id": "c",
            },
        })
        cfg = Config(path=path)
        sc = cfg.server
        assert sc.push_interval_seconds == 10.0

    def test_server_enabled_missing_url_returns_none(self, tmp_config, caplog):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {"enabled": True, "api_key": "k", "client_id": "c"},
        })
        cfg = Config(path=path)
        with caplog.at_level(logging.WARNING):
            result = cfg.server
        assert result is None

    def test_server_enabled_missing_api_key_returns_none(self, tmp_config, caplog):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {"enabled": True, "url": "ws://localhost:8080", "client_id": "c"},
        })
        cfg = Config(path=path)
        with caplog.at_level(logging.WARNING):
            result = cfg.server
        assert result is None

    def test_server_enabled_missing_client_id_returns_none(self, tmp_config, caplog):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {"enabled": True, "url": "ws://localhost:8080", "api_key": "k"},
        })
        cfg = Config(path=path)
        with caplog.at_level(logging.WARNING):
            result = cfg.server
        assert result is None

    def test_server_enabled_empty_api_key_returns_none(self, tmp_config, caplog):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {"enabled": True, "url": "ws://localhost:8080", "api_key": "", "client_id": "c"},
        })
        cfg = Config(path=path)
        with caplog.at_level(logging.WARNING):
            result = cfg.server
        assert result is None

    def test_partial_server_block_no_crash(self, tmp_config):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {"enabled": True},
        })
        cfg = Config(path=path)
        # Should not crash, should return None
        assert cfg.server is None

    def test_server_block_empty_dict(self, tmp_config):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
            "server": {},
        })
        cfg = Config(path=path)
        assert cfg.server is None

    def test_no_server_block_at_all(self, tmp_config):
        path = tmp_config({
            "current_agent": "claude",
            "agents": {"claude": {"command": "claude", "headless_flag": "-p"}},
            "boards": ["default"],
        })
        cfg = Config(path=path)
        assert cfg.server is None


# ---------------------------------------------------------------------------
# ServerClient unit tests
# ---------------------------------------------------------------------------

class TestServerClientInit:

    def test_initial_state(self):
        sc = ServerClient("ws://localhost:8080", "key", "my-client")
        assert sc.connected is False
        assert sc._url == "ws://localhost:8080"
        assert sc._api_key == "key"
        assert sc._client_id == "my-client"
        assert sc._backoff == 1.0
        assert sc._max_backoff == 30.0


class TestServerClientConnect:

    @pytest.mark.asyncio
    async def test_connect_success(self):
        ack_response = json.dumps({"type": "ack"})
        fake_ws = FakeWebSocket(responses=[ack_response])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            sc = ServerClient("ws://localhost:8080", "key", "client-1")
            result = await sc.connect()

        assert result is True
        assert sc.connected is True
        assert sc._backoff == 1.0  # reset on success
        # Verify register message was sent
        sent = json.loads(fake_ws._sent[0])
        assert sent["type"] == "register"
        assert sent["client_id"] == "client-1"
        assert sent["api_key"] == "key"

    @pytest.mark.asyncio
    async def test_connect_ws_url_appended(self):
        ack_response = json.dumps({"type": "ack"})
        fake_ws = FakeWebSocket(responses=[ack_response])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            sc = ServerClient("http://localhost:8080", "key", "c")
            await sc.connect()

        call_args = mock_ws.connect.call_args
        assert call_args[0][0] == "http://localhost:8080/ws"

    @pytest.mark.asyncio
    async def test_connect_url_trailing_slash_handled(self):
        ack_response = json.dumps({"type": "ack"})
        fake_ws = FakeWebSocket(responses=[ack_response])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            sc = ServerClient("http://localhost:8080/", "key", "c")
            await sc.connect()

        call_args = mock_ws.connect.call_args
        assert call_args[0][0] == "http://localhost:8080/ws"

    @pytest.mark.asyncio
    async def test_connect_server_rejects(self):
        err_response = json.dumps({"type": "error", "message": "unauthorized"})
        fake_ws = FakeWebSocket(responses=[err_response])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            sc = ServerClient("ws://localhost:8080", "wrong", "c")
            result = await sc.connect()

        assert result is False
        assert sc.connected is False
        assert fake_ws._closed is True

    @pytest.mark.asyncio
    async def test_connect_network_failure(self):
        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(side_effect=ConnectionRefusedError("refused"))
            sc = ServerClient("ws://localhost:9999", "key", "c")
            result = await sc.connect()

        assert result is False
        assert sc.connected is False

    @pytest.mark.asyncio
    async def test_connect_no_websockets_library(self):
        with patch("server_client.HAS_WEBSOCKETS", False):
            sc = ServerClient("ws://localhost:8080", "key", "c")
            result = await sc.connect()

        assert result is False

    @pytest.mark.asyncio
    async def test_connect_timeout_on_ack(self):
        # Simulate ack never arriving
        fake_ws = FakeWebSocket(responses=[])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            sc = ServerClient("ws://localhost:8080", "key", "c")
            # This should timeout waiting for ack and return False
            result = await sc.connect()

        assert result is False
        assert sc.connected is False


# ---------------------------------------------------------------------------
# ServerClient.push_state tests
# ---------------------------------------------------------------------------

class TestServerClientPushState:

    @pytest.mark.asyncio
    async def test_push_state_when_connected(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c1")
        sc._ws = fake_ws
        sc._connected = True

        features = [_make_feature("F1", "ideas", "board1", "2025-01-01", "f1")]
        await sc.push_state(features)

        assert len(fake_ws._sent) == 1
        payload = json.loads(fake_ws._sent[0])
        assert payload["type"] == "state_update"
        assert payload["client_id"] == "c1"
        assert "timestamp" in payload
        assert len(payload["features"]) == 1
        assert payload["features"][0]["title"] == "F1"
        assert payload["features"][0]["stage"] == "ideas"
        assert payload["features"][0]["board"] == "board1"
        assert payload["features"][0]["id"] == "f1"

    @pytest.mark.asyncio
    async def test_push_state_not_connected_silently_skips(self):
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._connected = False
        sc._ws = None

        # Should not raise
        await sc.push_state([_make_feature()])

    @pytest.mark.asyncio
    async def test_push_state_send_failure_marks_disconnected(self):
        fake_ws = FakeWebSocket(fail_send=True)
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        await sc.push_state([_make_feature()])

        assert sc._connected is False

    @pytest.mark.asyncio
    async def test_push_state_empty_features(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        await sc.push_state([])

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"] == []
        assert payload["logs"] == []

    @pytest.mark.asyncio
    async def test_push_state_includes_logs(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature(logs=[
            {"ts": "2025-01-01T00:00:00Z", "phase": "planning", "output": "did stuff"},
        ])
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert len(payload["logs"]) == 1
        assert payload["logs"][0]["phase"] == "planning"
        assert payload["logs"][0]["client_id"] == "c"

    @pytest.mark.asyncio
    async def test_push_state_truncates_log_output(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        long_output = "x" * 1000
        feature = _make_feature(logs=[
            {"ts": "t", "phase": "p", "output": long_output},
        ])
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert len(payload["logs"][0]["output"]) == 500

    @pytest.mark.asyncio
    async def test_push_state_limits_logs_per_feature(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        logs = [{"ts": f"t{i}", "phase": "p", "output": f"log{i}"} for i in range(20)]
        feature = _make_feature(logs=logs)
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        # Only last 10 per feature
        assert len(payload["logs"]) == 10
        assert payload["logs"][0]["output"] == "log10"

    @pytest.mark.asyncio
    async def test_push_state_timestamp_is_utc_iso(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        await sc.push_state([_make_feature()])

        payload = json.loads(fake_ws._sent[0])
        ts = payload["timestamp"]
        # Should be parseable as ISO format and contain timezone info
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# ServerClient.disconnect tests
# ---------------------------------------------------------------------------

class TestServerClientDisconnect:

    @pytest.mark.asyncio
    async def test_disconnect_sends_message_and_closes(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c1")
        sc._ws = fake_ws
        sc._connected = True

        await sc.disconnect()

        assert sc._connected is False
        assert sc._ws is None
        assert fake_ws._closed is True
        # Check disconnect message was sent
        sent = json.loads(fake_ws._sent[0])
        assert sent["type"] == "disconnect"
        assert sent["client_id"] == "c1"

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = None
        sc._connected = False
        # Should not raise
        await sc.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_with_send_error(self):
        fake_ws = FakeWebSocket(fail_send=True)
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        # Should not raise even if send fails
        await sc.disconnect()
        assert sc._connected is False
        assert sc._ws is None


# ---------------------------------------------------------------------------
# ServerClient.reconnect_loop tests
# ---------------------------------------------------------------------------

class TestServerClientReconnectLoop:

    @pytest.mark.asyncio
    async def test_reconnect_loop_no_websockets(self):
        with patch("server_client.HAS_WEBSOCKETS", False):
            sc = ServerClient("ws://localhost:8080", "key", "c")
            # Should return immediately
            await sc.reconnect_loop()

    @pytest.mark.asyncio
    async def test_reconnect_loop_cancelled_cleanly(self):
        fake_ws = FakeWebSocket(responses=[json.dumps({"type": "ack"})])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)

            sc = ServerClient("ws://localhost:8080", "key", "c")
            task = asyncio.create_task(sc.reconnect_loop())
            await asyncio.sleep(0.1)
            task.cancel()

            # reconnect_loop catches CancelledError, calls disconnect, and returns cleanly
            try:
                await task
            except asyncio.CancelledError:
                pass
            # Verify it disconnected cleanly
            assert sc._connected is False
            assert sc._ws is None

    @pytest.mark.asyncio
    async def test_reconnect_backoff_increases(self):
        call_count = 0
        backoffs = []

        original_sleep = asyncio.sleep

        async def mock_connect(*args, **kwargs):
            raise ConnectionRefusedError("refused")

        async def mock_sleep(duration):
            nonlocal call_count
            backoffs.append(duration)
            call_count += 1
            if call_count >= 5:
                raise asyncio.CancelledError()
            await original_sleep(0)

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws, \
             patch("asyncio.sleep", side_effect=mock_sleep):
            mock_ws.connect = AsyncMock(side_effect=ConnectionRefusedError("refused"))

            sc = ServerClient("ws://localhost:8080", "key", "c")
            try:
                await sc.reconnect_loop()
            except asyncio.CancelledError:
                pass

        # Backoff should increase: 1, 2, 4, 8, 16
        assert len(backoffs) >= 4
        assert backoffs[0] == 1.0
        assert backoffs[1] == 2.0
        assert backoffs[2] == 4.0
        assert backoffs[3] == 8.0

    @pytest.mark.asyncio
    async def test_reconnect_backoff_caps_at_30(self):
        call_count = 0
        backoffs = []

        async def mock_sleep(duration):
            nonlocal call_count
            backoffs.append(duration)
            call_count += 1
            if call_count >= 8:
                raise asyncio.CancelledError()

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws, \
             patch("asyncio.sleep", side_effect=mock_sleep):
            mock_ws.connect = AsyncMock(side_effect=ConnectionRefusedError("refused"))

            sc = ServerClient("ws://localhost:8080", "key", "c")
            try:
                await sc.reconnect_loop()
            except asyncio.CancelledError:
                pass

        # After enough failures, backoff should cap at 30
        assert any(b == 30.0 for b in backoffs), f"expected 30.0 cap in {backoffs}"

    @pytest.mark.asyncio
    async def test_reconnect_resets_backoff_on_success(self):
        call_count = 0

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        ack = json.dumps({"type": "ack"})
        fake_ws = FakeWebSocket(responses=[ack])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws, \
             patch("asyncio.sleep", side_effect=mock_sleep):
            mock_ws.connect = AsyncMock(return_value=fake_ws)

            sc = ServerClient("ws://localhost:8080", "key", "c")
            sc._backoff = 16.0  # Simulate previous failures

            try:
                await sc.reconnect_loop()
            except asyncio.CancelledError:
                pass

        # After successful connect, backoff should reset to 1.0
        assert sc._backoff <= 2.0  # 1.0 * 2 after one sleep cycle


# ---------------------------------------------------------------------------
# HAS_WEBSOCKETS import guard
# ---------------------------------------------------------------------------

class TestWebsocketsImportGuard:

    def test_has_websockets_flag_exists(self):
        import server_client
        assert hasattr(server_client, "HAS_WEBSOCKETS")

    @pytest.mark.asyncio
    async def test_no_import_error_when_disabled(self):
        """When HAS_WEBSOCKETS is False, connect should return False without crashing."""
        with patch("server_client.HAS_WEBSOCKETS", False):
            sc = ServerClient("ws://localhost:8080", "key", "c")
            assert await sc.connect() is False
            assert sc.connected is False


# ---------------------------------------------------------------------------
# ServerConfig dataclass tests
# ---------------------------------------------------------------------------

class TestServerConfigDataclass:

    def test_defaults(self):
        sc = ServerConfig(enabled=True, url="ws://x", api_key="k", client_id="c")
        assert sc.push_interval_seconds == 10.0

    def test_custom_interval(self):
        sc = ServerConfig(enabled=True, url="ws://x", api_key="k", client_id="c",
                          push_interval_seconds=2.5)
        assert sc.push_interval_seconds == 2.5

    def test_fields(self):
        sc = ServerConfig(enabled=True, url="ws://x", api_key="k", client_id="c")
        assert sc.enabled is True
        assert sc.url == "ws://x"
        assert sc.api_key == "k"
        assert sc.client_id == "c"


# ---------------------------------------------------------------------------
# ServerClient._handle_incoming tests
# ---------------------------------------------------------------------------

class TestHandleIncoming:

    @pytest.mark.asyncio
    async def test_answer_questions_dispatches_to_callback(self):
        received = {}

        async def on_answers(feature_id, answers):
            received["feature_id"] = feature_id
            received["answers"] = answers

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=on_answers)
        msg = json.dumps({
            "type": "answer_questions",
            "feature_id": "feat-1",
            "answers": [
                {"question": "What color?", "answer": "blue"},
                {"question": "What size?", "answer": "large"},
            ],
        })
        await sc._handle_incoming(msg)

        assert received["feature_id"] == "feat-1"
        assert len(received["answers"]) == 2
        assert received["answers"][0]["question"] == "What color?"
        assert received["answers"][0]["answer"] == "blue"
        assert received["answers"][1]["answer"] == "large"

    @pytest.mark.asyncio
    async def test_answer_questions_no_callback_no_crash(self):
        sc = ServerClient("ws://localhost:8080", "key", "c")
        msg = json.dumps({
            "type": "answer_questions",
            "feature_id": "f1",
            "answers": [{"question": "q", "answer": "a"}],
        })
        # Should not raise
        await sc._handle_incoming(msg)

    @pytest.mark.asyncio
    async def test_answer_questions_empty_feature_id_ignored(self):
        called = False

        async def on_answers(feature_id, answers):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=on_answers)
        msg = json.dumps({
            "type": "answer_questions",
            "feature_id": "",
            "answers": [{"question": "q", "answer": "a"}],
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_answer_questions_missing_feature_id_ignored(self):
        called = False

        async def on_answers(feature_id, answers):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=on_answers)
        msg = json.dumps({
            "type": "answer_questions",
            "answers": [{"question": "q", "answer": "a"}],
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_answer_questions_empty_answers_array(self):
        received = {}

        async def on_answers(feature_id, answers):
            received["answers"] = answers

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=on_answers)
        msg = json.dumps({
            "type": "answer_questions",
            "feature_id": "f1",
            "answers": [],
        })
        await sc._handle_incoming(msg)
        assert received["answers"] == []

    @pytest.mark.asyncio
    async def test_handle_incoming_malformed_json(self):
        called = False

        async def on_answers(feature_id, answers):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=on_answers)
        # Should not raise, should not call callback
        await sc._handle_incoming("{not valid json")
        assert called is False

    @pytest.mark.asyncio
    async def test_handle_incoming_unknown_type_ignored(self):
        called = False

        async def on_answers(feature_id, answers):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=on_answers)
        msg = json.dumps({"type": "some_unknown_type", "data": "hello"})
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_handle_incoming_callback_error_does_not_crash(self):
        async def on_answers(feature_id, answers):
            raise ValueError("handler exploded")

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=on_answers)
        msg = json.dumps({
            "type": "answer_questions",
            "feature_id": "f1",
            "answers": [{"question": "q", "answer": "a"}],
        })
        # Should not raise even though callback raises
        await sc._handle_incoming(msg)

    @pytest.mark.asyncio
    async def test_answer_questions_special_characters_preserved(self):
        received = {}

        async def on_answers(feature_id, answers):
            received["answers"] = answers

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=on_answers)
        special = "line1\nline2\ttab \"quotes\" <html> & 🎉 こんにちは"
        msg = json.dumps({
            "type": "answer_questions",
            "feature_id": "f1",
            "answers": [{"question": "q", "answer": special}],
        })
        await sc._handle_incoming(msg)
        assert received["answers"][0]["answer"] == special


# ---------------------------------------------------------------------------
# ServerClient reconnect_loop dispatches incoming messages
# ---------------------------------------------------------------------------

class TestReconnectLoopDispatch:

    @pytest.mark.asyncio
    async def test_reconnect_loop_dispatches_answer_questions(self):
        """When connected, incoming answer_questions messages are dispatched."""
        received = {}

        async def on_answers(feature_id, answers):
            received["feature_id"] = feature_id
            received["answers"] = answers

        ack = json.dumps({"type": "ack"})
        answer_msg = json.dumps({
            "type": "answer_questions",
            "feature_id": "f1",
            "answers": [{"question": "q", "answer": "a"}],
        })
        fake_ws = FakeWebSocket(responses=[ack, answer_msg])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)

            sc = ServerClient("ws://localhost:8080", "key", "c",
                              on_answers_received=on_answers)
            task = asyncio.create_task(sc.reconnect_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert received.get("feature_id") == "f1"
        assert len(received.get("answers", [])) == 1


# ---------------------------------------------------------------------------
# ServerClient.push_state includes questions field
# ---------------------------------------------------------------------------

class TestPushStateQuestions:

    @pytest.mark.asyncio
    async def test_push_state_includes_questions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "requested-input", "board1", "2025-01-01", "f1")
        feature.questions = [
            {"question": "What color?", "answer": ""},
            {"question": "What size?", "answer": "large"},
        ]
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        qs = payload["features"][0]["questions"]
        assert len(qs) == 2
        assert qs[0]["question"] == "What color?"
        assert qs[0]["answer"] == ""
        assert qs[1]["answer"] == "large"

    @pytest.mark.asyncio
    async def test_push_state_empty_questions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "ideas", "board1", "2025-01-01", "f1")
        feature.questions = []
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"][0]["questions"] == []

    @pytest.mark.asyncio
    async def test_push_state_includes_history(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "plan-inbox", "board1", "2025-01-01", "f1")
        feature._data["history"] = [
            {"ts": "2025-01-01T00:00:00Z", "stage": "PLAN-INBOX", "note": "Questions answered via web UI"},
        ]
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        hist = payload["features"][0]["history"]
        assert len(hist) == 1
        assert hist[0]["note"] == "Questions answered via web UI"


# ---------------------------------------------------------------------------
# ServerClient on_answers_received callback wiring
# ---------------------------------------------------------------------------

class TestServerClientCallbackWiring:

    def test_callback_stored_on_init(self):
        async def cb(fid, ans):
            pass

        sc = ServerClient("ws://localhost:8080", "key", "c", on_answers_received=cb)
        assert sc._on_answers_received is cb

    def test_callback_default_none(self):
        sc = ServerClient("ws://localhost:8080", "key", "c")
        assert sc._on_answers_received is None


# ---------------------------------------------------------------------------
# STAGE_ACTIONS config tests
# ---------------------------------------------------------------------------

class TestStageActions:

    def test_stage_actions_exists(self):
        from state import STAGE_ACTIONS
        assert isinstance(STAGE_ACTIONS, dict)
        assert "plan" in STAGE_ACTIONS
        assert "implement" in STAGE_ACTIONS

    def test_stage_actions_plan_stages(self):
        from state import STAGE_ACTIONS
        assert STAGE_ACTIONS["plan"] == ["plan-inbox", "reviewing-plan", "requested-input", "approved"]

    def test_stage_actions_implement_stages(self):
        from state import STAGE_ACTIONS
        assert STAGE_ACTIONS["implement"] == ["approved", "spec-writing"]

    def test_approved_in_both_actions(self):
        """approved stage should allow both plan and implement."""
        from state import STAGE_ACTIONS
        assert "approved" in STAGE_ACTIONS["plan"]
        assert "approved" in STAGE_ACTIONS["implement"]

    def test_server_client_imports_stage_actions(self):
        import server_client
        assert hasattr(server_client, "STAGE_ACTIONS")


# ---------------------------------------------------------------------------
# push_state available_actions tests
# ---------------------------------------------------------------------------

class TestPushStateAvailableActions:

    @pytest.mark.asyncio
    async def test_feature_in_plan_inbox_has_plan_action(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "plan-inbox", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert "plan" in payload["features"][0]["available_actions"]

    @pytest.mark.asyncio
    async def test_feature_in_approved_has_both_actions(self):
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
    async def test_feature_in_done_has_no_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "done", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"][0]["available_actions"] == []

    @pytest.mark.asyncio
    async def test_feature_in_ideas_has_no_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "ideas", "board1", "2025-01-01", "f1")
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"][0]["available_actions"] == []

    @pytest.mark.asyncio
    async def test_feature_in_spec_writing_has_implement_only(self):
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

    @pytest.mark.asyncio
    async def test_plan_agent_running_excludes_plan_action(self):
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
    async def test_impl_agent_running_excludes_implement_action(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        impl_status = SimpleNamespace(running=True, phase="implementing", feature_slug="f1", agent="claude")
        feature = _make_feature("F1", "approved", "board1", "2025-01-01", "f1")
        await sc.push_state([feature], impl_status=impl_status)

        payload = json.loads(fake_ws._sent[0])
        actions = payload["features"][0]["available_actions"]
        assert "plan" in actions
        assert "implement" not in actions

    @pytest.mark.asyncio
    async def test_both_agents_running_no_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        plan_status = SimpleNamespace(running=True, phase="planning", feature_slug="f1", agent="claude")
        impl_status = SimpleNamespace(running=True, phase="implementing", feature_slug="f2", agent="claude")
        feature = _make_feature("F1", "approved", "board1", "2025-01-01", "f1")
        await sc.push_state([feature], plan_status=plan_status, impl_status=impl_status)

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"][0]["available_actions"] == []

    @pytest.mark.asyncio
    async def test_agent_not_running_does_not_exclude(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        plan_status = SimpleNamespace(running=False, phase="", feature_slug="", agent="")
        feature = _make_feature("F1", "plan-inbox", "board1", "2025-01-01", "f1")
        await sc.push_state([feature], plan_status=plan_status)

        payload = json.loads(fake_ws._sent[0])
        assert "plan" in payload["features"][0]["available_actions"]

    @pytest.mark.asyncio
    async def test_multiple_features_different_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        f1 = _make_feature("F1", "plan-inbox", "b", "2025-01-01", "f1")
        f2 = _make_feature("F2", "spec-writing", "b", "2025-01-01", "f2")
        f3 = _make_feature("F3", "done", "b", "2025-01-01", "f3")
        await sc.push_state([f1, f2, f3])

        payload = json.loads(fake_ws._sent[0])
        assert "plan" in payload["features"][0]["available_actions"]
        assert "implement" in payload["features"][1]["available_actions"]
        assert payload["features"][2]["available_actions"] == []


# ---------------------------------------------------------------------------
# push_state stage_actions field tests
# ---------------------------------------------------------------------------

class TestPushStateStageActions:

    @pytest.mark.asyncio
    async def test_push_state_includes_stage_actions(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        await sc.push_state([_make_feature()])

        payload = json.loads(fake_ws._sent[0])
        assert "stage_actions" in payload
        from state import STAGE_ACTIONS
        assert payload["stage_actions"] == STAGE_ACTIONS

    @pytest.mark.asyncio
    async def test_push_state_includes_agent_dicts(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        plan_status = SimpleNamespace(running=True, phase="planning", feature_slug="my-feat", agent="claude")
        await sc.push_state([_make_feature()], plan_status=plan_status)

        payload = json.loads(fake_ws._sent[0])
        assert payload["plan_agent"]["running"] is True
        assert payload["plan_agent"]["feature"] == "my-feat"
        assert payload["impl_agent"]["running"] is False

    @pytest.mark.asyncio
    async def test_push_state_none_agents_default_dict(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        await sc.push_state([_make_feature()])

        payload = json.loads(fake_ws._sent[0])
        assert payload["plan_agent"] == {"running": False, "phase": "", "feature": "", "agent": ""}
        assert payload["impl_agent"] == {"running": False, "phase": "", "feature": "", "agent": ""}

    @pytest.mark.asyncio
    async def test_push_state_includes_auto_mode_flags(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        await sc.push_state([_make_feature()], auto_plan_enabled=True, auto_impl_enabled=False)

        payload = json.loads(fake_ws._sent[0])
        assert payload["auto_plan"] is True
        assert payload["auto_impl"] is False


# ---------------------------------------------------------------------------
# _handle_incoming start_agent tests
# ---------------------------------------------------------------------------

class TestHandleIncomingStartAgent:

    @pytest.mark.asyncio
    async def test_start_agent_plan_dispatches_callback(self):
        received = {}

        def on_start(feature_id, action):
            received["feature_id"] = feature_id
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "abc123",
            "action": "plan",
        })
        await sc._handle_incoming(msg)

        assert received["feature_id"] == "abc123"
        assert received["action"] == "plan"

    @pytest.mark.asyncio
    async def test_start_agent_implement_dispatches_callback(self):
        received = {}

        def on_start(feature_id, action):
            received["feature_id"] = feature_id
            received["action"] = action

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "xyz789",
            "action": "implement",
        })
        await sc._handle_incoming(msg)

        assert received["feature_id"] == "xyz789"
        assert received["action"] == "implement"

    @pytest.mark.asyncio
    async def test_start_agent_no_callback_no_crash(self):
        sc = ServerClient("ws://localhost:8080", "key", "c")
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "plan",
        })
        # Should not raise
        await sc._handle_incoming(msg)

    @pytest.mark.asyncio
    async def test_start_agent_unknown_action_ignored(self):
        called = False

        def on_start(feature_id, action):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "migrate",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_start_agent_empty_feature_id_ignored(self):
        called = False

        def on_start(feature_id, action):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "",
            "action": "plan",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_start_agent_missing_fields_ignored(self):
        called = False

        def on_start(feature_id, action):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({"type": "start_agent"})
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_start_agent_null_fields_ignored(self):
        called = False

        def on_start(feature_id, action):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": None,
            "action": None,
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_start_agent_callback_error_does_not_crash(self):
        def on_start(feature_id, action):
            raise ValueError("handler exploded")

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "plan",
        })
        # Should not raise even though callback raises
        await sc._handle_incoming(msg)

    @pytest.mark.asyncio
    async def test_start_agent_callback_called_exactly_once(self):
        call_count = 0

        def on_start(feature_id, action):
            nonlocal call_count
            call_count += 1

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=on_start)
        msg = json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "plan",
        })
        await sc._handle_incoming(msg)
        assert call_count == 1


# ---------------------------------------------------------------------------
# on_start_agent callback wiring tests
# ---------------------------------------------------------------------------

class TestStartAgentCallbackWiring:

    def test_on_start_agent_stored_on_init(self):
        def cb(fid, action):
            pass

        sc = ServerClient("ws://localhost:8080", "key", "c", on_start_agent=cb)
        assert sc._on_start_agent is cb

    def test_on_start_agent_default_none(self):
        sc = ServerClient("ws://localhost:8080", "key", "c")
        assert sc._on_start_agent is None


# ---------------------------------------------------------------------------
# reconnect_loop dispatches start_agent messages
# ---------------------------------------------------------------------------

class TestReconnectLoopStartAgent:

    @pytest.mark.asyncio
    async def test_reconnect_loop_dispatches_start_agent(self):
        received = {}

        def on_start(feature_id, action):
            received["feature_id"] = feature_id
            received["action"] = action

        ack = json.dumps({"type": "ack"})
        start_msg = json.dumps({
            "type": "start_agent",
            "feature_id": "f1",
            "action": "implement",
        })
        fake_ws = FakeWebSocket(responses=[ack, start_msg])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)

            sc = ServerClient("ws://localhost:8080", "key", "c",
                              on_start_agent=on_start)
            task = asyncio.create_task(sc.reconnect_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert received.get("feature_id") == "f1"
        assert received.get("action") == "implement"


# ---------------------------------------------------------------------------
# push_state new fields (plan, impl_spec, test_spec, impl_notes) tests
# ---------------------------------------------------------------------------

class TestPushStateNewFields:

    @pytest.mark.asyncio
    async def test_push_state_includes_plan_field(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "plan-inbox", "board1", "2025-01-01", "f1")
        feature.plan = "This is a test plan"
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"][0]["plan"] == "This is a test plan"

    @pytest.mark.asyncio
    async def test_push_state_includes_impl_spec_field(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "spec-writing", "board1", "2025-01-01", "f1")
        feature.impl_spec = "Implementation spec content"
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"][0]["impl_spec"] == "Implementation spec content"

    @pytest.mark.asyncio
    async def test_push_state_includes_test_spec_field(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "spec-writing", "board1", "2025-01-01", "f1")
        feature.test_spec = "Test spec content"
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"][0]["test_spec"] == "Test spec content"

    @pytest.mark.asyncio
    async def test_push_state_includes_impl_notes_field(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "implementing", "board1", "2025-01-01", "f1")
        feature.impl_notes = "Implementation notes content"
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert payload["features"][0]["impl_notes"] == "Implementation notes content"

    @pytest.mark.asyncio
    async def test_push_state_all_new_fields_together(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "spec-writing", "board1", "2025-01-01", "f1")
        feature.plan = "Test plan"
        feature.impl_spec = "Test impl spec"
        feature.test_spec = "Test test spec"
        feature.impl_notes = "Test impl notes"
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        f = payload["features"][0]
        assert f["plan"] == "Test plan"
        assert f["impl_spec"] == "Test impl spec"
        assert f["test_spec"] == "Test test spec"
        assert f["impl_notes"] == "Test impl notes"

    @pytest.mark.asyncio
    async def test_push_state_empty_fields_not_in_json(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "ideas", "board1", "2025-01-01", "f1")
        feature.plan = ""
        feature.impl_spec = ""
        feature.test_spec = ""
        feature.impl_notes = ""
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        f = payload["features"][0]
        assert "plan" not in f or f.get("plan") == ""
        assert "impl_spec" not in f or f.get("impl_spec") == ""
        assert "test_spec" not in f or f.get("test_spec") == ""
        assert "impl_notes" not in f or f.get("impl_notes") == ""

    @pytest.mark.asyncio
    async def test_push_state_long_content_truncated(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        long_plan = "x" * 2000
        feature = _make_feature("F1", "plan-inbox", "board1", "2025-01-01", "f1")
        feature.plan = long_plan
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        assert len(payload["features"][0]["plan"]) == 1000

    @pytest.mark.asyncio
    async def test_push_state_special_characters_preserved(self):
        fake_ws = FakeWebSocket()
        sc = ServerClient("ws://localhost:8080", "key", "c")
        sc._ws = fake_ws
        sc._connected = True

        feature = _make_feature("F1", "plan-inbox", "board1", "2025-01-01", "f1")
        feature.plan = "Plan with <html> & \"quotes\" and emojis 🎉"
        feature.impl_spec = "Impl with <script>alert('xss')</script>"
        feature.test_spec = "Test with newlines\n\tand tabs"
        feature.impl_notes = "Notes with unicode: 日本語"
        await sc.push_state([feature])

        payload = json.loads(fake_ws._sent[0])
        f = payload["features"][0]
        assert f["plan"] == "Plan with <html> & \"quotes\" and emojis 🎉"
        assert f["impl_spec"] == "Impl with <script>alert('xss')</script>"
        assert f["test_spec"] == "Test with newlines\n\tand tabs"
        assert f["impl_notes"] == "Notes with unicode: 日本語"
