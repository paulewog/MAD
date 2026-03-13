import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


class TestPerPhaseAgentSettings:
    """Tests for TUI per-phase agent settings functionality."""

    def test_settingspane_css_select_width(self):
        """Test that CSS sets adequate width for Select widgets in settings."""
        from pipeline.tui import SettingsPane
        
        css = SettingsPane.CSS
        
        assert ".settings-row Select {" in css
        assert "width: 20;" in css

    def test_settingspane_model_field_is_select(self):
        """Test that model field uses Select widget instead of Input."""
        from pipeline.tui import SettingsPane, PIPELINE_PHASES
        from textual.widgets import Select
        
        import inspect
        source = inspect.getsource(SettingsPane.compose)
        
        assert 'id=f"model-' in source
        assert "Select(" in source

    def test_settingspane_agent_options_include_default(self):
        """Test that agent options include 'default' option."""
        from pipeline.tui import SettingsPane
        import inspect
        
        source = inspect.getsource(SettingsPane.compose)
        
        assert '("default", "default")' in source

    def test_settingspane_not_selected_option(self):
        """Test that model dropdown includes 'Not Selected' option."""
        from pipeline.tui import SettingsPane
        import inspect
        
        source = inspect.getsource(SettingsPane.compose)
        
        assert '("", "Not Selected")' in source

    def test_save_settings_handles_select_widgets(self):
        """Test that _save_settings correctly handles Select widgets."""
        from pipeline.tui import SettingsPane
        import inspect
        
        source = inspect.getsource(SettingsPane._save_settings)
        
        assert "Select" in source
        assert 'query_one(f"#model-' in source

    def test_on_select_changed_handler_exists(self):
        """Test that on_select_changed event handler exists."""
        from pipeline.tui import SettingsPane
        import inspect
        
        assert hasattr(SettingsPane, 'on_select_changed')
        
        source = inspect.getsource(SettingsPane.on_select_changed)
        assert "phase-" in source
        assert "model-" in source
        assert "set_options" in source

    def test_on_select_changed_has_error_handling(self):
        """Test that on_select_changed has try/except error handling."""
        from pipeline.tui import SettingsPane
        import inspect
        
        source = inspect.getsource(SettingsPane.on_select_changed)
        
        assert "try:" in source
        assert "except" in source


class TestConfigModelsProperty:
    """Tests for Config.models property."""

    def test_models_property_returns_dict(self):
        """Test that Config.models returns a dictionary of agent to model lists."""
        import pipeline.config as config_module
        
        original_find = config_module.find_config
        original_ensure = config_module.ensure_local_config
        
        try:
            config_module.find_config = lambda: None
            config_module.ensure_local_config = lambda p: Path("/tmp/test_config.json")
            
            test_config = {"models": {"claude": ["a", "b"], "opencode": ["c"]}}
            
            original_open = open
            def mock_open_fn(file, *args, **kwargs):
                if hasattr(file, '__fspath__'):
                    file = file.__fspath__()
                if 'config.json' in str(file):
                    from io import StringIO
                    return MagicMock(__enter__=lambda s: MagicMock(read=lambda: '{"models": {"claude": ["a", "b"], "opencode": ["c"]}}'), __exit__=lambda s, *a: None)
                return original_open(file, *args, **kwargs)
            
            with patch('builtins.open', mock_open_fn):
                with patch.object(config_module.Config, '_load', return_value=test_config):
                    c = config_module.Config(path=Path("/tmp/test_config.json"))
                    assert c.models == {"claude": ["a", "b"], "opencode": ["c"]}
        finally:
            config_module.find_config = original_find
            config_module.ensure_local_config = original_ensure

    def test_get_models_for_agent_returns_correct_models(self):
        """Test that get_models_for_agent returns correct models for each agent."""
        import pipeline.config as config_module
        
        original_find = config_module.find_config
        original_ensure = config_module.ensure_local_config
        
        try:
            config_module.find_config = lambda: None
            config_module.ensure_local_config = lambda p: Path("/tmp/test_config.json")
            
            test_config = {
                "models": {
                    "claude": ["opus", "sonnet", "haiku"],
                    "opencode": ["minimax/MiniMax-M2.5"]
                }
            }
            
            original_open = open
            def mock_open_fn(file, *args, **kwargs):
                if hasattr(file, '__fspath__'):
                    file = file.__fspath__()
                if 'config.json' in str(file):
                    from io import StringIO
                    return MagicMock(__enter__= lambda s: MagicMock(read=lambda: '{"models": {"claude": ["opus", "sonnet", "haiku"], "opencode": ["minimax/MiniMax-M2.5"]}}'), __exit__=lambda s, *a: None)
                return original_open(file, *args, **kwargs)
            
            with patch('builtins.open', mock_open_fn):
                with patch.object(config_module.Config, '_load', return_value=test_config):
                    c = config_module.Config(path=Path("/tmp/test_config.json"))
                    
                    assert c.get_models_for_agent("claude") == ["opus", "sonnet", "haiku"]
                    assert c.get_models_for_agent("opencode") == ["minimax/MiniMax-M2.5"]
                    assert c.get_models_for_agent("default") == []
                    assert c.get_models_for_agent("unknown") == []
        finally:
            config_module.find_config = original_find
            config_module.ensure_local_config = original_ensure
