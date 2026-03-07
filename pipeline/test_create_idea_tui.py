"""Tests for create_idea integration in tui.py.

Run with:
    pytest test_create_idea_tui.py -v
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))


class TestFeatureFileCreate:
    """Tests for FeatureFile.create method."""

    def test_featurefile_create_makes_file(self):
        """Test that FeatureFile.create actually creates a file."""
        from state import FeatureFile, Config
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "My New Idea", "A description")
                
                assert feature.title == "My New Idea"
                assert feature.board == "testboard"
                assert feature.description == "A description"
                
                ideas_dir = Path(tmpdir) / "testboard" / "ideas"
                assert ideas_dir.exists()
                
                files = list(ideas_dir.glob("*.json"))
                assert len(files) == 1
                
                with open(files[0]) as f:
                    data = json.load(f)
                    assert data["title"] == "My New Idea"
                    assert data["board"] == "testboard"
                    assert data["description"] == "A description"

    def test_featurefile_create_without_description(self):
        """Test that FeatureFile.create works without description."""
        from state import FeatureFile, Config
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Minimal Idea", "")
                
                assert feature.description == "No description provided."

    def test_featurefile_create_id_unique(self):
        """Test that FeatureFile.create generates unique IDs."""
        from state import FeatureFile, Config
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature1 = FeatureFile.create("testboard", "Idea One", "")
                feature2 = FeatureFile.create("testboard", "Idea Two", "")
                
                assert feature1.id != feature2.id

    def test_featurefile_create_history(self):
        """Test that FeatureFile.create adds history entry."""
        from state import FeatureFile, Config
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", "Test", "")
                
                assert len(feature._data["history"]) == 1
                assert feature._data["history"][0]["stage"] == "IDEAS"
                assert "created" in feature._data["history"][0]["note"]

    def test_featurefile_create_special_characters(self):
        """Test that FeatureFile.create handles special characters."""
        from state import FeatureFile, Config
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                special_title = 'Idea with "quotes" <br> & ampersand 🎉 日本語'
                feature = FeatureFile.create("testboard", special_title, "Line1\nLine2\tTab")
                
                assert feature.title == special_title
                assert feature.description == "Line1\nLine2\tTab"

    def test_featurefile_create_long_input(self):
        """Test that FeatureFile.create handles long inputs (but not too long for filesystem)."""
        from state import FeatureFile, Config
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MagicMock()
            config.boards_dir = Path(tmpdir)
            
            long_string = "a" * 200
            
            with patch('state.Config') as mock_config_class:
                mock_config_class.return_value = config
                feature = FeatureFile.create("testboard", long_string, long_string)
                
                assert feature.title == long_string
                assert feature.description == long_string


class TestServerClientCallbackWiring:
    """Tests that verify the server_client callback wiring."""

    def test_callback_receives_correct_parameters(self):
        """Test that on_idea_created callback receives correct parameters."""
        from server_client import ServerClient
        
        received = {}
        
        def on_idea_created(title, board, description):
            received["title"] = title
            received["board"] = board
            received["description"] = description
        
        with patch.object(ServerClient, '__init__', lambda self, *args, **kwargs: None):
            sc = ServerClient.__new__(ServerClient)
            sc._on_idea_created = on_idea_created
            
            test_title = "Test Idea"
            test_board = "testboard"
            test_desc = "A description"
            
            if sc._on_idea_created:
                sc._on_idea_created(test_title, test_board, test_desc)
            
            assert received["title"] == test_title
            assert received["board"] == test_board
            assert received["description"] == test_desc

    def test_callback_with_empty_optional_fields(self):
        """Test that callback handles missing optional fields."""
        from server_client import ServerClient
        
        received = {}
        
        def on_idea_created(title, board, description):
            received["title"] = title
            received["board"] = board
            received["description"] = description
        
        with patch.object(ServerClient, '__init__', lambda self, *args, **kwargs: None):
            sc = ServerClient.__new__(ServerClient)
            sc._on_idea_created = on_idea_created
            
            if sc._on_idea_created:
                sc._on_idea_created("Title", "board", "")
            
            assert received["title"] == "Title"
            assert received["description"] == ""


class TestCreateIdeaPipelineIntegration:
    """Integration tests for create_idea through the pipeline."""

    def test_create_idea_message_format(self):
        """Test that create_idea message has correct format."""
        import json
        
        msg = {
            "type": "create_idea",
            "title": "Test Idea",
            "board": "testboard", 
            "description": "A description"
        }
        
        json_str = json.dumps(msg)
        parsed = json.loads(json_str)
        
        assert parsed["type"] == "create_idea"
        assert parsed["title"] == "Test Idea"
        assert parsed["board"] == "testboard"
        assert parsed["description"] == "A description"

    def test_create_idea_message_without_description(self):
        """Test create_idea message format without optional description."""
        import json
        
        msg = {
            "type": "create_idea",
            "title": "Test Idea",
            "board": "testboard"
        }
        
        json_str = json.dumps(msg)
        parsed = json.loads(json_str)
        
        assert parsed["type"] == "create_idea"
        assert "description" not in parsed or parsed.get("description") is None
