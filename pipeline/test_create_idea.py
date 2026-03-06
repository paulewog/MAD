"""Tests for create_idea message handling in server_client.py.

Run with:
    pytest test_create_idea.py -v
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from server_client import ServerClient


class FakeWebSocket:
    """Fake websocket for testing."""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self._sent = []

    async def send(self, data):
        self._sent.append(data)

    async def recv(self):
        if self._responses:
            return self._responses.pop(0)
        await asyncio.sleep(3600)

    async def close(self):
        pass


class TestHandleIncomingCreateIdea:

    @pytest.mark.asyncio
    async def test_create_idea_dispatches_to_callback(self):
        """Test that create_idea message dispatches to on_idea_created callback."""
        received = {}

        def on_idea_created(title, board, description):
            received["title"] = title
            received["board"] = board
            received["description"] = description

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "My New Idea",
            "board": "default",
            "description": "A test idea description",
        })
        await sc._handle_incoming(msg)

        assert received["title"] == "My New Idea"
        assert received["board"] == "default"
        assert received["description"] == "A test idea description"

    @pytest.mark.asyncio
    async def test_create_idea_no_callback_no_crash(self):
        """Test that missing callback doesn't cause crash."""
        sc = ServerClient("ws://localhost:8080", "key", "c")
        msg = json.dumps({
            "type": "create_idea",
            "title": "Test",
            "board": "b",
            "description": "desc",
        })
        await sc._handle_incoming(msg)

    @pytest.mark.asyncio
    async def test_create_idea_empty_title_ignored(self):
        """Test that empty title doesn't call callback."""
        called = False

        def on_idea_created(title, board, description):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "",
            "board": "default",
            "description": "desc",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_create_idea_missing_title_ignored(self):
        """Test that missing title doesn't call callback."""
        called = False

        def on_idea_created(title, board, description):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "board": "default",
            "description": "desc",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_create_idea_empty_board_ignored(self):
        """Test that empty board doesn't call callback."""
        called = False

        def on_idea_created(title, board, description):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "Test",
            "board": "",
            "description": "desc",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_create_idea_missing_board_ignored(self):
        """Test that missing board doesn't call callback."""
        called = False

        def on_idea_created(title, board, description):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "Test",
            "description": "desc",
        })
        await sc._handle_incoming(msg)
        assert called is False

    @pytest.mark.asyncio
    async def test_create_idea_only_required_fields(self):
        """Test that callback receives empty string for missing optional fields."""
        received = {}

        def on_idea_created(title, board, description):
            received["title"] = title
            received["board"] = board
            received["description"] = description

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "Minimal Idea",
            "board": "myboard",
        })
        await sc._handle_incoming(msg)

        assert received["title"] == "Minimal Idea"
        assert received["board"] == "myboard"
        assert received["description"] == ""

    @pytest.mark.asyncio
    async def test_create_idea_special_characters_preserved(self):
        """Test that special characters survive round trip."""
        received = {}

        def on_idea_created(title, board, description):
            received["title"] = title
            received["board"] = board
            received["description"] = description

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        special_title = 'Idea with "quotes" <br> & ampersand 🎉 日本語'
        special_board = "board-with-dashes_and_underscores"
        special_desc = "Line1\nLine2\ttab"

        msg = json.dumps({
            "type": "create_idea",
            "title": special_title,
            "board": special_board,
            "description": special_desc,
        })
        await sc._handle_incoming(msg)

        assert received["title"] == special_title
        assert received["board"] == special_board
        assert received["description"] == special_desc

    @pytest.mark.asyncio
    async def test_create_idea_malformed_json_ignored(self):
        """Test that malformed JSON doesn't crash handler."""
        called = False

        def on_idea_created(title, board, description):
            nonlocal called
            called = True

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        await sc._handle_incoming("{not valid json")
        assert called is False

    @pytest.mark.asyncio
    async def test_create_idea_callback_error_does_not_crash(self):
        """Test that callback errors are handled gracefully."""

        def on_idea_created(title, board, description):
            raise ValueError("handler exploded")

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "Test",
            "board": "b",
            "description": "d",
        })
        await sc._handle_incoming(msg)

    @pytest.mark.asyncio
    async def test_create_idea_unicode_preserved(self):
        """Test that unicode characters are preserved."""
        received = {}

        def on_idea_created(title, board, description):
            received["title"] = title
            received["board"] = board
            received["description"] = description

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "café 🎉",
            "board": "日本語",
            "description": "emoji: 🎊",
        })
        await sc._handle_incoming(msg)

        assert received["title"] == "café 🎉"
        assert received["board"] == "日本語"
        assert received["description"] == "emoji: 🎊"


class TestCreateIdeaCallbackWiring:

    def test_on_idea_created_stored_on_init(self):
        """Test that on_idea_created callback is stored."""
        def cb(title, board, description):
            pass

        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=cb)
        assert sc._on_idea_created is cb

    def test_on_idea_created_default_none(self):
        """Test that callback defaults to None."""
        sc = ServerClient("ws://localhost:8080", "key", "c")
        assert sc._on_idea_created is None


class TestReconnectLoopCreateIdea:

    @pytest.mark.asyncio
    async def test_reconnect_loop_dispatches_create_idea(self):
        """Test that create_idea messages are dispatched when received via WebSocket."""
        received = {}

        def on_idea_created(title, board, description):
            received["title"] = title
            received["board"] = board
            received["description"] = description

        ack = json.dumps({"type": "ack"})
        create_msg = json.dumps({
            "type": "create_idea",
            "title": "Server Idea",
            "board": "default",
            "description": "Created from server",
        })
        fake_ws = FakeWebSocket(responses=[ack, create_msg])

        with patch("server_client.HAS_WEBSOCKETS", True), \
             patch("server_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)

            sc = ServerClient("ws://localhost:8080", "key", "c",
                              on_idea_created=on_idea_created)
            task = asyncio.create_task(sc.reconnect_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert received.get("title") == "Server Idea"
        assert received.get("board") == "default"
        assert received.get("description") == "Created from server"
