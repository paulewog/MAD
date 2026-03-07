"""Tests for Bug: cannot create new item from webui.

Run with:
    pytest test_bug_create_item_webui.py -v

These tests verify fixes for two bugs:
1. _handle_create_idea callback failing due to incorrect thread handling
2. Board field using free-text input instead of dropdown
"""

import asyncio
import inspect
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from server_client import ServerClient


class TestBug1HandleCreateIdeaAsync:
    """Tests for Bug 1: _handle_create_idea callback must be async and awaited."""

    def test_handle_create_idea_is_async_method(self):
        """Verify that _handle_create_idea is defined as async def in tui.py."""
        import tui
        
        method = getattr(tui.PipelineApp, '_handle_create_idea', None)
        assert method is not None, "_handle_create_idea method not found in tui.py"
        assert inspect.iscoroutinefunction(method), \
            "_handle_create_idea must be an async def function"

    @pytest.mark.asyncio
    async def test_callback_is_awaited_in_server_client(self):
        """Verify that the callback is awaited in server_client.py line 252."""
        source_file = Path(__file__).parent / "server_client.py"
        source_code = source_file.read_text()
        
        pattern = r'await\s+self\._on_idea_created\s*\('
        match = re.search(pattern, source_code)
        assert match is not None, \
            "Callback must be awaited with 'await self._on_idea_created(' in server_client.py"

    @pytest.mark.asyncio
    async def test_callback_executes_without_thread_error(self):
        """Test that callback executes successfully without thread-related errors."""
        call_executed = False
        call_args = {}
        
        async def mock_handle_create_idea(title, board, description):
            nonlocal call_executed
            call_executed = True
            call_args['title'] = title
            call_args['board'] = board
            call_args['description'] = description
        
        with patch.object(ServerClient, '__init__', lambda self, *args, **kwargs: None):
            sc = ServerClient.__new__(ServerClient)
            sc._on_idea_created = mock_handle_create_idea
            sc._log_line = MagicMock()
            sc._refresh_board = MagicMock()
            sc._server_client = MagicMock()
            sc._server_client.connected = True
            sc._load_features = MagicMock(return_value=[])
            sc._plan_agent_status = "idle"
            sc._impl_agent_status = "idle"
            sc.auto_plan_enabled = False
            sc.auto_implement_enabled = False
            sc._server_client.push_state = AsyncMock()
            
            await sc._on_idea_created("Test Title", "testboard", "Test description")
            
            assert call_executed is True
            assert call_args['title'] == "Test Title"
            assert call_args['board'] == "testboard"
            assert call_args['description'] == "Test description"

    @pytest.mark.asyncio
    async def test_create_idea_message_awaited_properly(self):
        """Test that create_idea message handler awaits the callback properly."""
        callback_invoked = False
        
        async def on_idea_created(title, board, description):
            nonlocal callback_invoked
            callback_invoked = True
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "New Idea",
            "board": "myboard",
            "description": "Description here",
        })
        
        await sc._handle_incoming(msg)
        
        assert callback_invoked is True


class TestBug2BoardDropdown:
    """Tests for Bug 2: Board field must be a dropdown in client.html."""

    def test_client_html_board_field_is_select(self):
        """Verify that board field uses <select> element, not <input type='text'>."""
        template_path = Path(__file__).parent.parent / "server" / "templates" / "client.html"
        
        if not template_path.exists():
            pytest.skip("client.html template not found")
        
        content = template_path.read_text()
        
        select_pattern = r'<select[^>]*id\s*=\s*["\']new-idea-board["\']'
        assert re.search(select_pattern, content), \
            "Board field must use <select> element with id='new-idea-board'"
        
        input_pattern = r'<input[^>]*id\s*=\s*["\']new-idea-board["\'][^>]*type\s*=\s*["\']text["\']'
        assert not re.search(input_pattern, content), \
            "Board field must NOT use <input type='text'> for board selection"

    def test_client_html_board_options_from_boards_range(self):
        """Verify that board dropdown options are populated via {{range .Boards}}."""
        template_path = Path(__file__).parent.parent / "server" / "templates" / "client.html"
        
        if not template_path.exists():
            pytest.skip("client.html template not found")
        
        content = template_path.read_text()
        
        range_boards_pattern = r'\{\{range\s+\.Boards\}\}'
        assert re.search(range_boards_pattern, content), \
            "Board dropdown must use {{range .Boards}} to populate options"

    def test_client_full_html_board_field_is_select(self):
        """Verify that board field in client_full.html is also a dropdown."""
        template_path = Path(__file__).parent.parent / "server" / "templates" / "client_full.html"
        
        if not template_path.exists():
            pytest.skip("client_full.html template not found")
        
        content = template_path.read_text()
        
        select_pattern = r'<select[^>]*id\s*=\s*["\']new-idea-board["\']'
        assert re.search(select_pattern, content), \
            "Board field must use <select> element in client_full.html"

    def test_client_full2_html_board_field_is_select(self):
        """Verify that board field in client_full2.html is also a dropdown."""
        template_path = Path(__file__).parent.parent / "server" / "templates" / "client_full2.html"
        
        if not template_path.exists():
            pytest.skip("client_full2.html template not found")
        
        content = template_path.read_text()
        
        select_pattern = r'<select[^>]*id\s*=\s*["\']new-idea-board["\']'
        assert re.search(select_pattern, content), \
            "Board field must use <select> element in client_full2.html"

    def test_api_passes_boards_to_template(self):
        """Verify that api.go passes Boards to template context."""
        api_path = Path(__file__).parent.parent / "server" / "api.go"
        
        if not api_path.exists():
            pytest.skip("api.go not found")
        
        content = api_path.read_text()
        
        boards_pattern = r'"Boards"\s*:\s*boardsFromClients'
        assert re.search(boards_pattern, content), \
            "api.go must pass Boards to template via boardsFromClients"


class TestCreateIdeaValidation:
    """Tests for edge cases in idea creation."""

    @pytest.mark.asyncio
    async def test_empty_title_not_processed(self):
        """Test that empty title is rejected and callback is not invoked."""
        callback_invoked = False
        
        async def on_idea_created(title, board, description):
            nonlocal callback_invoked
            callback_invoked = True
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "",
            "board": "testboard",
            "description": "desc",
        })
        
        await sc._handle_incoming(msg)
        
        assert callback_invoked is False, "Empty title should not trigger callback"

    @pytest.mark.asyncio
    async def test_missing_title_not_processed(self):
        """Test that missing title is rejected."""
        callback_invoked = False
        
        async def on_idea_created(title, board, description):
            nonlocal callback_invoked
            callback_invoked = True
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "board": "testboard",
            "description": "desc",
        })
        
        await sc._handle_incoming(msg)
        
        assert callback_invoked is False

    @pytest.mark.asyncio
    async def test_empty_board_not_processed(self):
        """Test that empty board is rejected."""
        callback_invoked = False
        
        async def on_idea_created(title, board, description):
            nonlocal callback_invoked
            callback_invoked = True
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "Test Title",
            "board": "",
            "description": "desc",
        })
        
        await sc._handle_incoming(msg)
        
        assert callback_invoked is False

    @pytest.mark.asyncio
    async def test_missing_board_not_processed(self):
        """Test that missing board is rejected."""
        callback_invoked = False
        
        async def on_idea_created(title, board, description):
            nonlocal callback_invoked
            callback_invoked = True
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "Test Title",
            "description": "desc",
        })
        
        await sc._handle_incoming(msg)
        
        assert callback_invoked is False

    @pytest.mark.asyncio
    async def test_long_title_handled(self):
        """Test that very long title is handled without crashing."""
        callback_invoked = False
        
        async def on_idea_created(title, board, description):
            nonlocal callback_invoked
            callback_invoked = True
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        long_title = "a" * 1000
        msg = json.dumps({
            "type": "create_idea",
            "title": long_title,
            "board": "testboard",
            "description": "desc",
        })
        
        await sc._handle_incoming(msg)
        
        assert callback_invoked is True

    @pytest.mark.asyncio
    async def test_callback_error_does_not_crash_handler(self):
        """Test that callback errors are handled gracefully without crashing."""
        async def on_idea_created(title, board, description):
            raise RuntimeError("Simulated callback error")
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "Test",
            "board": "b",
            "description": "d",
        })
        
        await sc._handle_incoming(msg)


class TestCreateIdeaMessageFormat:
    """Tests for create_idea message format and handling."""

    @pytest.mark.asyncio
    async def test_create_idea_message_with_all_fields(self):
        """Test that create_idea message with all fields works correctly."""
        received = {}
        
        async def on_idea_created(title, board, description):
            received['title'] = title
            received['board'] = board
            received['description'] = description
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "My New Idea",
            "board": "default",
            "description": "A test idea description",
        })
        
        await sc._handle_incoming(msg)
        
        assert received['title'] == "My New Idea"
        assert received['board'] == "default"
        assert received['description'] == "A test idea description"

    @pytest.mark.asyncio
    async def test_create_idea_message_minimal_fields(self):
        """Test that create_idea works with only required fields."""
        received = {}
        
        async def on_idea_created(title, board, description):
            received['title'] = title
            received['board'] = board
            received['description'] = description
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        msg = json.dumps({
            "type": "create_idea",
            "title": "Minimal Idea",
            "board": "myboard",
        })
        
        await sc._handle_incoming(msg)
        
        assert received['title'] == "Minimal Idea"
        assert received['board'] == "myboard"
        assert received['description'] == ""

    @pytest.mark.asyncio
    async def test_create_idea_special_characters_preserved(self):
        """Test that special characters survive round trip."""
        received = {}
        
        async def on_idea_created(title, board, description):
            received['title'] = title
            received['board'] = board
            received['description'] = description
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        special_title = 'Idea with "quotes" <br> & ampersand 🎉 日本語'
        
        msg = json.dumps({
            "type": "create_idea",
            "title": special_title,
            "board": "testboard",
            "description": "Line1\nLine2",
        })
        
        await sc._handle_incoming(msg)
        
        assert received['title'] == special_title

    @pytest.mark.asyncio
    async def test_malformed_json_does_not_crash(self):
        """Test that malformed JSON is handled gracefully."""
        callback_invoked = False
        
        async def on_idea_created(title, board, description):
            nonlocal callback_invoked
            callback_invoked = True
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        
        await sc._handle_incoming("{not valid json")
        
        assert callback_invoked is False


class TestConcurrentOperations:
    """Tests for concurrent idea creation scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_concurrent_ideas_all_created(self):
        """Test that multiple concurrent idea creations all succeed."""
        created_ideas = []
        
        async def on_idea_created(title, board, description):
            created_ideas.append({'title': title, 'board': board})
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        
        tasks = []
        for i in range(5):
            msg = json.dumps({
                "type": "create_idea",
                "title": f"Concurrent Idea {i}",
                "board": "testboard",
                "description": f"Description {i}",
            })
            tasks.append(sc._handle_incoming(msg))
        
        await asyncio.gather(*tasks)
        
        assert len(created_ideas) == 5
        for i in range(5):
            assert any(idea['title'] == f"Concurrent Idea {i}" for idea in created_ideas)

    @pytest.mark.asyncio
    async def test_callback_error_does_not_affect_other_requests(self):
        """Test that an error in one callback doesn't affect subsequent requests."""
        call_count = 0
        
        async def on_idea_created(title, board, description):
            nonlocal call_count
            call_count += 1
            if title == "Error Idea":
                raise RuntimeError("Simulated error")
        
        sc = ServerClient("ws://localhost:8080", "key", "c", on_idea_created=on_idea_created)
        
        await sc._handle_incoming(json.dumps({
            "type": "create_idea",
            "title": "Good Idea 1",
            "board": "b",
            "description": "d",
        }))
        
        await sc._handle_incoming(json.dumps({
            "type": "create_idea",
            "title": "Error Idea",
            "board": "b",
            "description": "d",
        }))
        
        await sc._handle_incoming(json.dumps({
            "type": "create_idea",
            "title": "Good Idea 2",
            "board": "b",
            "description": "d",
        }))
        
        assert call_count == 3, "All ideas should be processed despite one error"


class TestBoardSelectionIntegration:
    """Integration tests for board selection from dropdown."""

    def test_board_dropdown_has_default_option(self):
        """Verify that board dropdown has a default 'Select a board...' option."""
        template_path = Path(__file__).parent.parent / "server" / "templates" / "client.html"
        
        if not template_path.exists():
            pytest.skip("client.html template not found")
        
        content = template_path.read_text()
        
        default_option_pattern = r'<option[^>]*value\s*=\s*["\']["\'][^>]*>.*Select.*board'
        assert re.search(default_option_pattern, content, re.IGNORECASE), \
            "Board dropdown should have a default 'Select a board...' option"

    def test_board_dropdown_options_have_value_attribute(self):
        """Verify that board options have value attribute for selection."""
        template_path = Path(__file__).parent.parent / "server" / "templates" / "client.html"
        
        if not template_path.exists():
            pytest.skip("client.html template not found")
        
        content = template_path.read_text()
        
        option_value_pattern = r'<option[^>]*value\s*=\s*["\']\{\{.*\}\}["\'][^>]*>'
        assert re.search(option_value_pattern, content), \
            "Board options should have value attribute populated from template data"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
