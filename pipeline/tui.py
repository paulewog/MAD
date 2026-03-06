#!/usr/bin/env python3
"""MAD Pipeline TUI — interactive kanban dashboard built with Textual."""

import sys
import time
from pathlib import Path
from typing import Optional

# Ensure pipeline modules are importable when running tui.py directly
sys.path.insert(0, str(Path(__file__).parent))

from agent_status import AgentStatus
from config import Config
from phases import run_pipeline, _load_prompt, update_design_doc, _get_latest_feedback
from runner import AgentRunner
from state import STAGES, FeatureFile

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll, Grid
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Header,
    Input,
    Label,
    Log,
    Markdown,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

# Pipeline phases that can have different agents assigned
PIPELINE_PHASES = [
    ("planning", "Planning"),
    ("reviewing_plan", "Plan Review"),
    ("spec_writing", "Spec Writing"),
    ("implementing", "Implementing"),
    ("fix_feedback", "Fix Feedback"),
    ("testing", "Testing"),
    ("review", "Review"),
]

# Stages that require human attention — highlighted differently
HUMAN_STAGES = {"final-human-approval"}

# Display order: ideas first, then pipeline flow, then archived
STAGE_DISPLAY_ORDER = [
    "ideas",
    "plan-inbox",
    "approved",
    "reviewing-plan",
    "spec-writing",
    "implementing",
    "testing",
    "review",
    "final-human-approval",
    "done",
    "rejected",
]


def _feature_markdown(feature: FeatureFile) -> str:
    """Build a Markdown string showing all sections of a feature file."""
    header = f"**Board:** {feature.board} | **Stage:** {feature.current_stage} | **ID:** {feature.id}"
    if feature.design_ref:
        header += f" | **Design:** {feature.design_ref}"
    
    lines = [
        f"# {feature.title}",
        "",
        header,
        "",
    ]

    description = feature.get_section("Description")
    if description:
        lines += ["## Description", "", description, ""]

    if feature.plan:
        lines += ["## Plan", "", feature.plan, ""]

    if feature.impl_spec:
        lines += ["## Implementation Spec", "", feature.impl_spec, ""]

    if feature.test_spec:
        lines += ["## Test Spec", "", feature.test_spec, ""]

    if feature.impl_notes:
        lines += ["## Implementation Notes", "", feature.impl_notes, ""]

    if feature.history:
        # Escape markdown in history to display as plain text
        escaped_history = feature.history.replace("[", r"\[").replace("]", r"\]")
        lines += ["## History", "", escaped_history, ""]

    if feature.pipeline_log:
        lines += ["## Pipeline Log", "", feature.pipeline_log, ""]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Custom widgets for the kanban left pane
# ---------------------------------------------------------------------------


class StageHeader(Static):
    """Focusable stage header in the kanban list."""

    class Selected(Message):
        """Posted when this stage header is selected."""

        def __init__(self, stage: str) -> None:
            self.stage = stage
            super().__init__()

    def __init__(self, stage: str, count: int) -> None:
        self.stage = stage
        self.count = count
        if count > 0:
            arrow = "v"
        else:
            arrow = "-"
        label = f" {arrow} {stage.upper()} ({count})"
        super().__init__(label)
        self.can_focus = True

        classes = "stage-header"
        if count > 0:
            classes += " has-features"
        if stage in HUMAN_STAGES and count > 0:
            classes += " needs-attention"
        self.set_classes(classes)

    def on_click(self) -> None:
        self.post_message(self.Selected(self.stage))


class FeatureItem(Static):
    """A clickable/focusable feature item in the kanban list."""

    class Selected(Message):
        """Posted when this feature item is selected."""

        def __init__(self, feature: FeatureFile) -> None:
            self.feature = feature
            super().__init__()

    def __init__(self, feature: FeatureFile, is_selected: bool = False) -> None:
        self.feature = feature
        display_name = feature.slug
        # Truncate long slugs for the left pane
        if len(display_name) > 24:
            display_name = display_name[:22] + ".."
        indicator = " << " if is_selected else ""
        super().__init__(f"    {display_name}{indicator}")
        self.can_focus = True
        classes = "feature-item"
        if is_selected:
            classes += " --selected"
        self.set_classes(classes)

    def on_click(self) -> None:
        self.post_message(self.Selected(self.feature))

    def on_focus(self) -> None:
        self.post_message(self.Selected(self.feature))


# ---------------------------------------------------------------------------
# Modal dialogs
# ---------------------------------------------------------------------------


class RejectModal(ModalScreen[str | None]):
    """Modal to collect an optional rejection reason."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    RejectModal {
        align: center middle;
    }
    #reject-dialog {
        width: 60;
        height: auto;
        max-height: 16;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #reject-dialog Input {
        margin: 1 0;
    }
    #reject-buttons {
        margin-top: 1;
        height: 3;
    }
    #reject-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="reject-dialog"):
            yield Label("Reject feature -- optional reason:")
            yield Input(
                placeholder="Reason (press Enter to skip)", id="reject-reason"
            )
            with Horizontal(id="reject-buttons"):
                yield Button("Reject", variant="error", id="reject-confirm")
                yield Button("Cancel", variant="default", id="reject-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reject-confirm":
            reason_input = self.query_one("#reject-reason", Input)
            reason = reason_input.value.strip() or "No reason given"
            self.dismiss(reason)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        reason = event.value.strip() or "No reason given"
        self.dismiss(reason)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NewBoardModal(ModalScreen[str | None]):
    """Modal to create a new board."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    NewBoardModal {
        align: center middle;
    }
    #board-dialog {
        width: 50;
        height: auto;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #board-dialog Input {
        margin: 1 0;
    }
    #board-buttons {
        margin-top: 1;
        height: 3;
    }
    #board-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="board-dialog"):
            yield Label("New Board")
            yield Label("Board name:")
            yield Input(placeholder="e.g. myproject", id="board-name")
            with Horizontal(id="board-buttons"):
                yield Button("Create", variant="primary", id="board-create")
                yield Button("Cancel", variant="default", id="board-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "board-create":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        name = self.query_one("#board-name", Input).value.strip()
        if not name:
            self.query_one("#board-name", Input).focus()
            return
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NewFeatureModal(ModalScreen[tuple[str, str, str] | None]):
    """Modal to create a new feature -- collects title, description, and board."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    NewFeatureModal {
        align: center middle;
    }
    #new-dialog {
        width: 70;
        height: auto;
        max-height: 22;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #new-dialog Input {
        margin: 1 0;
    }
    #new-dialog Select {
        margin: 1 0;
    }
    #new-buttons {
        margin-top: 1;
        height: 3;
    }
    #new-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, boards: list[str], default_board: str) -> None:
        self.boards = boards
        self.default_board = default_board
        super().__init__()

    def compose(self) -> ComposeResult:
        options = [(b, b) for b in self.boards]
        with Vertical(id="new-dialog"):
            yield Label("New Feature")
            yield Label("Board:")
            yield Select(options, value=self.default_board, id="new-board")
            yield Label("Title (required):")
            yield Input(placeholder="Feature title", id="new-title")
            yield Label("Description (optional):")
            yield Input(placeholder="Brief description", id="new-desc")
            with Horizontal(id="new-buttons"):
                yield Button("Create", variant="primary", id="new-create")
                yield Button("Cancel", variant="default", id="new-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-create":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        title = self.query_one("#new-title", Input).value.strip()
        if not title:
            self.query_one("#new-title", Input).focus()
            return
        desc = self.query_one("#new-desc", Input).value.strip()
        board = self.query_one("#new-board", Select).value
        if board is Select.BLANK:
            board = self.default_board
        self.dismiss((str(board), title, desc))

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------


class SettingsPane(Static):
    """Settings panel for configuring agents and pipeline options."""

    class SettingsChanged(Message):
        """Posted when settings are modified."""

        def __init__(self) -> None:
            super().__init__()

    def __init__(self, config: "Config", **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="settings-scroll"):
            yield Label("[bold]Pipeline Settings[/bold]\n", id="settings-title")
            
            # Default agent selector
            agents = list(self._config.agents.keys())
            current_default = self._config.current_agent_name
            agent_options = [(a, a) for a in agents]
            
            yield Label("Default Agent:")
            yield Select(
                agent_options,
                value=current_default,
                id="default-agent-select"
            )
            yield Label("")
            
            # Phase assignments
            yield Label("[bold]Agent Phase Assignments[/bold]\n")
            yield Label("Select which agent to use for each pipeline phase:")
            yield Label("")
            
            # Get current phase assignments
            agent_for_phase = self._config.agent_for_phase
            
            # Build phase -> agent selection
            for phase_key, phase_label in PIPELINE_PHASES:
                current_agent = agent_for_phase.get(phase_key, current_default)
                agent_options = [(a, a) for a in agents]
                yield Label(f"{phase_label}:")
                yield Select(
                    agent_options,
                    value=current_agent,
                    id=f"phase-{phase_key}"
                )
                yield Label("")
            
            # Save button
            yield Label("")
            yield Button("Save Settings", variant="primary", id="save-settings")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-settings":
            self._save_settings()

    def _save_settings(self) -> None:
        try:
            # Get default agent
            default_select = self.query_one("#default-agent-select", Select)
            new_default = default_select.value
            if new_default and new_default != Select.BLANK and isinstance(new_default, str):
                self._config.set_current_agent(new_default)
            
            # Get phase assignments
            new_agent_for_phase = {}
            for phase_key, _ in PIPELINE_PHASES:
                phase_select = self.query_one(f"#phase-{phase_key}", Select)
                agent = phase_select.value
                if agent and agent != Select.BLANK and isinstance(agent, str):
                    new_agent_for_phase[phase_key] = agent
            
            # Save phase assignments
            self._config.set_agent_for_phases(new_agent_for_phase)
            
            self.post_message(self.SettingsChanged())
            self.notify("Settings saved!", severity="information")
        except Exception as e:
            self.notify(f"Failed to save: {e}", severity="error")

# Number of stdout tail lines to display in the status widget
_STATUS_TAIL_LINES = 8


class AgentStatusWidget(Static):
    """Always-visible panel showing real-time agent progress at the bottom of the right pane."""

    REFRESH_INTERVAL = 1.0  # seconds between re-renders

    def __init__(self, status: AgentStatus, **kwargs) -> None:
        super().__init__(**kwargs)
        self._status = status

    def on_mount(self) -> None:
        self.set_interval(self.REFRESH_INTERVAL, self.refresh)

    def render(self) -> str:
        status = self._status
        if not status.running:
            return "[dim]● idle[/dim]"

        # Elapsed time
        if status.started_at:
            elapsed = time.time() - status.started_at
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        else:
            elapsed_str = "0s"

        parts = [
            f"[bold cyan]● running[/bold cyan]"
            f"  [bold]{status.agent}[/bold]"
            f"  [blue]{status.feature_slug}[/blue]"
            f"  [yellow]{status.phase}[/yellow]"
            f"  [dim]{elapsed_str}[/dim]",
        ]

        tail = status.lines[-_STATUS_TAIL_LINES:] if status.lines else []
        for line in tail:
            # Escape Rich markup in raw subprocess output
            safe_line = line.replace("[", r"\[")
            parts.append(f"[dim]{safe_line}[/dim]")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


class PipelineApp(App):
    """MAD Pipeline TUI -- interactive kanban dashboard."""

    TITLE = "MAD Pipeline"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("i", "interactive", "Interactive"),
        Binding("a", "approve", "Approve"),
        Binding("r", "review", "Review"),
        Binding("ctrl+r", "restart", "Restart"),
        Binding("x", "reject", "Reject"),
        Binding("u", "restore", "Restore"),
        Binding("p", "promote", "Promote"),
        Binding("s", "promote_skip", "Promote Skip"),
        Binding("o", "run_pipeline", "Run"),
        Binding("e", "toggle_auto_run", "Auto-Run"),
        Binding("n", "new_feature", "New"),
        Binding("b", "new_board", "New Board"),
        Binding("f", "refresh", "Refresh"),
        Binding("t", "toggle_log_detail", "Log/Detail"),
        Binding("tab", "toggle_focus", "Switch Pane"),
        Binding("f2", "go_to_settings", "Settings"),
        Binding("up", "focus_previous", "Up"),
        Binding("down", "focus_next", "Down"),
    ]

    # Stage -> list of available actions (key, action, label)
    STAGE_ACTIONS = {
        "ideas": [("i", "interactive", "Interactive"), ("a", "approve", "To Plan-Inbox"), ("x", "reject", "Reject")],
        "plan-inbox": [("x", "reject", "Reject"), ("o", "run_pipeline", "Run"), ("ctrl+r", "restart", "Restart")],
        "reviewing-plan": [("o", "run_pipeline", "Run"), ("ctrl+r", "restart", "Restart")],
        "approved": [("x", "reject", "Reject"), ("o", "run_pipeline", "Run"), ("ctrl+r", "restart", "Restart")],
        "spec-writing": [("x", "reject", "Reject"), ("o", "run_pipeline", "Run"), ("ctrl+r", "restart", "Restart")],
        "implementing": [("x", "reject", "Reject"), ("ctrl+r", "restart", "Restart")],
        "testing": [("x", "reject", "Reject"), ("ctrl+r", "restart", "Restart")],
        "review": [("a", "approve", "Approve"), ("x", "reject", "Reject"), ("ctrl+r", "restart", "Restart")],
        "final-human-approval": [("a", "approve", "Done"), ("x", "reject", "Reject")],
        "done": [("u", "restore", "Restore")],
        "rejected": [("u", "restore", "Restore")],
    }

    # Global hotkeys that are always available
    GLOBAL_BINDINGS = [
        ("n", "new_feature", "New"),
        ("b", "new_board", "New Board"),
        ("f", "refresh", "Refresh"),
        ("t", "toggle_log_detail", "Log/Detail"),
        ("tab", "toggle_focus", "Switch"),
        ("e", "toggle_auto_run", "Auto-Run"),
    ]

    def get_contextual_bindings(self) -> list[tuple]:
        """Get bindings - global + stage-specific actions."""
        # Always include global hotkeys
        bindings = list(self.GLOBAL_BINDINGS)
        
        if not self.selected_feature:
            return bindings + [("q", "quit", "Quit")]
        
        # Add stage-specific actions
        stage = self.selected_feature.current_stage
        stage_bindings = self.STAGE_ACTIONS.get(stage, [])
        
        # Add stage actions in the middle, Quit always last
        return bindings + stage_bindings + [("q", "quit", "Quit")]

    CSS = """
    Screen {
        layout: vertical;
    }

    #main-layout {
        layout: horizontal;
        height: 1fr;
    }

    #left-pane {
        width: 34;
        min-width: 24;
        border-right: solid $panel;
        overflow-y: auto;
    }

    #left-pane:focus-within {
        border-right: solid $accent;
    }

    #right-pane {
        width: 1fr;
        padding: 0 1;
    }

    #right-pane:focus-within {
        border-left: solid $accent;
    }

    #detail-view {
        height: 1fr;
        overflow-y: auto;
    }

    #log-view {
        height: 1fr;
        overflow-y: auto;
        border: solid $panel;
        display: none;
    }

    #detail-view.split-top {
        height: 1fr;
    }

    #log-view.split-bottom {
        height: 1fr;
    }

    .stage-header {
        color: $text-muted;
        text-style: bold;
        padding: 0 1;
        margin-top: 1;
    }

    .stage-header.has-features {
        color: $text;
    }

    .stage-header.needs-attention {
        color: yellow;
        text-style: bold;
    }

    .feature-item {
        padding: 0 2;
        color: $text-muted;
        height: 1;
    }

    .feature-item:hover {
        background: $boost;
        color: $text;
    }

    .feature-item:focus {
        background: $accent;
        color: $text;
    }

    .feature-item.--selected {
        background: $accent;
        color: $text;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 2;
    }

    #board-tabs {
        height: 1fr;
    }

    AgentStatusWidget {
        height: 12;
        border-top: solid $panel;
        padding: 0 1;
        color: $text-muted;
        overflow-y: auto;
    }

    #contextual-footer {
        dock: bottom;
        height: 1;
        background: $panel;
    }
    
    #contextual-footer Button {
        margin: 0 1;
    }
    """

    # Currently selected feature
    selected_feature: reactive[FeatureFile | None] = reactive(None)
    # Currently active board tab
    active_board: reactive[str] = reactive("")
    # Whether a pipeline run is in progress
    running: reactive[bool] = reactive(False)
    # Whether auto-run is enabled
    auto_run_enabled: reactive[bool] = reactive(False)
    # Which right pane to show: "detail" or "log"
    right_pane_mode: reactive[str] = reactive("detail")

    def __init__(self) -> None:
        super().__init__()
        self._config = Config()
        self._config.setup_boards()
        # Track previous feature snapshot for change detection in auto-refresh
        self._prev_snapshot: dict[str, list[tuple[str, str]]] = {}
        self._prev_mtimes: dict[str, dict[str, float]] = {}
        # Shared agent status instance — mutated by runner, read by widget
        self._agent_status = AgentStatus()
        # Track which features have been auto-queued to avoid duplicates
        self._auto_queued: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        boards = self._config.boards
        if not boards:
            yield Label("No boards configured. Check ~/MAD/config.json")
            yield Static("", id="contextual-footer")
            return

        # Board tabs sit above the main layout - always show tabs to include Settings
        with TabbedContent(id="main-tabs"):
            # Board tabs
            for board in boards:
                yield TabPane(board, id=f"board-{board}")
            # Settings tab
            yield TabPane("Settings", id="settings-tab")
        
        self.active_board = boards[0] if boards else ""

        # Kanban layout - shown for board tabs, hidden for settings
        with Horizontal(id="main-layout"):
            with VerticalScroll(id="left-pane"):
                yield Static("Loading...", id="kanban-placeholder")
            with Vertical(id="right-pane"):
                yield VerticalScroll(Markdown(
                    "*Select a feature to view details*", id="detail-view"
                ))
                yield Log(id="log-view", auto_scroll=True)
                yield AgentStatusWidget(self._agent_status, id="agent-status")

        # Settings view - hidden by default
        with Vertical(id="settings-view"):
            yield SettingsPane(self._config, id="settings-pane")

        yield Static("", id="status-bar")
        
        # Custom contextual footer with clickable buttons
        with Horizontal(id="contextual-footer"):
            yield Static("", id="footer-content")

    def on_mount(self) -> None:
        """Initialize the first board and start auto-refresh."""
        boards = self._config.boards
        if boards:
            self.active_board = boards[0]
            self._refresh_board(self.active_board)
        # Initialize footer
        self._update_footer()
        # Hide settings view initially
        try:
            self.query_one("#settings-view", Vertical).display = False
        except NoMatches:
            pass
        # Auto-refresh every 10 seconds to pick up external changes
        self.set_interval(10.0, self._auto_refresh)
        # Auto-run check every 10 seconds
        self.set_interval(10.0, self._auto_run_check)

    def on_settings_pane_settings_changed(self, event: SettingsPane.SettingsChanged) -> None:
        """Handle settings changes - reload config."""
        # Reload the config to pick up changes
        self._config = Config(path=self._config._path)
        self.notify("Settings applied", severity="information")

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        """Handle tab switch - boards or settings."""
        pane_id = event.pane.id or ""
        
        if pane_id == "settings-tab":
            self._show_settings_view()
            return
        
        if pane_id.startswith("board-"):
            board = pane_id[len("board-") :]
            self.active_board = board
            self.selected_feature = None
            self._show_kanban_view()
            self._refresh_board(board)
            self._update_detail_view()

    def _show_settings_view(self) -> None:
        """Show the settings panel and hide the kanban."""
        try:
            main_layout = self.query_one("#main-layout", Horizontal)
            main_layout.display = False
            settings_view = self.query_one("#settings-view", Vertical)
            settings_view.display = True
            self.query_one("#status-bar", Static).display = False
            self.query_one("#contextual-footer", Horizontal).display = False
        except NoMatches:
            pass

    def _show_kanban_view(self) -> None:
        """Show the kanban board and hide settings."""
        try:
            main_layout = self.query_one("#main-layout", Horizontal)
            main_layout.display = True
            settings_view = self.query_one("#settings-view", Vertical)
            settings_view.display = False
            self.query_one("#status-bar", Static).display = True
            self.query_one("#contextual-footer", Horizontal).display = True
        except NoMatches:
            pass

    def on_feature_item_selected(self, event: FeatureItem.Selected) -> None:
        """Handle feature selection from the left pane."""
        self.selected_feature = event.feature
        self._show_detail()  # Switch to detail view when selecting
        self._update_detail_view()
        # Re-render left pane to update the selection indicator
        self._refresh_kanban_widgets()

    # ------------------------------------------------------------------
    # Board data loading and rendering
    # ------------------------------------------------------------------

    def _load_features(self, board: str) -> list[FeatureFile]:
        """Load all features for a board."""
        return FeatureFile.list_all(board=board)

    def _snapshot(self, features: list[FeatureFile]) -> list[tuple[str, str]]:
        """Create a lightweight snapshot for change detection."""
        return [(f.slug, f.current_stage) for f in features]

    def _refresh_board(self, board: str) -> None:
        """Reload features and rebuild the kanban left pane for the given board."""
        features = self._load_features(board)
        self._prev_snapshot[board] = self._snapshot(features)
        self._current_features = features
        self._refresh_kanban_widgets()
        self._update_status_bar(board, features)

    def _refresh_kanban_widgets(self) -> None:
        """Rebuild the kanban left pane widgets from self._current_features."""
        features = getattr(self, "_current_features", [])

        # Group features by stage
        by_stage: dict[str, list[FeatureFile]] = {s: [] for s in STAGES}
        for f in features:
            stage = f.current_stage
            if stage in by_stage:
                by_stage[stage].append(f)

        try:
            left_pane = self.query_one("#left-pane", VerticalScroll)
        except NoMatches:
            return

        # Remove all current children
        left_pane.remove_children()

        # Build new widgets
        selected_slug = self.selected_feature.slug if self.selected_feature else None
        selected_board = self.selected_feature.board if self.selected_feature else None

        for stage in STAGE_DISPLAY_ORDER:
            stage_features = by_stage.get(stage, [])
            count = len(stage_features)
            left_pane.mount(StageHeader(stage, count))

            if count > 0:
                for f in stage_features:
                    is_sel = (
                        f.slug == selected_slug and f.board == selected_board
                    )
                    left_pane.mount(FeatureItem(f, is_selected=is_sel))

    def _update_status_bar(
        self, board: str, features: list[FeatureFile]
    ) -> None:
        """Update the bottom status bar text."""
        by_stage: dict[str, list[FeatureFile]] = {s: [] for s in STAGES}
        for f in features:
            if f.current_stage in by_stage:
                by_stage[f.current_stage].append(f)

        total = len(features)
        human_count = sum(len(by_stage.get(s, [])) for s in HUMAN_STAGES)
        status_text = f" {board}: {total} features"
        if human_count:
            status_text += f" | {human_count} need attention"
        if self.auto_run_enabled:
            status_text += " | [green]AUTO-RUN ON[/green] (press e to toggle)"
        else:
            status_text += " | [dim]auto-run off[/dim] (press e to enable)"

        try:
            self.query_one("#status-bar", Static).update(status_text)
        except NoMatches:
            pass

    def _auto_refresh(self) -> None:
        """Periodically check for changes and refresh if needed.
        
        Uses file modification times to detect changes efficiently.
        """
        board = self.active_board
        if not board:
            return
        
        try:
            features = self._load_features(board)
            
            # Build current state: slug -> mtime mapping
            current_mtimes = {f.slug: f.path.stat().st_mtime for f in features}
            current_snapshot = self._snapshot(features)
            
            # Check if anything changed
            prev_mtimes = self._prev_mtimes.get(board, {})
            prev_snapshot = self._prev_snapshot.get(board, [])
            
            # Need refresh if: snapshot changed OR any file mtime changed
            snapshot_changed = current_snapshot != prev_snapshot
            mtimes_changed = any(
                current_mtimes.get(slug, 0) != prev_mtimes.get(slug, 0)
                for slug in set(list(current_mtimes.keys()) + list(prev_mtimes.keys()))
            )
            
            if not snapshot_changed and not mtimes_changed:
                return  # Nothing changed, skip refresh
            
            # Something changed - refresh
            self._current_features = features
            self._prev_snapshot[board] = current_snapshot
            self._prev_mtimes[board] = current_mtimes
            self._refresh_kanban_widgets()
            self._update_status_bar(board, features)
            
            # Always refresh selected feature from disk to show latest content
            if self.selected_feature:
                refreshed = FeatureFile.find(self.selected_feature.slug)
                if refreshed:
                    self.selected_feature = refreshed
                    self._update_detail_view()
        except Exception:
            pass  # Silently ignore refresh errors

    # ------------------------------------------------------------------
    # Right pane detail view
    # ------------------------------------------------------------------

    def _update_detail_view(self) -> None:
        """Update the right pane Markdown to show the selected feature."""
        try:
            detail = self.query_one("#detail-view", Markdown)
        except NoMatches:
            return

        if not self.selected_feature:
            detail.update("*Select a feature to view details*")
            self._update_footer()
            return

        md_text = _feature_markdown(self.selected_feature)
        detail.update(md_text)
        self._update_footer()

    def _update_footer(self) -> None:
        """Update the contextual footer based on selected feature."""
        try:
            footer_content = self.query_one("#footer-content", Static)
        except NoMatches:
            return
        
        bindings = self.get_contextual_bindings()
        # Build footer text: key: label | key: label | ...
        parts = []
        for key, action, label in bindings:
            if key == "ctrl+r":
                parts.append("ctrl+r: Restart")
            else:
                parts.append(f"{key}: {label}")
        
        footer_text = " | ".join(parts)
        footer_content.update(footer_text)

    def _show_detail(self) -> None:
        """Switch right pane to detail (Markdown) view."""
        try:
            detail = self.query_one("#detail-view", Markdown)
            log = self.query_one("#log-view", Log)
            
            # Remove split classes
            detail.remove_class("split-top")
            log.remove_class("split-bottom")
            
            detail.display = True
            log.display = False
        except NoMatches:
            pass

    def _show_log(self) -> None:
        """Switch right pane to log view."""
        try:
            detail = self.query_one("#detail-view", Markdown)
            log = self.query_one("#log-view", Log)
            
            # Remove split classes
            detail.remove_class("split-top")
            log.remove_class("split-bottom")
            
            detail.display = False
            log.display = True
            log.clear()
        except NoMatches:
            pass

    def _show_split(self) -> None:
        """Show split view with detail on top and log at bottom."""
        try:
            detail = self.query_one("#detail-view", Markdown)
            log = self.query_one("#log-view", Log)
            
            # Remove old split classes first
            detail.remove_class("split-top")
            log.remove_class("split-bottom")
            
            # Add split classes and show both
            detail.add_class("split-top")
            detail.display = True
            
            log.add_class("split-bottom")
            log.display = True
        except NoMatches:
            pass

    def _log_line(self, text: str) -> None:
        """Write a line to the log view."""
        try:
            log = self.query_one("#log-view", Log)
            log.write_line(text)
            log.scroll_end()  # Auto-scroll to bottom
        except NoMatches:
            pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_go_to_settings(self) -> None:
        """Switch to the Settings tab."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "settings-tab"
        except NoMatches:
            pass

    def action_refresh(self) -> None:
        """Manually refresh the board."""
        self._refresh_board(self.active_board)
        self.notify("Board refreshed", severity="information")

    def action_toggle_log_detail(self) -> None:
        """Cycle through: detail -> log -> split -> detail."""
        modes = ["detail", "log", "split"]
        current_idx = modes.index(self.right_pane_mode) if self.right_pane_mode in modes else 0
        next_idx = (current_idx + 1) % len(modes)
        self.right_pane_mode = modes[next_idx]
        
        if self.right_pane_mode == "split":
            self._show_split()
        elif self.right_pane_mode == "log":
            self._show_log()
        else:
            self._show_detail()
            self._update_detail_view()

    def action_toggle_focus(self) -> None:
        """Toggle focus between left and right panes."""
        try:
            left = self.query_one("#left-pane")
            right_detail = self.query_one("#detail-view")
        except NoMatches:
            return

        # If focus is in the left pane, move to right; otherwise move to left
        if self.focused and left in self.focused.ancestors_with_self:
            right_detail.focus()
        else:
            # Focus the first focusable feature item in the left pane
            items = self.query(FeatureItem)
            if items:
                items[0].focus()

    def action_focus_previous(self) -> None:
        """Move focus to the previous item (stage header or feature)."""
        try:
            left_pane = self.query_one("#left-pane")
            left_pane.focus()
        except Exception:
            pass
        
        stage_headers = self.query(StageHeader)
        feature_items = self.query(FeatureItem)
        items = list(stage_headers) + list(feature_items)
        if not items:
            return
        item_list = items
        
        current = self.focused
        if current and current in item_list:
            try:
                idx = item_list.index(current)
                if idx > 0:
                    item_list[idx - 1].focus()
                else:
                    item_list[-1].focus()
            except ValueError:
                if item_list:
                    item_list[0].focus()
        else:
            if item_list:
                item_list[0].focus()

    def action_focus_next(self) -> None:
        """Move focus to the next item (stage header or feature)."""
        try:
            left_pane = self.query_one("#left-pane")
            left_pane.focus()
        except Exception:
            pass
        
        stage_headers = self.query(StageHeader)
        feature_items = self.query(FeatureItem)
        items = list(stage_headers) + list(feature_items)
        if not items:
            return
        item_list = items
        
        current = self.focused
        if current and current in item_list:
            try:
                idx = item_list.index(current)
                if idx < len(item_list) - 1:
                    item_list[idx + 1].focus()
                else:
                    item_list[0].focus()
            except ValueError:
                if item_list:
                    item_list[0].focus()
        else:
            if item_list:
                item_list[0].focus()

    def action_approve(self) -> None:
        """Approve the selected feature (move to plan-inbox or complete it)."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        
        # Handle different stages
        if f.current_stage == "ideas":
            # Move ideas -> plan-inbox
            f.add_history("PROMOTED", "Moved from ideas to plan-inbox")
            f.save()
            f.move_to_stage("plan-inbox")
            self.notify(f"Moved to plan-inbox: {f.title}", severity="information")
        elif f.current_stage == "final-human-approval":
            # Approve on final-human-approval = it's done
            f.add_history("DONE", "Approved as complete (TUI)")
            f.save()
            f.move_to_stage("done")
            self.notify(f"Completed: {f.title}", severity="information")
        elif f.current_stage == "review":
            # Approve in review = pass review and go to final-human-approval
            f.add_history("APPROVED", "Passed review (TUI)")
            f.save()
            f.move_to_stage("final-human-approval")
            self.notify(f"Approved (passed review): {f.title}", severity="information")
        else:
            self.notify(
                f"Cannot approve from {f.current_stage}",
                severity="warning",
            )
            return

        # Refresh to show the move
        refreshed = FeatureFile.find(f.slug)
        self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()

    def action_interactive(self) -> None:
        """Open an interactive session to iterate on the selected feature."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        
        if f.current_stage not in ("ideas", "plan-inbox"):
            self.notify(
                f"Interactive mode only available for ideas/plan-inbox, not {f.current_stage}",
                severity="warning",
            )
            return

        config = self._config
        runner = AgentRunner(config)

        context = (
            f"# Feature Iteration: {f.title}\n\n"
            f"The feature file is at: `{f.path.name}`\n\n"
            f"## Current Description:\n{f.get_section('Description') or '(no description)'}\n\n"
            f"## Current Plan:\n{f.plan or '(no plan yet)'}\n\n"
            f"Please discuss and iterate on this feature with the user. "
            f"You can update the Description and Plan sections of the feature file based on your discussion. "
            f"When finished, let the user know they're done."
        )

        # Suspend the TUI and launch the interactive agent
        with self.suspend():
            runner.interactive(
                workdir=f.path.parent,
                initial_message=context,
            )

        # After returning, refresh everything
        refreshed = FeatureFile.find(f.slug)
        if refreshed:
            self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()

    def action_review(self) -> None:
        """Open an interactive agent session to review the selected feature."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return

        config = self._config
        runner = AgentRunner(config)

        context = (
            f"# Plan Review: {f.title}\n\n"
            f"The feature file is at: `{f.path.name}`\n\n"
            f"1. Read the file and review the **## Plan** section.\n"
            f"2. Share your observations: what looks good, what's unclear, what's missing, and any concerns.\n"
            f"3. Suggest specific improvements, but **do not edit the file yet**.\n"
            f"4. Ask the user: do these suggestions make sense? What would they like to change, keep, or explore further?\n"
            f"5. Iterate based on their feedback, then update the plan once you've agreed on the changes.\n"
        )

        # Suspend the TUI and launch the interactive agent
        with self.suspend():
            runner.interactive(
                workdir=f.path.parent,
                initial_message=context,
            )

        # After returning, refresh everything
        refreshed = FeatureFile.find(f.slug)
        if refreshed:
            self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()

    def action_reject(self) -> None:
        """Reject the selected feature (move to appropriate stage based on current stage)."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        
        # Handle different stages
        if f.current_stage == "final-human-approval":
            # Push a modal to get rejection reason
            def handle_final_reject(reason: str | None) -> None:
                if reason is None:
                    return
                feedback = _get_latest_feedback(f)
                full_feedback = f"Human rejection: {reason}\n\nPrevious review feedback:\n{feedback}"
                f.add_history("REJECTED", f"Sent back to implementing: {reason}")
                f.save()
                f.move_to_stage("implementing")
                self.notify(f"Sent back to implementing: {f.title}", severity="information")
                refreshed = FeatureFile.find(f.slug)
                self.selected_feature = refreshed
                self._refresh_board(self.active_board)
                self._update_detail_view()
            
            self.push_screen(RejectModal(), callback=handle_final_reject)
            return

        def handle_reject(reason: str | None) -> None:
            if reason is None:
                return  # Cancelled
            
            # ideas and plan-inbox go to rejected
            if f.current_stage in ("ideas", "plan-inbox"):
                f.add_history("REJECTED", f"Rejected: {reason}")
                f.save()
                f.move_to_stage("rejected")
                self.notify(f"Rejected: {f.title}", severity="information")
            else:
                # Most stages go back to implementing for fixes
                f.add_history("REJECTED", f"Rejected: {reason}")
                f.save()
                f.move_to_stage("implementing")
                self.notify(f"Sent back to implementing: {f.title}", severity="information")

            refreshed = FeatureFile.find(f.slug)
            self.selected_feature = refreshed
            self._refresh_board(self.active_board)
            self._update_detail_view()

        self.push_screen(RejectModal(), callback=handle_reject)

    def action_restore(self) -> None:
        """Restore a rejected feature back to ideas."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        if f.current_stage not in ("rejected",):
            self.notify(
                f"Cannot restore: feature is in '{f.current_stage}' "
                "(only rejected can be restored)",
                severity="warning",
            )
            return

        f.add_history("RESTORED", "Feature restored to ideas")
        f.save()
        f.move_to_stage("ideas")
        self.notify(f"Restored to ideas: {f.title}", severity="information")

        refreshed = FeatureFile.find(f.slug)
        self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()

    def action_promote(self, skip_awaiting: bool = False) -> None:
        """Promote a feature to the next stage."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        
        if f.current_stage == "ideas":
            f.add_history("PROMOTED", "Promoted from ideas to plan-inbox")
            f.save()
            f.move_to_stage("plan-inbox")
            self.notify(f"Promoted: {f.title} -> plan-inbox", severity="information")
        elif f.current_stage == "plan-inbox":
            # Run pipeline to complete the feature
            self.action_run_pipeline()
        elif f.current_stage == "final-human-approval":
            f.add_history("PROMOTED", "Promoted from final-human-approval to done")
            f.save()
            f.move_to_stage("done")
            
            # Update design doc if design_ref is set
            if f.design_ref:
                try:
                    from runner import AgentRunner
                    runner = AgentRunner(self._config)
                    update_design_doc(f, runner)
                except Exception as e:
                    self.notify(f"Design doc update failed: {e}", severity="warning")
            
            self.notify(f"Promoted: {f.title} -> done", severity="information")
        else:
            self.notify(
                f"Cannot promote: feature is in '{f.current_stage}'. "
                "Use approve to move to approved.",
                severity="warning",
            )
            return

        refreshed = FeatureFile.find(f.slug)
        self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()

    def action_promote_skip(self) -> None:
        """Promote from plan-inbox directly to run pipeline."""
        self.action_promote(skip_awaiting=True)

    def action_restart(self) -> None:
        """Restart pipeline - automatically detects current phase and runs full pipeline from there."""
        if not self.selected_feature:
            self.notify("No feature selected", severity="warning")
            return
        
        f = self.selected_feature
        stage = f.current_stage
        
        self.notify(f"Starting pipeline from {stage}...", severity="information")
        
        # Auto-switch to log view to see pipeline output
        self.right_pane_mode = "log"
        self._show_log()
        self._log_line(f"Starting pipeline from {stage}...")
        
        # Run in background thread
        self._run_restart_async(f, stage)

    @work(thread=True, exclusive=True, group="pipeline")
    def _run_restart_async(self, feature: FeatureFile, stage: str) -> None:
        """Run restart pipeline in background thread."""
        from phases import (
            run_pipeline, run_pipeline_from_implementing,
            run_writing_tests, run_review_impl
        )
        from runner import AgentRunner
        
        runner = AgentRunner(self._config)
        
        # Set up status for display
        status = self._agent_status
        status.lines = []
        status.phase = ""
        status.started_at = 0.0
        status.agent = runner.agent.name
        status.feature_slug = feature.slug
        status.running = True
        
        try:
            if stage in ("plan-inbox", "reviewing-plan", "spec-writing", "approved", "ideas", "inbox"):
                run_pipeline(feature, runner, status=status)
            elif stage == "implementing":
                run_pipeline_from_implementing(feature, runner, status=status)
            elif stage == "testing":
                verdict = "FAIL"
                for attempt in range(1, 4):
                    run_writing_tests(feature, runner, status=status)
                    verdict, feedback = run_review_impl(feature, runner, status=status)
                    if verdict == "PASS":
                        break
                if verdict != "PASS":
                    self.call_from_thread(self.notify, "Review retries exhausted, continuing...", severity="warning")
                    run_pipeline_from_implementing(feature, runner, status=status)
            elif stage == "review":
                verdict, feedback = run_review_impl(feature, runner, status=status)
                if verdict != "PASS":
                    self.call_from_thread(self.notify, "Review failed, continuing pipeline...", severity="warning")
                    run_pipeline_from_implementing(feature, runner, status=status)
            else:
                self.call_from_thread(self.notify, f"Cannot restart from {stage}", severity="warning")
                return
            
            self.call_from_thread(self.notify, "Pipeline completed", severity="information")
            self.call_from_thread(self._refresh_board, self.active_board)
        except Exception as e:
            self.call_from_thread(self.notify, f"Pipeline failed: {e}", severity="error")

    def action_toggle_auto_run(self) -> None:
        """Toggle automatic pipeline execution for approved features."""
        self.auto_run_enabled = not self.auto_run_enabled
        if self.auto_run_enabled:
            self.notify("Auto-run enabled - will process approved features automatically", severity="information")
        else:
            self.notify("Auto-run disabled", severity="information")
        self._update_status_bar(self.active_board, self._load_features(self.active_board))

    # Auto-run settings
    AUTO_RUN_INTERVAL = 1800  # 30 minutes between runs
    _last_auto_run_time: Optional[float] = None

    def _auto_run_check(self) -> None:
        """Check for plan-inbox features and auto-run pipeline if enabled.
        
        After a run completes, waits 30 minutes before picking another.
        """
        if not self.auto_run_enabled:
            return
        if self.running:
            return
        
        # Check if we should wait since last run
        now = time.time()
        if self._last_auto_run_time is not None:
            elapsed = now - self._last_auto_run_time
            if elapsed < self.AUTO_RUN_INTERVAL:
                return  # Still waiting
        
        for board in self._config.boards:
            # Check plan-inbox and reviewing-plan (features ready to process)
            inbox = FeatureFile.list_all(board=board, stage="plan-inbox")
            reviewing = FeatureFile.list_all(board=board, stage="reviewing-plan")
            candidates = inbox + reviewing
            for f in candidates:
                if f.slug in self._auto_queued:
                    continue
                self._last_auto_run_time = now
                self._auto_queued.add(f.slug)
                self._log_line(f"[auto] Found feature to process: {f.title} ({f.current_stage})")
                self._auto_run_feature(f)
                break
            else:
                continue
            break

    def _auto_run_feature(self, feature: FeatureFile) -> None:
        """Auto-run pipeline on a feature (internal)."""
        self._show_log()
        self._log_line(f"=== Auto-running pipeline for: {feature.title} ===")
        self._log_line(f"Stage: {feature.current_stage}")
        self._log_line("")
        self._run_pipeline_async(feature)

    def action_run_pipeline(self) -> None:
        """Run the full automated pipeline on the selected feature."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        if f.current_stage not in ("approved", "plan-inbox", "reviewing-plan"):
            self.notify(
                f"Cannot run pipeline from {f.current_stage} "
                f"(allowed: plan-inbox, reviewing-plan, approved)",
                severity="warning",
            )
            return
        if self.running:
            self.notify("Pipeline already running", severity="warning")
            return

        self._show_log()
        self._log_line(f"=== Running pipeline for: {f.title} ===")
        self._log_line(f"Stage: {f.current_stage}")
        self._log_line("")
        self._log_line("Starting... (this may take several minutes)")
        self._log_line("")
        # TODO: Full streaming output -- parse stream-json from claude subprocess
        # and show tool calls / assistant text in real time. For now, run_pipeline
        # blocks in a thread and we show completion status.
        self._run_pipeline_async(f)

    @work(thread=True, exclusive=True, group="pipeline")
    def _run_pipeline_async(self, feature: FeatureFile) -> None:
        """Run the pipeline in a background thread."""
        self.running = True
        runner = AgentRunner(self._config)

        # Reset and populate agent status for this run
        status = self._agent_status
        status.lines = []
        status.phase = ""
        status.started_at = 0.0
        status.agent = runner.agent.name
        status.feature_slug = feature.slug
        status.running = True

        try:
            run_pipeline(feature, runner, status=status)
            self.app.call_from_thread(
                self._log_line, "=== Pipeline completed successfully ==="
            )
        except Exception as e:
            self.app.call_from_thread(
                self._log_line, f"=== Pipeline failed: {e} ==="
            )
        finally:
            self.running = False
            status.running = False
            self.app.call_from_thread(self._on_pipeline_done, feature.slug)

    def _on_pipeline_done(self, slug: str) -> None:
        """Called on main thread after pipeline completes."""
        self._auto_queued.discard(slug)
        refreshed = FeatureFile.find(slug)
        if refreshed:
            self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()
        self._show_detail()
        self.notify("Pipeline run finished", severity="information")

    def action_new_board(self) -> None:
        """Open modal to create a new board."""
        def handle_new_board(name: str | None) -> None:
            if not name:
                return
            try:
                self._config.add_board(name)
                # Add a new tab for the board
                tabs = self.query_one("#board-tabs", TabbedContent)
                tabs.add_pane(TabPane(name, id=f"board-{name}"))
                self.notify(f"Board created: {name}", severity="information")
            except ValueError as e:
                self.notify(str(e), severity="error")

        self.push_screen(NewBoardModal(), callback=handle_new_board)

    def action_new_feature(self) -> None:
        """Open modal to create a new feature."""
        boards = self._config.boards
        default = self.active_board or (boards[0] if boards else "")

        def handle_new(result: tuple[str, str, str] | None) -> None:
            if result is None:
                return  # Cancelled
            board, title, desc = result
            self._create_feature(board, title, desc)

        self.push_screen(
            NewFeatureModal(boards, default),
            callback=handle_new,
        )

    @work(thread=True, exclusive=True, group="create")
    def _create_feature(self, board: str, title: str, desc: str) -> None:
        """Create a feature file in a background thread, then hand off to interactive planning."""
        try:
            feature = FeatureFile.create(board, title, desc)
            self.app.call_from_thread(
                self._on_feature_created, feature.slug, board
            )
        except Exception as e:
            self.app.call_from_thread(
                self.notify,
                f"Failed to create feature: {e}",
                severity="error",
            )

    def _on_feature_created(self, slug: str, board: str) -> None:
        """Called on main thread after feature file is created — launches interactive planning."""
        # Switch to the board tab if not already there
        if self.active_board != board:
            try:
                tabs = self.query_one("#board-tabs", TabbedContent)
                tabs.active = f"board-{board}"
            except NoMatches:
                pass
            self.active_board = board

        feature = FeatureFile.find(slug)
        if feature:
            self.selected_feature = feature
        self._refresh_board(board)
        self._update_detail_view()

        if not feature:
            self.notify(f"Feature created: {slug}", severity="information")
            return

        # Suspend the TUI and run an interactive design planning session
        template = _load_prompt("plan.md")
        prompt = template.format(
            title=feature.title,
            description=feature.get_section("Description") or "(no description)",
            filepath=feature.path,
        )

        with self.suspend():
            AgentRunner(self._config).interactive(
                workdir=feature.path.parent,
                initial_message=prompt,
            )

        # After the user exits the planning session, reload and finalize
        refreshed = FeatureFile.find(slug)
        if refreshed:
            refreshed.add_history("PLANNING", "Design discussed interactively with user")
            refreshed.save()
            if refreshed.plan:
                refreshed.move_to_stage("approved")
            self.selected_feature = refreshed

        self._refresh_board(board)
        self._update_detail_view()
        self.notify(f"Planning complete: {slug}", severity="information")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_tui():
    app = PipelineApp()
    app.run()


if __name__ == "__main__":
    run_tui()
