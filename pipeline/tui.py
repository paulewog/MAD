#!/usr/bin/env python3
"""MAD Pipeline TUI — interactive kanban dashboard built with Textual."""

import asyncio
import atexit
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Import config early for logging setup
from config import Config, get_mad_dir, BUILTIN_AGENTS, PIPELINE_PHASES

# Set up pipeline log file - use code_path if set, otherwise use mad_dir from cwd
_config = Config()
_log_dir = (_config.code_path / ".mad" / "logs" if _config.code_path else get_mad_dir() / "logs")
_log_dir.mkdir(parents=True, exist_ok=True)
_pipeline_log_file = _log_dir / "pipeline.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler(_pipeline_log_file, maxBytes=5*1024*1024, backupCount=5),
    ]
)
logger = logging.getLogger("pipeline")

# Legacy logger for compatibility
app_logger = logging.getLogger(__name__)

# Ensure pipeline modules are importable when running tui.py directly
sys.path.insert(0, str(Path(__file__).parent))

from agent_status import AgentStatus
from config import (
    view_context_file,
    edit_context_file,
)
from lock import PipelineLock, PipelineLockError
from phases import run_pipeline, _load_prompt, update_design_doc, _get_latest_feedback
from runner import AgentRunner, RateLimitError
from scripts import ScriptConfig, ScriptStatus, load_scripts, run_script
from service import service_name_for_dir
from state import STAGES, STAGE_ACTIONS, FeatureFile

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
    Collapsible,
    Header,
    Input,
    Label,
    ListView,
    Log,
    Markdown,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

# Optional server push
try:
    from server_client import ServerClient, HAS_WEBSOCKETS
except ImportError:
    HAS_WEBSOCKETS = False
    ServerClient = None


def _last_modified_key(f: FeatureFile) -> str:
    history = f._data.get("history", [])
    if history:
        return history[-1].get("ts", "")
    return f._data.get("created", "")


def handle_cli_commands(args: list) -> bool:
    """Handle CLI commands. Returns True if command was handled, False otherwise."""
    if len(args) < 2:
        return False
    
    if args[1] == "config":
        return _handle_config_command(args[2:])
    elif args[1] == "context":
        return _handle_context_command(args[2:])
    
    return False


def _handle_config_command(args: list) -> bool:
    """Handle 'mad config' subcommands."""
    if not args:
        print("Usage: mad config [get|set] [key] [value]")
        return True
    
    config = Config()
    
    if args[0] == "get":
        if len(args) < 2:
            print("Usage: mad config get <key>")
            return True
        key = args[1]
        if key == "code_path":
            value = config.get_code_path_value()
            if value:
                print(value)
            else:
                code_path = config.code_path
                if code_path:
                    print(f"(derived) {code_path}")
                else:
                    print("(not set)")
        else:
            print(f"Unknown key: {key}")
        return True
    
    elif args[0] == "set":
        if len(args) < 3:
            print("Usage: mad config set <key> <value>")
            return True
        key = args[1]
        value = args[2]
        if key == "code_path":
            config.set_code_path(value)
            print(f"code_path set to: {value}")
        else:
            print(f"Unknown key: {key}")
        return True
    
    else:
        print(f"Unknown config command: {args[0]}")
        return True


def _handle_context_command(args: list) -> bool:
    """Handle 'mad context' subcommands."""
    if not args:
        print("Usage: mad context [view|edit]")
        return True
    
    config = Config()
    
    if args[0] == "view":
        view_context_file(config)
        return True
    elif args[0] == "edit":
        edit_context_file(config)
        return True
    else:
        print(f"Unknown context command: {args[0]}")
        return True

# Stages that require human attention — highlighted differently
HUMAN_STAGES = {"final-human-approval", "awaiting-human-approval"}

# Display order: ideas first, then pipeline flow, then archived
STAGE_DISPLAY_ORDER = [
    "ideas",
    "ideating",
    "plan-inbox",
    "requested-input",
    "reviewing-plan",
    "awaiting-human-approval",
    "approved",
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
    if feature.done_script:
        header += f" | **Done Script:** {feature.done_script}"
    
    lines = [
        f"# {feature.title}",
        "",
        header,
        "",
    ]

    description = feature.get_section("Description")
    if description:
        lines += ["## Description", "", description, ""]

    if feature.Ideation:
        lines += ["## Ideation", "", feature.Ideation, ""]

    if feature.ideation_summaries:
        lines += ["## Ideation Rounds", ""]
        for i, summary in enumerate(feature.ideation_summaries):
            lines += [f"### Round {i+1}", "", summary, ""]

    if feature.plan:
        lines += ["## Plan", "", feature.plan, ""]

    if feature.impl_spec:
        formatted = _format_json_text(feature.impl_spec)
        lines += ["## Implementation Spec", "", "```", formatted, "```", ""]

    if feature.test_spec:
        formatted = _format_json_text(feature.test_spec)
        lines += ["## Test Spec", "", "```", formatted, "```", ""]

    test_results = feature.test_results
    if test_results:
        ts = test_results.get("ts", "")
        verdict = test_results.get("verdict", "")
        results = test_results.get("results", {})
        feedback = test_results.get("feedback", "")
        
        lines += ["## Test Results", ""]
        if ts:
            lines += [f"**Timestamp:** {ts}", ""]
        if verdict:
            lines += [f"**Verdict:** {verdict}", ""]
        
        if results:
            passed = results.get("passed", 0)
            failed = results.get("failed", 0)
            errors = results.get("errors", 0)
            lines += [f"**Passed:** {passed}  **Failed:** {failed}  **Errors:** {errors}", ""]
        
        if feedback:
            lines += ["**Feedback:**", feedback, ""]
        
        lines += ["", "Use 'tt' to toggle test results", ""]

    if feature.impl_notes:
        formatted = _format_json_text(feature.impl_notes)
        lines += ["## Implementation Notes", "", "```", formatted, "```", ""]

    # Show questions if any
    questions = feature.questions
    if questions:
        lines += ["## Questions for Human", ""]
        for i, q in enumerate(questions):
            q_text = q.get("question", "")
            a_text = q.get("answer", "")
            if a_text:
                lines += [f"- **Q{i+1}:** {q_text}", f"  - **A:** {a_text}", ""]
            else:
                lines += [f"- **Q{i+1}:** {q_text} *(unanswered)*", ""]
        lines += ["", "Press 'q' to answer questions", ""]

    if feature.history:
        # Escape markdown in history to display as plain text
        escaped_history = feature.history.replace("[", r"\[").replace("]", r"\]")
        lines += ["## History", "", escaped_history, ""]

    if feature.pipeline_log:
        lines += ["## Pipeline Log", "", feature.pipeline_log, ""]

    return "\n".join(lines)


def _escape_markup(text: str) -> str:
    """Escape Rich markup characters to display raw text safely."""
    import re
    # Replace markup characters with escaped versions
    text = text.replace("[", r"\[").replace("]", r"\]")
    text = text.replace("{", r"\{").replace("}", r"\}")
    text = re.sub(r'([%])', r'\\\1', text)
    return text


def _format_json_text(text) -> str:
    """Format JSON string for display - parse and pretty-print if valid JSON.
    
    Handles both string and dict types.
    """
    import json
    if not text:
        return ""
    if isinstance(text, dict):
        return json.dumps(text, indent=2)
    text = text.strip()
    if not text:
        return text
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2)
    except (json.JSONDecodeError, ValueError):
        pass
    return text

def _build_feature_detail_widgets(feature: FeatureFile) -> list:
    """Build collapsible widgets for feature detail view.
    
    Returns a list of widgets: Static for always-visible content, 
    and Collapsible for collapsible sections.
    
    Sections:
    - Always visible: Title, Description
    - Collapsible (expanded by default): History
    - Collapsible (collapsed by default): Plan, Impl Spec, Test Spec, Impl Notes, Questions
    """
    from textual.widgets import Collapsible, Static
    
    widgets = []
    
    # Title (always visible) - no ID to avoid duplicate errors
    widgets.append(Static(f"[b]{_escape_markup(feature.title)}[/b]"))
    
    # Description (always visible)
    description = feature.get_section("Description")
    if description:
        widgets.append(Static(_escape_markup(description)))
    
    # Ideation (collapsible, collapsed by default)
    if feature.Ideation:
        import re
        escaped = feature.Ideation.replace("[", r"\[").replace("]", r"\]").replace("{", r"\{").replace("}", r"\}")
        escaped = re.sub(r'([%])', r'\\\1', escaped)
        widgets.append(Collapsible(Static(escaped), title="Ideation", collapsed=True))
    else:
        widgets.append(Collapsible(Static("[dim]No ideation provided[/dim]"), title="Ideation", collapsed=True))
    
    # Ideation Prompt / User Direction (always visible if set)
    if feature.ideation_prompt:
        import re
        escaped_prompt = feature.ideation_prompt.replace("[", r"\[").replace("]", r"\]").replace("{", r"\{").replace("}", r"\}")
        escaped_prompt = re.sub(r'([%])', r'\\\1', escaped_prompt)
        widgets.append(Static(f"User Direction: {escaped_prompt}"))
    
    # Ideation Rounds (collapsible, collapsed by default)
    if feature.ideation_summaries:
        import re
        rounds_content = ""
        for i, summary in enumerate(feature.ideation_summaries):
            escaped = summary.replace("[", r"\[").replace("]", r"\]").replace("{", r"\{").replace("}", r"\}")
            escaped = re.sub(r'([%])', r'\\\1', escaped)
            rounds_content += f"## Round {i+1}\n{escaped}\n\n"
        widgets.append(Collapsible(Static(rounds_content), title=f"Ideation Rounds ({len(feature.ideation_summaries)})", collapsed=True))
    
    # Done Script (always visible if set)
    if feature.done_script:
        from scripts import load_scripts
        scripts = load_scripts(Config().mad_dir)
        script = next((s for s in scripts if s.id == feature.done_script), None)
        label = script.label if script else f"[MISSING] {feature.done_script}"
        widgets.append(Static(f"Done Script: {label}"))
    
    # History (collapsible, expanded by default)
    if feature.history:
        # Escape all markup characters for safe display
        import re
        escaped = feature.history.replace("[", r"\[").replace("]", r"\]").replace("{", r"\{").replace("}", r"\}")
        escaped = re.sub(r'([%])', r'\\\1', escaped)
        widgets.append(Collapsible(Static(escaped), title="History", collapsed=False))
    else:
        widgets.append(Collapsible(Static("[dim]No history provided[/dim]"), title="History", collapsed=False))
    
    # Plan (collapsible, collapsed by default)
    if feature.plan:
        import re
        escaped = feature.plan.replace("[", r"\[").replace("]", r"\]").replace("{", r"\{").replace("}", r"\}")
        escaped = re.sub(r'([%])', r'\\\1', escaped)
        widgets.append(Collapsible(Static(escaped), title="Plan", collapsed=True))
    else:
        widgets.append(Collapsible(Static("[dim]No plan provided[/dim]"), title="Plan", collapsed=True))
    
    # Plan Exploration Summary (collapsible, collapsed by default)
    if feature.plan_exploration_summary:
        import re
        escaped = feature.plan_exploration_summary.replace("[", r"\[").replace("]", r"\]").replace("{", r"\{").replace("}", r"\}")
        escaped = re.sub(r'([%])', r'\\\1', escaped)
        widgets.append(Collapsible(Static(escaped), title="Plan Exploration Summary", collapsed=True))
    
    # Implementation Spec (collapsible, collapsed by default)
    if feature.impl_spec:
        formatted = _format_json_text(feature.impl_spec)
        widgets.append(Collapsible(Static(_escape_markup(formatted)), title="Implementation Spec", collapsed=True))
    else:
        widgets.append(Collapsible(Static("[dim]No implementation spec provided[/dim]"), title="Implementation Spec", collapsed=True))
    
    # Test Spec (collapsible, collapsed by default)
    if feature.test_spec:
        formatted = _format_json_text(feature.test_spec)
        widgets.append(Collapsible(Static(_escape_markup(formatted)), title="Test Spec", collapsed=True))
    else:
        widgets.append(Collapsible(Static("[dim]No test spec provided[/dim]"), title="Test Spec", collapsed=True))
    
    # Test Results (collapsible, collapsed by default)
    test_results = feature.test_results
    if test_results:
        ts = test_results.get("ts", "")
        verdict = test_results.get("verdict", "")
        results = test_results.get("results", {})
        feedback = test_results.get("feedback", "")
        
        result_lines = []
        if ts:
            result_lines.append(f"Timestamp: {ts}")
        if verdict:
            result_lines.append(f"Verdict: {verdict}")
        
        if results:
            passed = results.get("passed", 0)
            failed = results.get("failed", 0)
            errors = results.get("errors", 0)
            result_lines.append(f"Passed: {passed}  Failed: {failed}  Errors: {errors}")
        
        if feedback:
            result_lines.append(f"Feedback: {feedback}")
        
        result_text = "\n".join(result_lines)
        widgets.append(Collapsible(Static(_escape_markup(result_text)), title="Test Results", collapsed=True))
    
    # Implementation Notes (collapsible, collapsed by default)
    if feature.impl_notes:
        formatted = _format_json_text(feature.impl_notes)
        widgets.append(Collapsible(Static(_escape_markup(formatted)), title="Implementation Notes", collapsed=True))
    else:
        widgets.append(Collapsible(Static("[dim]No implementation notes provided[/dim]"), title="Implementation Notes", collapsed=True))
    
    # Review History (collapsible, collapsed by default)
    plan_reviews = feature.plan_reviews
    impl_reviews = feature.impl_reviews
    if plan_reviews or impl_reviews:
        review_lines = []
        if plan_reviews:
            review_lines.append("Plan Reviews:")
            for r in plan_reviews:
                summary = r.get('summary', '') or r.get('feedback', '') or ''
                primary = _escape_markup(summary)[:200]
                line = f"  {r.get('ts', '')} | {r.get('verdict', '')} | {primary}"
                review_lines.append(line)
                fb = r.get('feedback', '') or ''
                if fb and summary and fb != summary:
                    review_lines.append(f"    {_escape_markup(fb)[:200]}")
        if impl_reviews:
            review_lines.append("Impl Reviews:")
            for r in impl_reviews:
                summary = r.get('summary', '') or r.get('feedback', '') or ''
                primary = _escape_markup(summary)[:200]
                line = f"  {r.get('ts', '')} | {r.get('verdict', '')} | {primary}"
                review_lines.append(line)
                fb = r.get('feedback', '') or ''
                if fb and summary and fb != summary:
                    review_lines.append(f"    {_escape_markup(fb)[:200]}")
        widgets.append(Collapsible(Static("\n".join(review_lines)), title=f"Review History ({len(plan_reviews) + len(impl_reviews)})", collapsed=True))

    # Questions (collapsible, collapsed by default)
    questions = feature.questions
    if questions:
        questions_content = []
        for i, q in enumerate(questions):
            q_text = q.get("question", "")
            a_text = q.get("answer", "")
            if a_text:
                questions_content.append(f"Q{i+1}: {_escape_markup(q_text)}\n  A: {_escape_markup(a_text)}")
            else:
                questions_content.append(f"Q{i+1}: {_escape_markup(q_text)} (unanswered)")
        questions_text = "\n\n".join(questions_content)
        widgets.append(Collapsible(Static(_escape_markup(questions_text)), title=f"Questions ({len(questions)})", collapsed=True))
    else:
        widgets.append(Collapsible(Static("[dim]No questions provided[/dim]"), title="Questions", collapsed=True))
    
    return widgets


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

    _spinner_frames = "\u29fe\u29fd\u29fb\u28ff\u287f\u29df\u29ef\u29f7"

    class Selected(Message):
        """Posted when this feature item is selected."""

        def __init__(self, feature: FeatureFile) -> None:
            self.feature = feature
            super().__init__()

    def __init__(self, feature: FeatureFile, is_selected: bool = False, is_active: bool = False) -> None:
        self.feature = feature
        self._is_selected = is_selected
        self.is_active = is_active
        self._frame = 0
        super().__init__(self._render_label())
        self.can_focus = True
        classes = "feature-item"
        if is_selected:
            classes += " --selected"
        if is_active:
            classes += " --active"
        self.set_classes(classes)

    def _render_label(self) -> str:
        display_name = self.feature.slug
        if len(display_name) > 24:
            display_name = display_name[:22] + ".."
        if self.is_active:
            return f" [cyan]{self._spinner_frames[self._frame]}[/cyan]  {display_name}"
        indicator = " << " if self._is_selected else ""
        return f"    {display_name}{indicator}"

    def on_mount(self) -> None:
        if self.is_active:
            self.set_interval(0.1, self._advance_spinner)

    def _advance_spinner(self) -> None:
        self._frame = (self._frame + 1) % len(self._spinner_frames)
        self.update(self._render_label())

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


class AnswerQuestionsModal(ModalScreen[bool | None]):
    """Modal to answer questions raised by the planning agent."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, feature: FeatureFile) -> None:
        self.feature = feature
        self.questions = [q for q in feature.questions if not q.get("answer")]
        self.original_indices = [i for i, q in enumerate(feature.questions) if not q.get("answer")]
        super().__init__()

    def on_key(self, event) -> None:
        """Handle tab to move between inputs."""
        if event.key == "tab":
            self._move_focus_next()
            event.prevent_default()
        elif event.key == "shift+tab":
            self._move_focus_prev()
            event.prevent_default()
        elif event.key == "escape":
            self.dismiss(None)

    def _move_focus_next(self) -> None:
        """Move focus to next input."""
        from textual.widgets import Input
        inputs = list(self.query(Input))
        if not inputs:
            return
        current = self.focused
        if current in inputs:
            idx = inputs.index(current)  # type: ignore[arg-type]
            if idx + 1 < len(inputs):
                inputs[idx + 1].focus()
                return
        # First input or no focus
        if inputs:
            inputs[0].focus()

    def _move_focus_prev(self) -> None:
        """Move focus to previous input."""
        from textual.widgets import Input
        inputs = list(self.query(Input))
        if not inputs:
            return
        current = self.focused
        if current in inputs:
            idx = inputs.index(current)  # type: ignore[arg-type]
            if idx > 0:
                inputs[idx - 1].focus()
                return
        # Last input
        if inputs:
            inputs[-1].focus()

    CSS = """
    AnswerQuestionsModal {
        align: center middle;
    }
    #questions-dialog {
        width: 70;
        height: 80%;
        max-height: 80%;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #questions-dialog VerticalLayout {
        height: 100%;
    }
    #questions-dialog VerticalScroll {
        height: 100%;
    }
    #questions-dialog Label {
        width: 100%;
        text-wrap: wrap;
    }
    #questions-dialog Input {
        margin: 1 0;
    }
    #questions-buttons {
        height: 3;
        dock: bottom;
    }
    #questions-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="questions-dialog"):
            with VerticalScroll(id="questions-content"):
                yield Label(f"[bold]Questions for: {self.feature.title}[/bold]\n")
                
                for i, q in enumerate(self.questions):
                    yield Label(f"\nQ{i+1}: {q.get('question', '')}", id=f"q-{i}")
                    yield Input(
                        value=q.get('answer', ''),
                        id=f"answer-{i}",
                        placeholder="Type your answer..."
                    )
                
                yield Label("")
            with Horizontal(id="questions-buttons"):
                yield Button("Cancel", variant="default", id="cancel")
                yield Button("Save & Continue", variant="primary", id="save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "save":
            # Save all answers using original indices
            for i, orig_idx in enumerate(self.original_indices):
                input_widget = self.query_one(f"#answer-{i}", Input)
                self.feature.answer_question(orig_idx, input_widget.value)
            
            # Move back to plan-inbox to continue pipeline
            self.feature.move_to_stage("plan-inbox")
            self.feature.save()
            self.dismiss(True)


class NewFeatureModal(ModalScreen[tuple[str, str, str, str, str, bool] | None]):
    """Modal to create a new feature -- collects title, description, board, done_script, and requires_human_approval."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def on_key(self, event) -> None:
        """Handle tab to move between inputs."""
        if event.key == "tab":
            self._move_focus_next()
            event.prevent_default()
        elif event.key == "shift+tab":
            self._move_focus_prev()
            event.prevent_default()

    def _move_focus_next(self) -> None:
        """Move focus to next input."""
        from textual.widgets import Input, Select
        inputs = list(self.query(Input)) + list(self.query(Select))
        if not inputs:
            return
        current = self.focused
        if current in inputs:
            idx = inputs.index(current)
            if idx + 1 < len(inputs):
                inputs[idx + 1].focus()
                return
        if inputs:
            inputs[0].focus()

    def _move_focus_prev(self) -> None:
        """Move focus to previous input."""
        from textual.widgets import Input, Select
        inputs = list(self.query(Input)) + list(self.query(Select))
        if not inputs:
            return
        current = self.focused
        if current in inputs:
            idx = inputs.index(current)
            if idx > 0:
                inputs[idx - 1].focus()
                return
        if inputs:
            inputs[-1].focus()

    CSS = """
    NewFeatureModal {
        align: center middle;
    }
    #new-dialog {
        width: 70;
        height: auto;
        max-height: 36;
        overflow-y: auto;
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

    def __init__(self, boards: list[str], default_board: str, scripts: list = None) -> None:
        self.boards = boards
        self.default_board = default_board
        self.scripts = scripts or []
        super().__init__()

    def compose(self) -> ComposeResult:
        options = [(b, b) for b in self.boards]
        type_options = [('Feature', 'feature'), ('Bug', 'bug')]
        script_options = [("(none)", "")] + [(s.label, s.id) for s in self.scripts]
        with Vertical(id="new-dialog"):
            yield Label("New Item")
            yield Label("Board:")
            yield Select(options, value=self.default_board, id="new-board")
            yield Label("Type:")
            yield Select(type_options, value='feature', id="new-type")
            yield Label("Title (required):")
            yield Input(placeholder="Item title", id="new-title")
            yield Label("Description (optional):")
            yield Input(placeholder="Brief description", id="new-desc")
            yield Label("Done Script (optional):")
            yield Select(script_options, value="", id="new-done-script")
            yield Label("Require Human Approval:")
            yield Select([("No", "no"), ("Yes", "yes")], value="no", id="new-requires-approval")
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
        item_type = self.query_one("#new-type", Select).value
        done_script_select = self.query_one("#new-done-script", Select)
        done_script = done_script_select.value if done_script_select.value else ""
        requires_approval_select = self.query_one("#new-requires-approval", Select)
        requires_approval = requires_approval_select.value == "yes"
        self.dismiss((str(board), title, desc, done_script, str(item_type), requires_approval))

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditDoneScriptModal(ModalScreen[str | None]):
    """Modal to edit the done script for a feature."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    EditDoneScriptModal {
        align: center middle;
    }
    #edit-script-dialog {
        width: 50;
        height: auto;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #edit-script-dialog Select {
        margin: 1 0;
    }
    """

    def __init__(self, current_script: str, scripts: list) -> None:
        self.current_script = current_script or ""
        self.scripts = scripts or []
        super().__init__()

    def compose(self) -> ComposeResult:
        script_options = [("(none)", "")] + [(s.label, s.id) for s in self.scripts]
        current_value = self.current_script if self.current_script else ""
        with Vertical(id="edit-script-dialog"):
            yield Label("Edit Done Script")
            yield Label("Select script to run when item is completed:")
            yield Select(script_options, value=current_value, id="edit-script-select")
            with Horizontal(id="edit-script-buttons"):
                yield Button("Save", variant="primary", id="edit-script-save")
                yield Button("Cancel", variant="default", id="edit-script-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-script-save":
            script_select = self.query_one("#edit-script-select", Select)
            value = script_select.value if script_select.value else ""
            self.dismiss(value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class MoveFeatureModal(ModalScreen[str | None]):
    """Modal to move a feature to a different stage."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    MoveFeatureModal {
        align: center middle;
    }
    #move-dialog {
        width: 50;
        height: auto;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #move-dialog Select {
        margin: 1 0;
    }
    #move-buttons {
        margin-top: 1;
        height: 3;
    }
    #move-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, current_stage: str) -> None:
        self.current_stage = current_stage
        super().__init__()

    def compose(self) -> ComposeResult:
        from state import STAGES
        options = [(s, s.replace("-", " ").title()) for s in STAGES]
        with Vertical(id="move-dialog"):
            yield Label("Move to Stage:")
            # Don't set value in constructor - set it after mount
            yield Select(options, id="move-stage")
            with Horizontal(id="move-buttons"):
                yield Button("Move", variant="primary", id="move-go")
                yield Button("Cancel", variant="default", id="move-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "move-go":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        from state import STAGES
        select = self.query_one("#move-stage", Select)
        label = select.value
        if label is Select.BLANK:
            self.dismiss(self.current_stage)
            return
        
        # Build reverse lookup: label -> stage
        label_to_stage = {s.replace("-", " ").title(): s for s in STAGES}
        stage = label_to_stage.get(str(label), str(label))
        
        if stage not in STAGES:
            stage = self.current_stage
        self.dismiss(stage)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RunScriptModal(ModalScreen[Optional[tuple[str, str]]]):
    """Modal to select and run a script."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    RunScriptModal {
        align: center middle;
    }
    #script-dialog {
        width: 60;
        height: auto;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #script-dialog Select {
        margin: 1 0;
    }
    #script-dialog TextArea {
        margin: 1 0;
        height: 3;
    }
    #script-buttons {
        margin-top: 1;
        height: 3;
    }
    #script-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, scripts: list[ScriptConfig]):
        self.scripts = scripts
        super().__init__()

    def compose(self) -> ComposeResult:
        options = [(s.label, s.id) for s in self.scripts]
        with Vertical(id="script-dialog"):
            yield Label("Run Script:")
            yield Select(options, prompt="Select script", id="script-select")
            yield TextArea(placeholder="Additional context for the agent (optional)", id="script-context")
            with Horizontal(id="script-buttons"):
                yield Button("Run", variant="primary", id="script-run")
                yield Button("Cancel", variant="default", id="script-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "script-run":
            script_select = self.query_one("#script-select", Select)
            script_id = script_select.value
            if script_id is Select.BLANK or script_id is None:
                self.app.notify("Please select a script", severity="warning")
                return
            context = self.query_one("#script-context", TextArea).text or ""
            selected = next((s for s in self.scripts if s.id == script_id), None)
            if selected:
                if selected.confirm:
                    self.app.push_screen(
                        ConfirmModal(f"Run '{selected.label}'?"),
                        callback=lambda ok: self._handle_confirm(ok, script_id, context) if ok else None
                    )
                else:
                    self.dismiss((script_id, context))
        else:
            self.dismiss(None)

    def _handle_confirm(self, ok: bool, script_id: str, context: str) -> None:
        if ok:
            self.dismiss((script_id, context))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Simple confirmation modal."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ConfirmModal {
        align: center middle;
    }
    #confirm-dialog {
        width: 40;
        height: auto;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #confirm-buttons {
        margin-top: 1;
        height: 3;
    }
    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(self.message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", variant="primary", id="confirm-yes")
                yield Button("No", variant="default", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class UnifiedEditModal(ModalScreen[dict | None]):
    """Modal to edit feature fields - shows all fields for IDEAS stage, only script for others."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    UnifiedEditModal {
        align: center middle;
    }
    #unified-edit-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        overflow-y: auto;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #unified-edit-dialog VerticalScroll {
    }
    #unified-edit-dialog Label {
        width: 100%;
        text-wrap: wrap;
        margin-top: 1;
    }
    #unified-edit-dialog Input {
        margin: 1 0;
    }
    #unified-edit-dialog Select {
        margin: 1 0;
    }
    #unified-edit-dialog TextArea {
        margin: 1 0;
        height: 4;
    }
    #unified-edit-buttons {
        margin-top: 1;
        height: 3;
    }
    #unified-edit-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, feature_data: dict, current_stage: str, scripts: list) -> None:
        self.feature_data = feature_data
        self.current_stage = current_stage
        self.scripts = scripts or []
        super().__init__()

    def compose(self) -> ComposeResult:
        is_early_stage = self.current_stage in ("ideas", "ideating", "plan-inbox")
        
        with Vertical(id="unified-edit-dialog"):
            with VerticalScroll(id="unified-edit-content"):
                yield Label(f"[bold]Edit Feature[/bold] - {self.current_stage.replace('-', ' ').title()}")
                
                yield Label("Title:")
                yield Input(
                    value=self.feature_data.get("title", ""),
                    placeholder="Feature title",
                    id="title-input"
                )
                
                if is_early_stage:
                    yield Label("Type:")
                    type_options = [("Feature", "feature"), ("Bug", "bug")]
                    current_type = self.feature_data.get("item_type", "feature")
                    yield Select(type_options, value=current_type, id="type-select")
                
                yield Label("Description:")
                description = self.feature_data.get("description", "")
                yield TextArea(
                    description,
                    placeholder="Feature description",
                    id="description-area"
                )
                
                yield Label("Done Script:")
                script_options = [("(none)", "")] + [(s.label, s.id) for s in self.scripts]
                current_script = self.feature_data.get("done_script", "")
                yield Select(script_options, value=current_script, id="script-select")
                yield Label("Require Human Approval:")
                current_approval = "yes" if self.feature_data.get("requires_human_approval", False) else "no"
                yield Select([("No", "no"), ("Yes", "yes")], value=current_approval, id="approval-select")
                
                yield Label("Ideation Prompt (User Direction):")
                yield TextArea(
                    self.feature_data.get("ideation_prompt", ""),
                    placeholder="Optional: provide direction for ideation debates",
                    id="ideation-prompt-area"
                )
            
            with Horizontal(id="unified-edit-buttons"):
                yield Button("Save", variant="primary", id="edit-save")
                yield Button("Cancel", variant="default", id="edit-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-save":
            self._save()
        else:
            self.dismiss(None)

    def _save(self) -> None:
        is_early_stage = self.current_stage in ("ideas", "ideating", "plan-inbox")
        result = {}
        
        title_input = self.query_one("#title-input", Input)
        result["title"] = title_input.value.strip()
        
        if is_early_stage:
            type_select = self.query_one("#type-select", Select)
            result["item_type"] = type_select.value if type_select.value else "feature"
        
        desc_area = self.query_one("#description-area", TextArea)
        result["description"] = desc_area.text
        
        script_select = self.query_one("#script-select", Select)
        result["done_script"] = script_select.value if script_select.value else ""
        
        approval_select = self.query_one("#approval-select", Select)
        result["requires_human_approval"] = approval_select.value == "yes"
        
        ideation_prompt_area = self.query_one("#ideation-prompt-area", TextArea)
        result["ideation_prompt"] = ideation_prompt_area.text
        
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------


class SettingsPane(Static):
    """Settings panel for configuring agents and pipeline options."""

    CSS = """
    SettingsPane {
        padding: 1;
    }
    .settings-row {
        height: 3;
    }
    .settings-row Label {
        width: 16;
    }
    .settings-row Select {
        width: 20;
    }
    .settings-row Input {
        width: 1fr;
        margin-left: 1;
    }
    """

    class SettingsChanged(Message):
        """Posted when settings are modified."""

        def __init__(self) -> None:
            super().__init__()

    def __init__(self, config: "Config", **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        yield Label("[bold]Pipeline Settings[/bold]\n", id="settings-title")
        
        # Default agent selector
        agents = list(BUILTIN_AGENTS.keys())
        current_default = self._config.current_agent_name
        agent_options = [(a, a) for a in agents]
        
        yield Label("[bold]Default Agent[/bold]", id="default-label")
        yield Select(
            agent_options,
            value=current_default,
            id="default-agent-select"
        )
        
        yield Label("[bold]Phase Agent Assignments[/bold]", id="phases-label")
        
        # Phase assignments - horizontal rows
        agent_for_phase = self._config.agent_for_phase
        for phase_key, phase_label in PIPELINE_PHASES:
            phase_cfg = agent_for_phase.get(phase_key)
            current_agent = phase_cfg.agent if phase_cfg else current_default
            current_model = phase_cfg.model if phase_cfg else ""
            phase_options = [("default", "default")] + [(a, a) for a in BUILTIN_AGENTS]
            model_options = [(m, m) for m in self._config.get_models_for_agent(current_agent)]
            with Horizontal(classes="settings-row"):
                yield Label(f"{phase_label}:", id=f"label-{phase_key}")
                yield Select(phase_options, value=current_agent, id=f"phase-{phase_key}")
                yield Select(model_options, value=current_model if current_model else Select.NULL, id=f"model-{phase_key}")
        
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
            new_phase_mapping = {}
            for phase_key, _ in PIPELINE_PHASES:
                agent = self.query_one(f"#phase-{phase_key}", Select).value
                model_select = self.query_one(f"#model-{phase_key}", Select)
                model_value = model_select.value
                model = model_value if model_value and model_value != Select.NULL else None
                if agent and agent != Select.BLANK and isinstance(agent, str):
                    if model:
                        new_phase_mapping[phase_key] = {"agent": agent, "model": model}
                    else:
                        new_phase_mapping[phase_key] = agent
            
            # Save phase assignments
            self._config.set_agent_for_phases(new_phase_mapping)
            
            self.post_message(self.SettingsChanged())
            self.notify("Settings saved!", severity="information")
        except Exception as e:
            self.notify(f"Failed to save: {e}", severity="error")

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle agent dropdown changes to update model options."""
        try:
            if event.select.id and event.select.id.startswith("phase-"):
                phase_key = event.select.id.replace("phase-", "")
                new_agent = event.select.value
                if new_agent and new_agent != "" and isinstance(new_agent, str):
                    if new_agent == "default":
                        agent_name = "default"
                    else:
                        agent_name = new_agent
                else:
                    agent_name = "default"
                model_select = self.query_one(f"#model-{phase_key}", Select)
                model_options = [(m, m) for m in self._config.get_models_for_agent(agent_name)]
                model_select.set_options(model_options)
                model_select.value = Select.NULL
        except Exception as e:
            self.notify(f"Failed to update model options: {e}", severity="warning")


# ---------------------------------------------------------------------------
# Scripts panel
# ---------------------------------------------------------------------------


class ScriptsPane(Static):
    """Scripts panel for viewing and running scripts."""

    CSS = """
    ScriptsPane {
        padding: 1;
    }
    #scripts-title {
        margin-bottom: 1;
    }
    #scripts-list {
        height: 30;
        border: solid $panel;
        margin-bottom: 1;
    }
    #context-label {
        margin-top: 1;
    }
    #context-area {
        height: 6;
        margin-bottom: 1;
    }
    #scripts-buttons {
        height: 3;
    }
    #scripts-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, scripts: list, script_status: Optional["ScriptStatus"], **kwargs) -> None:
        super().__init__(**kwargs)
        self._scripts = scripts
        self._script_status = script_status

    def compose(self) -> ComposeResult:
        yield Label("[bold]Scripts[/bold]\n", id="scripts-title")
        
        yield Label("Select a script to run:", id="scripts-list-label")
        yield Select(
            [(s.label, str(i)) for i, s in enumerate(self._scripts)],
            prompt="Select a script...",
            id="script-select"
        )
        
        yield Label("Additional context (optional):", id="context-label")
        yield TextArea(placeholder="Context to pass to the script", id="context-area")
        
        with Horizontal(id="scripts-buttons"):
            yield Button("Run Script", variant="primary", id="run-script-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-script-btn":
            self._run_selected_script()

    def _run_selected_script(self) -> None:
        """Run the selected script."""
        try:
            script_select = self.query_one("#script-select", Select)
            selected_value = script_select.value
            
            if selected_value is Select.BLANK or selected_value is None:
                self.notify("Please select a script first", severity="warning")
                return
            
            try:
                script_index = int(str(selected_value))
            except (ValueError, TypeError):
                self.notify("Invalid script selection", severity="warning")
                return
            
            if script_index < 0 or script_index >= len(self._scripts):
                self.notify("Invalid script selection", severity="warning")
                return
            
            script = self._scripts[script_index]
            context_area = self.query_one("#context-area", TextArea)
            context = context_area.text or ""
            
            if script.confirm:
                self.app.push_screen(
                    ConfirmModal(f"Run '{script.label}'?"),
                    callback=lambda ok: self._execute_script(script.id, context) if ok else None
                )
            else:
                self._execute_script(script.id, context)
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    def _execute_script(self, script_id: str, context: str) -> None:
        """Execute the script via the parent app."""
        app = self.app
        if hasattr(app, '_run_script_async'):
            app._run_script_async(script_id, context)
            self.notify(f"Running script: {script_id}", severity="information")


# Number of stdout tail lines to display in the status widget
_STATUS_TAIL_LINES = 8


class AgentStatusWidget(Static):
    """Always-visible panel showing real-time agent progress at the bottom of the right pane.
    
    Can display either a single status or dual statuses (plan + implement).
    """

    REFRESH_INTERVAL = 1.0  # seconds between re-renders

    def __init__(
        self,
        status: AgentStatus | None = None,
        plan_status: AgentStatus | None = None,
        implement_status: AgentStatus | None = None,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._status = status
        self._plan_status = plan_status
        self._implement_status = implement_status

    def on_mount(self) -> None:
        self.set_interval(self.REFRESH_INTERVAL, self.refresh)

    def set_dual_statuses(self, plan_status: AgentStatus, implement_status: AgentStatus) -> None:
        """Set dual status objects for plan and implement pipelines."""
        self._plan_status = plan_status
        self._implement_status = implement_status
        self._status = None

    def render(self) -> str:
        # If we have plan_status only, show plan
        if self._plan_status is not None and self._implement_status is None:
            return self._render_single_with_label(self._plan_status, "PLAN")
        # If we have impl_status only, show impl
        if self._implement_status is not None and self._plan_status is None:
            return self._render_single_with_label(self._implement_status, "IMPL")
        # If we have dual statuses, show both
        if self._plan_status or self._implement_status:
            return self._render_dual()
        # Otherwise show single status
        if self._status:
            return self._render_single(self._status)
        return "[dim]● idle[/dim]"

    def _render_single_with_label(self, status: AgentStatus, label: str) -> str:
        """Render a single status with a label (PLAN or IMPL)."""
        if not status.running:
            # Check auto mode
            app = self.app
            if label == "PLAN":
                auto_on = getattr(app, 'auto_plan_enabled', False)
            else:
                auto_on = getattr(app, 'auto_implement_enabled', False)
            if auto_on:
                return f"[dim]○ {label} auto-ready[/dim]"
            return f"[dim]○ {label} idle[/dim]"

        # Elapsed time
        if status.started_at:
            elapsed = time.time() - status.started_at
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        else:
            elapsed_str = "0s"

        stale_str = ""
        if status.last_activity_at:
            idle_time = time.time() - status.last_activity_at
            if idle_time > 150:
                idle_mins = int(idle_time // 60)
                idle_secs = int(idle_time % 60)
                stale_str = f" [yellow][STALE {idle_mins}m{idle_secs}s][/yellow]"

        return (
            f"[bold cyan]●[/bold cyan] {label}: [bold]{status.agent}[/bold] "
            f"[blue]{status.feature_slug}[/blue] "
            f"[yellow]{status.phase}[/yellow] "
            f"[dim]{elapsed_str}[/dim]{stale_str}"
        )

    def _render_single(self, status: AgentStatus) -> str:
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

        stale_str = ""
        if status.last_activity_at:
            idle_time = time.time() - status.last_activity_at
            if idle_time > 150:
                idle_mins = int(idle_time // 60)
                idle_secs = int(idle_time % 60)
                stale_str = f" [yellow][STALE {idle_mins}m{idle_secs}s][/yellow]"

        parts = [
            f"[bold cyan]●[/bold cyan] [bold]{status.agent}[/bold] "
            f"[blue]{status.feature_slug}[/blue] "
            f"[yellow]{status.phase}[/yellow] "
            f"[dim]{elapsed_str}[/dim]{stale_str}",
        ]

        tail = status.lines[-_STATUS_TAIL_LINES:] if status.lines else []
        for line in tail:
            safe_line = line.replace("[", r"\[")
            parts.append(f"[dim]{safe_line}[/dim]")

        return "\n".join(parts)

    def _render_dual(self) -> str:
        """Render two status displays side by side or stacked."""
        # Get auto flags from app
        app = self.app
        auto_plan = getattr(app, 'auto_plan_enabled', False)
        auto_impl = getattr(app, 'auto_implement_enabled', False)
        
        parts = []
        
        # Plan status
        if self._plan_status and self._plan_status.running:
            parts.append(self._render_status_line(self._plan_status, "PLAN"))
        elif auto_plan:
            parts.append("[dim]○ PLAN auto-ready[/dim]")
        else:
            parts.append("[dim]○ PLAN idle[/dim]")
        
        # Implement status
        if self._implement_status and self._implement_status.running:
            parts.append(self._render_status_line(self._implement_status, "IMPL"))
        elif auto_impl:
            parts.append("[dim]○ IMPL auto-ready[/dim]")
        else:
            parts.append("[dim]○ IMPL idle[/dim]")
        
        return "  |  ".join(parts)

    def _render_status_line(self, status: AgentStatus, label: str) -> str:
        if status.started_at:
            elapsed = time.time() - status.started_at
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            elapsed_str = f"{mins}m{secs}s" if mins else f"{secs}s"
        else:
            elapsed_str = "0s"
        
        stale_str = ""
        if status.last_activity_at:
            idle_time = time.time() - status.last_activity_at
            if idle_time > 150:
                idle_mins = int(idle_time // 60)
                idle_secs = int(idle_time % 60)
                stale_str = f" [yellow][STALE {idle_mins}m{idle_secs}s][/yellow]"
        
        return (
            f"[bold cyan]●[/bold cyan] {label}: [bold]{status.agent}[/bold] "
            f"[blue]{status.feature_slug}[/blue] "
            f"[yellow]{status.phase}[/yellow] [dim]{elapsed_str}[/dim]{stale_str}"
        )


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


class PipelineApp(App):
    """MAD Pipeline TUI -- interactive kanban dashboard."""

    TITLE = "MAD Pipeline"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("q", "answer_questions", "Answer"),
        Binding("i", "interactive", "Interact"),
        Binding("a", "approve", "Approve"),
        Binding("r", "review", "Review"),
        Binding("ctrl+r", "restart", "Restart"),
        Binding("x", "reject", "Reject"),
        Binding("u", "restore", "Restore"),
        Binding("p", "promote", "Promote"),
        Binding("s", "start", "Start"),
        Binding("d", "debate", "Debate"),
        Binding("ctrl+a", "toggle_auto_plan", "Auto-Plan"),
        Binding("ctrl+d", "toggle_auto_implement", "Auto-Impl"),
        Binding("ctrl+s", "stop_auto", "Stop"),
        Binding("m", "move", "Move"),
        Binding("n", "new_feature", "New"),
        Binding("b", "new_board", "New Board"),
        Binding("f", "refresh", "Refresh"),
        Binding("tab", "toggle_focus", "Switch"),
        Binding(";", "go_to_settings", "Settings"),
        Binding("up", "focus_previous", "Up"),
        Binding("down", "focus_next", "Down"),
        Binding("e", "unified_edit", "Edit", show=False),
        Binding("k", "kill_agent", "Kill Agent", show=False),
        Binding("ctrl+k", "restart_agent", "Restart Agent", show=False),
    ]

    # Stage -> list of available actions (key, action, label)
    STAGE_ACTIONS = {
        "ideas": [("m", "move", "Move"), ("d", "debate", "Debate"), ("i", "interactive", "Interact"), ("a", "approve", "To Plan"), ("x", "reject", "Reject"), ("e", "unified_edit", "Edit")],
        "ideating": [("m", "move", "Move"), ("e", "unified_edit", "Edit")],
        "plan-inbox": [("m", "move", "Move"), ("x", "reject", "Reject"), ("s", "start", "Start"), ("ctrl+r", "restart", "Restart"), ("e", "unified_edit", "Edit")],
        "reviewing-plan": [("m", "move", "Move"), ("s", "start", "Start"), ("ctrl+r", "restart", "Restart"), ("e", "unified_edit", "Edit")],
        "requested-input": [("m", "move", "Move"), ("q", "answer_questions", "Answer"), ("s", "start", "Start"), ("e", "unified_edit", "Edit")],
        "awaiting-human-approval": [("m", "move", "Move"), ("a", "approve", "Approve"), ("x", "reject", "Reject"), ("e", "unified_edit", "Edit")],
        "approved": [("m", "move", "Move"), ("x", "reject", "Reject"), ("s", "start", "Start"), ("ctrl+r", "restart", "Restart"), ("e", "unified_edit", "Edit")],
        "spec-writing": [("m", "move", "Move"), ("x", "reject", "Reject"), ("s", "start", "Start"), ("ctrl+r", "restart", "Restart"), ("e", "unified_edit", "Edit")],
        "implementing": [("m", "move", "Move"), ("x", "reject", "Reject"), ("s", "start", "Start"), ("ctrl+r", "restart", "Restart"), ("e", "unified_edit", "Edit")],
        "testing": [("m", "move", "Move"), ("x", "reject", "Reject"), ("ctrl+r", "restart", "Restart"), ("e", "unified_edit", "Edit")],
        "review": [("m", "move", "Move"), ("a", "approve", "Approve"), ("r", "review", "Review"), ("x", "reject", "Reject"), ("ctrl+r", "restart", "Restart"), ("e", "unified_edit", "Edit")],
        "final-human-approval": [("m", "move", "Move"), ("a", "approve", "Done"), ("x", "reject", "Reject"), ("e", "unified_edit", "Edit")],
        "done": [("m", "move", "Move"), ("u", "restore", "Restore"), ("e", "unified_edit", "Edit")],
        "rejected": [("m", "move", "Move"), ("u", "restore", "Restore"), ("e", "unified_edit", "Edit")],
    }

    # Global hotkeys that are always available
    GLOBAL_BINDINGS = [
        ("n", "new_feature", "New"),
        ("b", "new_board", "New Board"),
        ("f", "refresh", "Refresh"),
        ("ctrl+a", "toggle_auto_plan", "Auto-Plan"),
        ("ctrl+d", "toggle_auto_implement", "Auto-Impl"),
    ]

    def get_contextual_bindings(self) -> list[tuple]:
        """Get bindings - global + stage-specific actions."""
        # Always include global hotkeys
        bindings = list(self.GLOBAL_BINDINGS)
        
        # Show kill/restart when an agent is running
        if self._plan_running or self._implement_running:
            bindings.append(("k", "kill_agent", "Kill"))
            bindings.append(("ctrl+k", "restart_agent", "Restart"))
        
        # Show kill when a script is running
        if self._script_status and self._script_status.running:
            bindings.append(("k", "kill_script", "Kill Script"))
        
        if not self.selected_feature:
            return bindings + [("ctrl+q", "quit", "Quit")]
        
        # Add stage-specific actions
        stage = self.selected_feature.current_stage
        stage_bindings = self.STAGE_ACTIONS.get(stage, [])
        
        # Add stage actions in the middle, Quit always last
        return bindings + stage_bindings + [("ctrl+q", "quit", "Quit")]

    def on_screen_resume(self) -> None:
        """Fix terminal input on tmux reattach."""
        # Only fix terminal if we've been running for at least 2 seconds (not on first startup)
        if time.time() - self._start_time >= 2.0:
            logger.info("ScreenResume event - fixing terminal input")
            self._fix_terminal_input()
        else:
            logger.info("ScreenResume event - skipping (not initialized yet)")
        self.refresh(repaint=True, layout=True)

    def on_resize(self, event) -> None:
        """Fix terminal input on resize/reattach."""
        # Only fix terminal if we've been running for at least 2 seconds (not on first startup)
        if time.time() - self._start_time >= 2.0:
            logger.info(f"Resize event: {event.size} - fixing terminal input")
            self._fix_terminal_input()
        else:
            logger.info(f"Resize event: {event.size} - skipping (not initialized yet)")
        self.refresh(repaint=True, layout=True)

    def _fix_terminal_input(self) -> None:
        """Fix terminal input settings on tmux reattach."""
        import termios
        import tty
        try:
            # Get current terminal settings
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            # Set to sane defaults
            new_settings = termios.tcgetattr(fd)
            new_settings[0] = termios.ICRNL | termios.INLCR | termios.IXON | termios.IXANY | termios.IXOFF  # iflag
            new_settings[1] = termios.OPOST | termios.ONLCR  # oflag
            new_settings[2] = termios.CS8 | termios.CREAD  # cflag
            new_settings[3] = termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG | termios.IEXTEN  # lflag
            new_settings[6][termios.VMIN] = 1
            new_settings[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, new_settings)
            logger.info("termios settings applied")
        except Exception as e:
            logger.warning(f"termios fix failed: {e}")
            # Fallback to stty
            try:
                subprocess.run(["stty", "-F", "/dev/tty", "sane", "echo", "icanon"], 
                              capture_output=True, timeout=2)
                logger.info("stty sane fallback completed")
            except Exception as e2:
                logger.warning(f"stty fallback also failed: {e2}")

    async def action_quit(self) -> None:
        """Override quit to detach from tmux instead of exiting."""
        if os.environ.get("TMUX"):
            subprocess.run(["tmux", "detach-client"], capture_output=True)
            print("\nDetached from tmux. Run 'pipeline tui' to reconnect.")
            while True:
                time.sleep(3600)
        else:
            self.exit()

    CSS = """
    Screen {
        layout: vertical;
    }

    #main-layout {
        layout: horizontal;
        height: 1fr;
    }

    #items-pane {
        width: 1fr;
        min-width: 20;
        border-right: solid $panel;
        overflow-y: auto;
    }

    #items-pane:focus-within {
        border-right: solid $accent;
    }

    #details-pane {
        width: 2fr;
        height: 100%;
        overflow-y: auto;
        border-right: solid $panel;
    }

    #details-container {
        height: auto;
    }

    #logs-agents-pane {
        width: 1fr;
        height: 100%;
    }

    #logs-agents-pane > * {
        height: 1fr;
    }

    #log-section, #plan-section, #impl-section {
        height: 1fr;
    }

    .panel-header {
        height: 1;
        background: $panel;
        color: $text;
        text-style: bold;
        padding: 0 1;
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

    .feature-item.--active {
        color: cyan;
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
    # Auto-run modes - can run independently in parallel
    auto_plan_enabled: reactive[bool] = reactive(False)
    auto_implement_enabled: reactive[bool] = reactive(False)
    # Which right pane to show: "detail" or "log"
    right_pane_mode: reactive[str] = reactive("detail")

    def __init__(self) -> None:
        super().__init__()
        self._config = Config()
        self._config.setup_boards()
        # Track start time for terminal reattach fix
        self._start_time = time.time()
        # Track previous feature snapshot for change detection in auto-refresh
        self._prev_snapshot: dict[str, list[tuple[str, str]]] = {}
        self._prev_mtimes: dict[str, dict[str, float]] = {}
        # Two separate agent status instances for plan and implement pipelines
        self._plan_agent_status = AgentStatus()
        self._implement_agent_status = AgentStatus()
        # Shared agent status instance (for manual runs)
        self._agent_status = AgentStatus()
        # Persistent log buffer - survives view toggles
        self._log_buffer: list[str] = []
        # Track which features have been auto-queued to avoid duplicates
        self._auto_plan_queued: set[str] = set()
        self._auto_implement_queued: set[str] = set()
        # Track failures for retry cooldown
        self._auto_plan_fail_cooldown: dict[str, float] = {}  # slug -> timestamp when eligible again
        self._auto_implement_fail_cooldown: dict[str, float] = {}  # slug -> timestamp when eligible again
        self._auto_plan_fail_count: dict[str, int] = {}  # slug -> consecutive failure count
        self._auto_implement_fail_count: dict[str, int] = {}  # slug -> consecutive failure count
        # Track if plan/implement pipelines are running
        self._plan_running = False
        self._implement_running = False
        # Rate limit tracking - timestamp until which auto modes are disabled
        self._rate_limit_until: float = 0.0
        # Server client for pushing state to monitoring server
        self._server_client: Optional[ServerClient] = None
        # Scripts configuration
        self._scripts: list[ScriptConfig] = load_scripts(self._config.mad_dir)
        self._script_status: Optional[ScriptStatus] = None
        # Pipeline lock for preventing concurrent runs
        self._pipeline_lock = PipelineLock(self._config)

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
            # Scripts tab
            yield TabPane("Scripts", id="scripts-tab")
        
        self.active_board = boards[0] if boards else ""

        # Kanban layout - shown for board tabs, hidden for settings/scripts
        with Horizontal(id="main-layout"):
            with VerticalScroll(id="items-pane"):
                yield Static("Loading...", id="kanban-placeholder")
            with VerticalScroll(id="details-pane"):
                yield Vertical(id="details-container")
            with Vertical(id="logs-agents-pane"):
                with Vertical(id="log-section"):
                    yield Static("=== LOG ===", classes="panel-header")
                    yield Log(id="log-view", auto_scroll=True)
                with Vertical(id="plan-section"):
                    yield Static("=== PLAN ===", classes="panel-header")
                    yield AgentStatusWidget(
                        plan_status=self._plan_agent_status,
                        implement_status=None,
                        id="plan-status"
                    )
                with Vertical(id="impl-section"):
                    yield Static("=== IMPL ===", classes="panel-header")
                    yield AgentStatusWidget(
                        plan_status=None,
                        implement_status=self._implement_agent_status,
                        id="impl-status"
                    )

        # Settings view - hidden by default
        with Vertical(id="settings-view"):
            yield SettingsPane(self._config, id="settings-pane")

        # Scripts view - hidden by default
        with Vertical(id="scripts-view"):
            yield ScriptsPane(self._scripts, self._script_status, id="scripts-pane")

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
        # Hide settings and scripts views initially
        try:
            self.query_one("#settings-view", Vertical).display = False
        except NoMatches:
            pass
        try:
            self.query_one("#scripts-view", Vertical).display = False
        except NoMatches:
            pass
        # Auto-refresh every 10 seconds to pick up external changes
        self.set_interval(10.0, self._check_for_changes)
        # Auto-run check every 10 seconds
        self.set_interval(10.0, self._auto_run_check)
        # Start server connection if configured
        server_cfg = self._config.server
        if server_cfg and HAS_WEBSOCKETS and ServerClient is not None:
            self._server_client = ServerClient(
                url=server_cfg.url,
                api_key=server_cfg.api_key,
                client_id=server_cfg.client_id,
                on_connect=self._on_server_connected,
                on_answers_received=self._handle_server_answers,
                on_set_auto_mode=self._handle_set_auto_mode,
                on_start_agent=self._handle_start_agent,
                on_idea_created=self._handle_create_idea,
                on_move_requested=self._on_move_requested,
                on_edit_description=self._handle_edit_description,
                on_edit_done_script=self._handle_edit_done_script,
                on_edit_title=self._handle_edit_title,
                on_edit_item_type=self._handle_edit_item_type,
                on_edit_ideation_prompt=self._handle_edit_ideation_prompt,
                on_run_script=self._handle_run_script,
                on_set_agent_for_phase=self._handle_set_agent_for_phase,
                on_restart=self._handle_remote_restart,
            )
            self._connect_server()
            self.set_interval(5.0, self._periodic_server_push)

    async def _periodic_server_push(self) -> None:
        """Push current state to server periodically (agent status may change without feature changes)."""
        if self._server_client and self._server_client.connected:
            features = self._load_features(self.active_board)
            self._push_to_server(features[:])

    @work(thread=False)
    async def _connect_server(self) -> None:
        """Background task: maintain WebSocket connection to monitoring server."""
        if self._server_client:
            await self._server_client.reconnect_loop()

    async def _handle_server_answers(self, feature_id: str, answers: list) -> None:
        """Handle answer_questions message from the server (web UI submitted answers)."""
        feature = FeatureFile.find(feature_id)
        if not feature:
            logger.warning(f"answer_questions: feature {feature_id} not found")
            return
        if feature.current_stage != "requested-input":
            logger.warning(f"answer_questions: feature {feature_id} not in requested-input (is {feature.current_stage})")
            return
        for i, a in enumerate(answers):
            ans_text = a.get("answer", "")
            if ans_text:
                feature.answer_question(i, ans_text)
        feature.add_history("PLAN-INBOX", "Questions answered via web UI")
        feature.move_to_stage("plan-inbox")
        self._refresh_board(self.active_board)
        if self._server_client and self._server_client.connected:
            features = self._load_features(self.active_board)
            await self._server_client.push_state(
                features, self._plan_agent_status, self._implement_agent_status,
                auto_plan_enabled=self.auto_plan_enabled,
                auto_impl_enabled=self.auto_implement_enabled,
                scripts=self._scripts,
                script_status=self._script_status,
            )

    def _handle_set_auto_mode(self, mode: str, enabled: bool) -> None:
        """Handle set_auto_mode message from the server (web UI toggled auto mode)."""
        if mode == "plan":
            self.auto_plan_enabled = enabled
        elif mode == "impl":
            self.auto_implement_enabled = enabled
        self._update_status_bar(self.active_board, self._load_features(self.active_board))
        # Push updated state back to server
        if self._server_client and self._server_client.connected:
            features = self._load_features(self.active_board)
            self._push_to_server(features[:])

    async def _handle_start_agent(self, feature_id: str, action: str) -> None:
        """Handle start_agent message from the server (web UI triggered plan/implement)."""
        features = self._load_features(self.active_board)
        feature = None
        for f in features:
            if f.id == feature_id:
                feature = f
                break
        if not feature:
            logger.warning(f"start_agent: feature {feature_id} not found")
            return
        if action not in STAGE_ACTIONS:
            logger.warning(f"start_agent: unknown action {action}")
            return
        if feature.current_stage not in STAGE_ACTIONS[action]:
            logger.warning(f"start_agent: feature {feature_id} stage {feature.current_stage} not valid for {action}")
            return
        if action == "plan" and self._plan_running:
            logger.warning("start_agent: plan agent already running")
            return
        if action == "implement" and self._implement_running:
            logger.warning("start_agent: implement agent already running")
            return

        self.selected_feature = feature
        self._show_log()
        if action == "plan":
            self._log_line(f"=== Running PLAN (via web UI) for: {feature.title} ===")
            self._log_line(f"Stage: {feature.current_stage}")
            self._log_line("")
            self._plan_running = True
            self._run_plan_async(feature)
        else:
            self._log_line(f"=== Running IMPLEMENT (via web UI) for: {feature.title} ===")
            self._log_line(f"Stage: {feature.current_stage}")
            self._log_line("")
            self._implement_running = True
            self._run_implement_async(feature)

    async def _handle_create_idea(self, title: str, board: str, description: str, item_type: str = "feature", done_script: str = "", requires_human_approval: bool = False) -> None:
        """Handle create_idea message from the server (web UI created a new idea)."""
        try:
            feature = FeatureFile.create(board, title, description, item_type=item_type, done_script=done_script, requires_human_approval=requires_human_approval)
            logger.info(f"Created new idea: {feature.title} ({feature.id}) in board {board}")
            self._refresh_board(self.active_board)
            if self._server_client and self._server_client.connected:
                features = self._load_features(self.active_board)
                asyncio.create_task(self._server_client.push_state(
                    features, self._plan_agent_status, self._implement_agent_status,
                    auto_plan_enabled=self.auto_plan_enabled,
                    auto_impl_enabled=self.auto_implement_enabled,
                    scripts=self._scripts,
                    script_status=self._script_status,
                ))
        except Exception as e:
            logger.warning(f"create_idea: failed to create idea: {e}")

    async def _on_move_requested(self, feature_id: str, target_stage: str, request_id: str, reason: str = "") -> None:
        """Handle move_feature message from the server (web UI moved a feature to a new stage)."""
        try:
            feature = FeatureFile.find(feature_id)
            if not feature:
                logger.warning(f"move_feature: feature {feature_id} not found")
                if request_id and self._server_client:
                    await self._server_client.send_move_result(request_id, False, "feature not found")
                return
            if target_stage not in STAGES:
                logger.warning(f"move_feature: invalid target stage {target_stage}")
                if request_id and self._server_client:
                    await self._server_client.send_move_result(request_id, False, f"invalid target stage: {target_stage}")
                return
            logger.info(f"Moving feature {feature_id} to {target_stage} via web UI")
            if target_stage == "ideas" and feature.current_stage == "awaiting-human-approval" and reason:
                feature.add_plan_review("FAIL", f"Human rejected plan: {reason}", reason)
                feature.add_history("REJECTED", f"Plan rejected by human via web UI: {reason}")
            elif target_stage == "implementing" and feature.current_stage == "final-human-approval" and reason:
                from phases import _get_latest_feedback
                prev_feedback = _get_latest_feedback(feature)
                full_feedback = f"Human rejection: {reason}\n\nPrevious review feedback:\n{prev_feedback}"
                feature.add_impl_review("FAIL", f"Human rejected from final-human-approval: {reason}", full_feedback)
                feature.add_history("IMPLEMENTING", f"Sent back to implementing: {reason or 'No reason given'}")
            else:
                feature.add_history(target_stage.upper(), "Moved via web UI")
            feature.move_to_stage(target_stage)
            if target_stage == "done":
                from scripts import execute_done_script
                try:
                    execute_done_script(feature, self._config)
                except Exception as e:
                    logger.warning(f"Done script execution failed: {e}")
            self._refresh_board(self.active_board)
            # Push updated state back to server
            if self._server_client and self._server_client.connected:
                features = self._load_features(self.active_board)
                asyncio.create_task(self._server_client.push_state(
                    features, self._plan_agent_status, self._implement_agent_status,
                    auto_plan_enabled=self.auto_plan_enabled,
                    auto_impl_enabled=self.auto_implement_enabled,
                    scripts=self._scripts,
                    script_status=self._script_status,
                ))
            if request_id and self._server_client:
                await self._server_client.send_move_result(request_id, True)
        except Exception as e:
            logger.warning(f"move_feature: failed to move feature: {e}")
            if request_id and self._server_client:
                await self._server_client.send_move_result(request_id, False, str(e))

    async def _handle_edit_description(self, feature_id: str, description: str) -> None:
        """Handle edit_description message from the server (web UI edited a feature's description)."""
        try:
            feature = FeatureFile.find(feature_id)
            if not feature:
                logger.warning(f"edit_description: feature {feature_id} not found")
                return
            if feature.current_stage != "ideas":
                logger.warning(f"edit_description: feature {feature_id} not in ideas stage")
                return
            logger.info(f"Editing description for feature {feature_id} via web UI")
            feature.set_description(description)
            feature.add_history(feature.current_stage.upper(), "Description edited via web UI")
            self._refresh_board(self.active_board)
            # Push updated state back to server
            if self._server_client and self._server_client.connected:
                features = self._load_features(self.active_board)
                asyncio.create_task(self._server_client.push_state(
                    features, self._plan_agent_status, self._implement_agent_status,
                    auto_plan_enabled=self.auto_plan_enabled,
                    auto_impl_enabled=self.auto_implement_enabled,
                    scripts=self._scripts,
                    script_status=self._script_status,
                ))
        except Exception as e:
            logger.warning(f"edit_description: failed to edit description: {e}")

    async def _handle_edit_done_script(self, feature_id: str, done_script: str) -> None:
        """Handle edit_done_script message from the server (web UI edited a feature's done script)."""
        try:
            feature = FeatureFile.find(feature_id)
            if not feature:
                logger.warning(f"edit_done_script: feature {feature_id} not found")
                return
            logger.info(f"Editing done_script for feature {feature_id} via web UI")
            feature.set_done_script(done_script)
            feature.add_history(feature.current_stage.upper(), "Done script edited via web UI")
            self._refresh_board(self.active_board)
            # Push updated state back to server
            if self._server_client and self._server_client.connected:
                features = self._load_features(self.active_board)
                asyncio.create_task(self._server_client.push_state(
                    features, self._plan_agent_status, self._implement_agent_status,
                    auto_plan_enabled=self.auto_plan_enabled,
                    auto_impl_enabled=self.auto_implement_enabled,
                    scripts=self._scripts,
                    script_status=self._script_status,
                ))
        except Exception as e:
            logger.warning(f"edit_done_script: failed to edit done script: {e}")

    async def _handle_edit_title(self, feature_id: str, title: str) -> None:
        """Handle edit_title message from the server (web UI edited a feature's title)."""
        try:
            feature = FeatureFile.find(feature_id)
            if not feature:
                logger.warning(f"edit_title: feature {feature_id} not found")
                return
            if feature.current_stage != "ideas":
                logger.warning(f"edit_title: feature {feature_id} not in ideas stage")
                return
            logger.info(f"Editing title for feature {feature_id} via web UI")
            feature.set_title(title)
            feature._data["history"][-1]["note"] = "Title edited via web UI"
            feature._save()
            self._refresh_board(self.active_board)
            # Push updated state back to server
            if self._server_client and self._server_client.connected:
                features = self._load_features(self.active_board)
                asyncio.create_task(self._server_client.push_state(
                    features, self._plan_agent_status, self._implement_agent_status,
                    auto_plan_enabled=self.auto_plan_enabled,
                    auto_impl_enabled=self.auto_implement_enabled,
                    scripts=self._scripts,
                    script_status=self._script_status,
                ))
        except Exception as e:
            logger.warning(f"edit_title: failed to edit title: {e}")

    async def _handle_edit_item_type(self, feature_id: str, item_type: str) -> None:
        """Handle edit_item_type message from the server (web UI edited a feature's type)."""
        try:
            feature = FeatureFile.find(feature_id)
            if not feature:
                logger.warning(f"edit_item_type: feature {feature_id} not found")
                return
            if feature.current_stage != "ideas":
                logger.warning(f"edit_item_type: feature {feature_id} not in ideas stage")
                return
            logger.info(f"Editing item_type for feature {feature_id} via web UI")
            feature.set_item_type(item_type)
            feature._data["history"][-1]["note"] = "Type edited via web UI"
            feature._save()
            self._refresh_board(self.active_board)
            # Push updated state back to server
            if self._server_client and self._server_client.connected:
                features = self._load_features(self.active_board)
                asyncio.create_task(self._server_client.push_state(
                    features, self._plan_agent_status, self._implement_agent_status,
                    auto_plan_enabled=self.auto_plan_enabled,
                    auto_impl_enabled=self.auto_implement_enabled,
                    scripts=self._scripts,
                    script_status=self._script_status,
                ))
        except Exception as e:
            logger.warning(f"edit_item_type: failed to edit item type: {e}")

    async def _handle_edit_ideation_prompt(self, feature_id: str, ideation_prompt: str) -> None:
        """Handle edit_ideation_prompt message from the server (web UI edited a feature's ideation prompt)."""
        try:
            feature = FeatureFile.find(feature_id)
            if not feature:
                logger.warning(f"edit_ideation_prompt: feature {feature_id} not found")
                return
            logger.info(f"Editing ideation_prompt for feature {feature_id} via web UI")
            feature.set_ideation_prompt(ideation_prompt)
            feature.add_history(feature.current_stage.upper(), "Ideation prompt edited via web UI")
            self._refresh_board(self.active_board)
            # Push updated state back to server
            if self._server_client and self._server_client.connected:
                features = self._load_features(self.active_board)
                asyncio.create_task(self._server_client.push_state(
                    features, self._plan_agent_status, self._implement_agent_status,
                    auto_plan_enabled=self.auto_plan_enabled,
                    auto_impl_enabled=self.auto_implement_enabled,
                    scripts=self._scripts,
                    script_status=self._script_status,
                ))
        except Exception as e:
            logger.warning(f"edit_ideation_prompt: failed: {e}")

    async def _handle_run_script(self, script_id: str, context: str) -> None:
        """Handle run_script message from the server (web UI requested script execution)."""
        script = next((s for s in self._scripts if s.id == script_id), None)
        if not script:
            logger.warning(f"run_script: script '{script_id}' not found")
            return
        self._run_script_async(script_id, context)

    async def _handle_set_agent_for_phase(self, phase: str, agent: str, model: str) -> None:
        """Handle set_agent_for_phase message from the server (web UI changed phase settings)."""
        try:
            cfg = Config()
            cfg.set_agent_for_phase(phase, agent, model or None)
            logger.info(f"Set agent for phase {phase}: agent={agent}, model={model}")
        except ValueError as e:
            logger.warning(f"Invalid set_agent_for_phase: {e}")

    async def _on_server_connected(self) -> None:
        """Push full board state immediately after connecting to server."""
        board = self.active_board
        if board and self._server_client and self._server_client.connected:
            features = self._load_features(board)
            await self._server_client.push_state(
                features, self._plan_agent_status, self._implement_agent_status,
                auto_plan_enabled=self.auto_plan_enabled,
                auto_impl_enabled=self.auto_implement_enabled,
                scripts=self._scripts,
                script_status=self._script_status,
            )

    @work(thread=False)
    async def _push_to_server(self, features: list) -> None:
        """Background task: push current feature state to the server."""
        if self._server_client and self._server_client.connected:
            try:
                await self._server_client.push_state(
                    features, self._plan_agent_status, self._implement_agent_status,
                    auto_plan_enabled=self.auto_plan_enabled,
                    auto_impl_enabled=self.auto_implement_enabled,
                    scripts=self._scripts,
                    script_status=self._script_status,
                )
            except Exception as e:
                logger.warning(f"Failed to push state to server: {e}")

    def on_settings_pane_settings_changed(self, event: SettingsPane.SettingsChanged) -> None:
        """Handle settings changes - reload config."""
        # Reload the config to pick up changes
        self._config = Config(path=self._config._path)
        self.notify("Settings applied", severity="information")

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        """Handle tab switch - boards, settings or scripts."""
        pane_id = event.pane.id or ""
        
        if pane_id == "settings-tab":
            self._show_settings_view()
            return
        
        if pane_id == "scripts-tab":
            self._show_scripts_view()
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
            scripts_view = self.query_one("#scripts-view", Vertical)
            scripts_view.display = False
            self.query_one("#status-bar", Static).display = False
            self.query_one("#contextual-footer", Horizontal).display = False
        except NoMatches:
            pass

    def _show_scripts_view(self) -> None:
        """Show the scripts panel and hide the kanban."""
        try:
            main_layout = self.query_one("#main-layout", Horizontal)
            main_layout.display = False
            settings_view = self.query_one("#settings-view", Vertical)
            settings_view.display = False
            scripts_view = self.query_one("#scripts-view", Vertical)
            scripts_view.display = True
            self.query_one("#status-bar", Static).display = False
            self.query_one("#contextual-footer", Horizontal).display = False
        except NoMatches:
            pass

    def _show_kanban_view(self) -> None:
        """Show the kanban board and hide settings/scripts."""
        try:
            main_layout = self.query_one("#main-layout", Horizontal)
            main_layout.display = True
            settings_view = self.query_one("#settings-view", Vertical)
            settings_view.display = False
            scripts_view = self.query_one("#scripts-view", Vertical)
            scripts_view.display = False
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
            items_pane = self.query_one("#items-pane", VerticalScroll)
        except NoMatches:
            return

        # Remove all current children
        items_pane.remove_children()

        # Build new widgets
        selected_slug = self.selected_feature.slug if self.selected_feature else None
        selected_board = self.selected_feature.board if self.selected_feature else None

        # Compute active slugs
        active_slugs: set[str] = set()
        if self._plan_running and self._plan_agent_status and self._plan_agent_status.feature_slug:
            active_slugs.add(self._plan_agent_status.feature_slug)
        if self._implement_running and self._implement_agent_status and self._implement_agent_status.feature_slug:
            active_slugs.add(self._implement_agent_status.feature_slug)

        for stage in STAGE_DISPLAY_ORDER:
            stage_features = by_stage.get(stage, [])
            stage_features.sort(key=_last_modified_key, reverse=True)
            count = len(stage_features)
            items_pane.mount(StageHeader(stage, count))

            if count > 0:
                for f in stage_features:
                    is_sel = (
                        f.slug == selected_slug and f.board == selected_board
                    )
                    items_pane.mount(FeatureItem(f, is_selected=is_sel, is_active=f.slug in active_slugs))

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
        
        # Show auto modes
        modes = []
        if self.auto_plan_enabled:
            modes.append("[green]PLAN[/green]")
        if self.auto_implement_enabled:
            modes.append("[green]IMPL[/green]")
        if modes:
            status_text += " | " + " ".join(modes) + " (stop: \\)"
        else:
            status_text += " | [dim]auto off[/dim] (plan: ctrl+a, impl: ctrl+d)"

        try:
            self.query_one("#status-bar", Static).update(status_text)
        except NoMatches:
            pass

    def _check_for_changes(self) -> None:
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
            self._log_line(f"[auto-refresh] Changes detected, refreshing {board}")
            self._current_features = features
            self._prev_snapshot[board] = current_snapshot
            self._prev_mtimes[board] = current_mtimes
            self._refresh_kanban_widgets()
            self._update_status_bar(board, features)

            if self._server_client and self._server_client.connected:
                self._push_to_server(features[:])

            # Always refresh selected feature from disk to show latest content
            if self.selected_feature:
                refreshed = FeatureFile.find(self.selected_feature.slug)
                if refreshed:
                    self.selected_feature = refreshed
                    self._update_detail_view()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._log_line(f"[auto-refresh] Error: {e}")

    # ------------------------------------------------------------------
    # Right pane detail view
    # ------------------------------------------------------------------

    def _update_detail_view(self) -> None:
        """Update the right pane to show the selected feature with collapsible sections."""
        try:
            details_container = self.query_one("#details-container", Vertical)
        except NoMatches:
            return

        if not self.selected_feature:
            details_container.remove_children()
            details_container.mount(Static("*Select a feature to view details*"))
            self._update_footer()
            return

        details_container.remove_children()
        widgets = _build_feature_detail_widgets(self.selected_feature)
        details_container.mount(*widgets)
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
        """Detail is always visible now."""
        pass

    def _show_log(self) -> None:
        """Log is always visible now - just flush buffer."""
        self._flush_log_buffer()

    def _show_split(self) -> None:
        """No longer needed - both are always visible."""
        pass

    def _log_line(self, text: str) -> None:
        """Write a line to the log view and persist to buffer."""
        # Always add to buffer
        self._log_buffer.append(text)
        # Keep buffer manageable
        if len(self._log_buffer) > 1000:
            self._log_buffer = self._log_buffer[-500:]
        
        try:
            log = self.query_one("#log-view", Log)
            log.write_line(text)
            log.scroll_end()  # Auto-scroll to bottom
        except NoMatches:
            pass

    def _flush_log_buffer(self) -> None:
        """Flush log buffer to view when log is shown."""
        try:
            log = self.query_one("#log-view", Log)
            for line in self._log_buffer[-100:]:  # Show last 100 lines
                log.write_line(line)
            log.scroll_end()
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

    def action_go_to_scripts(self) -> None:
        """Switch to the Scripts tab."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "scripts-tab"
        except NoMatches:
            pass

    def action_unified_edit(self) -> None:
        """Open unified edit modal for the selected feature."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return

        feature_data = {
            "title": f.title,
            "item_type": f.item_type,
            "description": f.get_section("Description") or "",
            "done_script": f.done_script,
            "requires_human_approval": f.requires_human_approval,
            "ideation_prompt": f.ideation_prompt,
        }

        def handle_unified_edit(result: dict | None) -> None:
            if result is None:
                return
            
            stage = f.current_stage
            
            if "title" in result:
                if result["title"]:
                    try:
                        f.set_title(result["title"])
                    except ValueError as e:
                        self.notify(f"Error: {e}", severity="error")
                        return
            
            if stage in ("ideas", "ideating", "plan-inbox") and "item_type" in result:
                try:
                    f.set_item_type(result["item_type"])
                except ValueError as e:
                    self.notify(f"Error: {e}", severity="error")
                    return
            
            if "description" in result:
                f.set_description(result["description"])
            
            if "done_script" in result:
                f.set_done_script(result["done_script"])
            
            if "requires_human_approval" in result:
                f.set_requires_human_approval(result["requires_human_approval"])
            
            if "ideation_prompt" in result:
                f.set_ideation_prompt(result["ideation_prompt"])
            
            self.notify("Feature updated", severity="information")
            self._update_detail_view()

        self.push_screen(
            UnifiedEditModal(feature_data, f.current_stage, self._scripts),
            callback=handle_unified_edit,
        )

    def action_refresh(self) -> None:
        """Manually refresh the board."""
        self._refresh_board(self.active_board)
        self.notify("Board refreshed", severity="information")

    def action_toggle_log_detail(self) -> None:
        """Now a no-op - both detail and log are always visible. Focus the log view."""
        try:
            log = self.query_one("#log-view", Log)
            log.focus()
        except NoMatches:
            pass

    def action_toggle_focus(self) -> None:
        """Toggle focus between left and right panes."""
        try:
            left = self.query_one("#items-pane")
            right_detail = self.query_one("#details-container")
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
        item_list = list(self.query("StageHeader, FeatureItem"))
        if not item_list:
            return

        current = self.focused
        if current and current in item_list:
            idx = item_list.index(current)
            if idx > 0:
                item_list[idx - 1].focus()
            else:
                item_list[-1].focus()
        else:
            if item_list:
                item_list[0].focus()

    def action_focus_next(self) -> None:
        """Move focus to the next item (stage header or feature)."""
        item_list = list(self.query("StageHeader, FeatureItem"))
        if not item_list:
            return

        current = self.focused
        if current and current in item_list:
            idx = item_list.index(current)
            if idx < len(item_list) - 1:
                item_list[idx + 1].focus()
            else:
                item_list[0].focus()
        else:
            if item_list:
                item_list[0].focus()

    def action_answer_questions(self) -> None:
        """Answer questions raised by the planning agent."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        
        if f.current_stage != "requested-input":
            self.notify("No questions to answer for this feature", severity="warning")
            return
        
        questions = f.questions
        if not questions:
            self.notify("No questions pending", severity="warning")
            return
        
        self.push_screen(AnswerQuestionsModal(f), callback=self._on_questions_answered)

    def _on_questions_answered(self, result: bool | None) -> None:
        """Called after questions are answered."""
        if result:
            self.notify("Questions answered, ready to continue", severity="information")
            self._refresh_board(self.active_board)
            self._update_detail_view()

    def action_move(self) -> None:
        """Open modal to move feature to any stage."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return

        def handle_move(stage: str | None) -> None:
            if stage is None:
                return
            old_stage = f.current_stage
            if stage == old_stage:
                self.notify(f"Already in {stage}", severity="information")
                return
            f.add_history("MOVED", f"Manual move: {old_stage} -> {stage}")
            f.save()
            f.move_to_stage(stage)
            self.notify(f"Moved: {f.title} -> {stage}", severity="information")
            refreshed = FeatureFile.find(f.slug)
            self.selected_feature = refreshed
            self._refresh_board(self.active_board)
            self._update_detail_view()

        self.push_screen(MoveFeatureModal(f.current_stage), callback=handle_move)

    def action_debate(self) -> None:
        """Move the selected feature to ideating stage for debate."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        
        if f.current_stage != "ideas":
            self.notify("Debate is only available from ideas stage", severity="warning")
            return
        
        f.add_history("DEBATE", "Moved from ideas to ideating for debate")
        f.save()
        f.move_to_stage("ideating")
        
        refreshed = FeatureFile.find(f.slug)
        self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()
        
        self.notify(f"Moved to ideating: {f.title}", severity="information")

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
        elif f.current_stage == "awaiting-human-approval":
            # Approve on awaiting-human-approval = move to approved for pipeline pickup
            f.add_history("PROMOTED", "Human approved plan, moving to approved")
            f.save()
            f.move_to_stage("approved")
            self.notify(f"Approved for implementation: {f.title}", severity="information")
        elif f.current_stage == "final-human-approval":
            # Approve on final-human-approval = it's done
            f.add_history("DONE", "Approved as complete (TUI)")
            f.save()
            f.move_to_stage("done")
            from scripts import execute_done_script
            try:
                execute_done_script(f, self._config)
            except Exception as e:
                self.notify(f"Done script failed: {e}", severity="warning")
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
            f"**Feature Iteration: {f.title}**\n\n"
            f"The feature file is at: `{f.path.name}`\n\n"
            f"**Current Description:**\n{f.get_section('Description') or '(no description)'}\n\n"
            f"**Current Plan:**\n{f.plan or '(no plan yet)'}\n\n"
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
        if f.current_stage == "awaiting-human-approval":
            def handle_plan_reject(reason: str | None) -> None:
                if reason is None:
                    return
                f.add_plan_review("FAIL", f"Human rejected plan: {reason}", reason)
                f.add_history("REJECTED", f"Plan rejected by human, sent back to ideas: {reason}")
                f.move_to_stage("ideas")
                self.notify(f"Rejected plan, sent to ideas: {f.title}", severity="information")
                refreshed = FeatureFile.find(f.slug)
                self.selected_feature = refreshed
                self._refresh_board(self.active_board)
                self._update_detail_view()
            self.push_screen(RejectModal(), callback=handle_plan_reject)
            return
        elif f.current_stage == "final-human-approval":
            # Push a modal to get rejection reason
            def handle_final_reject(reason: str | None) -> None:
                if reason is None:
                    return
                feedback = _get_latest_feedback(f)
                full_feedback = f"Human rejection: {reason}\n\nPrevious review feedback:\n{feedback}"
                f.add_impl_review("FAIL", f"Human rejected from final-human-approval: {reason}", full_feedback)
                f.add_history("REJECTED", f"Sent back to implementing: {reason}")
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
            
            # ideas, ideating, and plan-inbox go to rejected
            if f.current_stage in ("ideas", "ideating", "plan-inbox"):
                f.add_history("REJECTED", f"Rejected: {reason}")
                f.save()
                f.move_to_stage("rejected")
                self.notify(f"Rejected: {f.title}", severity="information")
            # approved goes back to plan-inbox
            elif f.current_stage == "approved":
                f.add_history("REJECTED", f"Rejected: {reason}")
                f.save()
                f.move_to_stage("plan-inbox")
                self.notify(f"Sent back to plan-inbox: {f.title}", severity="information")
            else:
                # Most other stages go back to implementing for fixes
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

    def action_start(self) -> None:
        """Start pipeline: plan from plan-inbox/requested-input, impl from approved/spec-writing/implementing."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        
        stage = f.current_stage
        if stage in ("plan-inbox", "reviewing-plan", "requested-input"):
            self.action_plan_only()
        elif stage in ("approved", "spec-writing", "implementing"):
            self.action_implement_only()
        else:
            self.notify(f"Cannot start from {stage}", severity="warning")

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
                    from runner import AgentRunner, RateLimitError
                    runner = AgentRunner(self._config)
                    update_design_doc(f, runner)
                except Exception as e:
                    self.notify(f"Design doc update failed: {e}", severity="warning")
            
            # Execute done script if configured
            from scripts import execute_done_script
            try:
                execute_done_script(f, self._config)
            except Exception as e:
                self.notify(f"Done script failed: {e}", severity="warning")
            
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
        
        # Log is always visible now
        self._log_line(f"Starting pipeline from {stage}...")
        
        # Run in background thread
        self._run_restart_async(f, stage)

    def action_kill_agent(self) -> None:
        """Kill the currently running agent."""
        if not self._plan_running and not self._implement_running:
            self.notify("No agent is running", severity="warning")
            return
        
        # Kill plan agent if running
        if self._plan_running and self._plan_agent_status:
            self._plan_agent_status.kill_requested = True
            slug = self._plan_agent_status.feature_slug
            self.notify(f"Killing plan agent for {slug}")
            self._log_line(f"[user] Kill requested for plan agent ({slug})")
            # Add history entry
            from state import FeatureFile
            feature = FeatureFile.find(slug)
            if feature:
                elapsed = ""
                if self._plan_agent_status.started_at:
                    elapsed_secs = int(time.time() - self._plan_agent_status.started_at)
                    elapsed = f"{elapsed_secs // 60}m{elapsed_secs % 60}s"
                feature.add_history("KILLED", f"Agent terminated by user after {elapsed}")
        # Kill implement agent if running
        elif self._implement_running and self._implement_agent_status:
            self._implement_agent_status.kill_requested = True
            slug = self._implement_agent_status.feature_slug
            self.notify(f"Killing implement agent for {slug}")
            self._log_line(f"[user] Kill requested for implement agent ({slug})")
            # Add history entry
            from state import FeatureFile
            feature = FeatureFile.find(slug)
            if feature:
                elapsed = ""
                if self._implement_agent_status.started_at:
                    elapsed_secs = int(time.time() - self._implement_agent_status.started_at)
                    elapsed = f"{elapsed_secs // 60}m{elapsed_secs % 60}s"
                feature.add_history("KILLED", f"Agent terminated by user after {elapsed}")

    def action_run_script(self) -> None:
        """Open modal to select and run a script."""
        if not self._scripts:
            self.notify("No scripts configured. Add scripts to .mad/scripts.json")
            return
        if self._script_status and self._script_status.running:
            self.notify("A script is already running")
            return
        self.push_screen(RunScriptModal(self._scripts), self._on_script_selected)

    def _on_script_selected(self, result: Optional[tuple[str, str]]) -> None:
        """Handle the result from RunScriptModal."""
        if result:
            script_id, context = result
            self._run_script_async(script_id, context)

    @work(thread=True, exclusive=True, group="script")
    def _run_script_async(self, script_id: str, context: str = "") -> None:
        """Run a script in a background thread."""
        script = next((s for s in self._scripts if s.id == script_id), None)
        if not script:
            self.app.call_from_thread(self.notify, f"Script '{script_id}' not found", severity="error")
            return

        self._script_status = ScriptStatus(script_id=script_id, context=context)
        self._log_line(f"=== Running SCRIPT: {script.label} ===")
        if context:
            self._log_line(f"Context: {context}")
        self._log_line("")

        try:
            exit_code = run_script(script, self._config, self._script_status, context)
            severity = "information" if exit_code == 0 else "error"
            self._log_line(f"=== Script '{script.label}' finished (exit code: {exit_code}) ===")
            self._log_line("")
            self.app.call_from_thread(
                self.notify, f"Script '{script.label}' finished (exit code: {exit_code})", severity=severity
            )
        except Exception as e:
            self._log_line(f"=== Script '{script.label}' ERROR: {e} ===")
            self._log_line("")
            self.app.call_from_thread(self.notify, f"Script error: {e}", severity="error")
        finally:
            self._script_status = None
            # Push updated state to webui so it clears the "running" indicator
            if self._server_client and self._server_client.connected:
                features = self._load_features(self.active_board)
                self._push_to_server(features[:])

    def action_kill_script(self) -> None:
        """Kill the currently running script."""
        if not self._script_status or not self._script_status.running:
            self.notify("No script is running", severity="warning")
            return
        if self._script_status._agent_status:
            self._script_status._agent_status.kill_requested = True
        self._script_status.kill_requested = True
        self.notify("Kill requested for running script")
        self._log_line(f"[user] Kill requested for script ({self._script_status.script_id})")

    def action_restart_agent(self) -> None:
        """Kill and restart the currently running agent."""
        if not self._plan_running and not self._implement_running:
            self.notify("No agent is running", severity="warning")
            return
        
        if self._plan_running and self._plan_agent_status:
            self._plan_agent_status.kill_requested = True
            self._plan_agent_status.restart_requested = True
            slug = self._plan_agent_status.feature_slug
            self.notify("Restarting plan agent...")
            self._log_line(f"[user] Restart requested for plan agent ({slug})")
            # Add history entry
            from state import FeatureFile
            feature = FeatureFile.find(slug)
            if feature:
                elapsed = ""
                if self._plan_agent_status.started_at:
                    elapsed_secs = int(time.time() - self._plan_agent_status.started_at)
                    elapsed = f"{elapsed_secs // 60}m{elapsed_secs % 60}s"
                feature.add_history("RESTARTED", f"Agent restarted by user after {elapsed}")
        elif self._implement_running and self._implement_agent_status:
            self._implement_agent_status.kill_requested = True
            self._implement_agent_status.restart_requested = True
            slug = self._implement_agent_status.feature_slug
            self.notify("Restarting implement agent...")
            self._log_line(f"[user] Restart requested for implement agent ({slug})")
            # Add history entry
            from state import FeatureFile
            feature = FeatureFile.find(slug)
            if feature:
                elapsed = ""
                if self._implement_agent_status.started_at:
                    elapsed_secs = int(time.time() - self._implement_agent_status.started_at)
                    elapsed = f"{elapsed_secs // 60}m{elapsed_secs % 60}s"
                feature.add_history("RESTARTED", f"Agent restarted by user after {elapsed}")

    async def _handle_remote_restart(self) -> None:
        """Handle restart_tui signal from the server. Performs graceful cleanup then triggers systemd service restart."""
        import subprocess
        from pathlib import Path

        logger.info("Handling remote restart request")

        project_dir = self._config.mad_dir.parent
        svc_name = service_name_for_dir(project_dir)
        unit_path = Path.home() / ".config" / "systemd" / "user" / f"{svc_name}.service"

        if not unit_path.exists():
            logger.warning(f"Systemd service unit not found at {unit_path}, cannot restart")
            if self._server_client and self._server_client._connected and self._server_client._ws:
                import json as _json
                try:
                    err_msg = _json.dumps({"type": "restart_error", "error": "Service not installed. Install the systemd service first (pipeline service install)."})
                    await self._server_client._ws.send(err_msg)
                except Exception:
                    pass
            return

        if self._plan_agent_status and self._plan_agent_status.running:
            self._plan_agent_status.kill_requested = True
            logger.info("Requested plan agent kill for restart")
        if self._implement_agent_status and self._implement_agent_status.running:
            self._implement_agent_status.kill_requested = True
            logger.info("Requested implement agent kill for restart")
        if self._script_status and self._script_status.running:
            logger.info("Script is running during restart — it will be terminated by SIGTERM")

        try:
            self._pipeline_lock.release('plan')
        except Exception:
            pass
        try:
            self._pipeline_lock.release('impl')
        except Exception:
            pass

        await asyncio.sleep(1.0)

        logger.info(f"Issuing systemctl --user restart {svc_name}")
        try:
            subprocess.Popen(
                ['systemctl', '--user', 'restart', svc_name],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"Failed to issue systemctl restart: {e}")

    @work(thread=True, exclusive=True, group="pipeline")
    def _run_restart_async(self, feature: FeatureFile, stage: str) -> None:
        """Run restart pipeline in background thread."""
        from phases import (
            run_pipeline, run_pipeline_from_implementing,
            run_verify_tests, run_review_impl
        )
        from runner import AgentRunner
        
        runner = AgentRunner(self._config)
        
        # Choose status based on stage
        if stage in ("plan-inbox", "reviewing-plan", "spec-writing", "approved", "ideas", "inbox"):
            status = self._plan_agent_status
        else:
            status = self._implement_agent_status
        
        # Set up status for display
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
                from phases import run_fix_feedback, _get_latest_feedback
                verdict = "FAIL"
                for attempt in range(1, 4):
                    if attempt == 1:
                        test_verdict, test_fb = run_verify_tests(feature, runner, status=status)
                    else:
                        fb = _get_latest_feedback(feature)
                        run_fix_feedback(feature, runner, fb, status=status)
                        test_verdict, test_fb = run_verify_tests(feature, runner, status=status)
                    if test_verdict != "PASS":
                        continue
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

    def action_toggle_auto_plan(self) -> None:
        """Toggle automatic planning for plan-inbox features."""
        self.auto_plan_enabled = not self.auto_plan_enabled
        if self.auto_plan_enabled:
            self.notify("Auto-Plan enabled - will process plan-inbox features", severity="information")
        else:
            self.auto_plan_enabled = False
            self.notify("Auto-Plan disabled", severity="information")
        self._update_status_bar(self.active_board, self._load_features(self.active_board))

    def _handle_rate_limit(self, mode: str) -> None:
        """Handle rate limiting by pausing auto mode for a cooldown period."""
        import datetime
        backoff_minutes = 5
        self._rate_limit_until = time.time() + (backoff_minutes * 60)
        
        if mode == "plan":
            self.notify(f"Rate limited! Auto-Plan pausing for {backoff_minutes} min", severity="warning")
        else:
            self.notify(f"Rate limited! Auto-Impl pausing for {backoff_minutes} min", severity="warning")
        
        self._update_status_bar(self.active_board, self._load_features(self.active_board))

    def action_toggle_auto_implement(self) -> None:
        """Toggle automatic implementation for approved features."""
        self.auto_implement_enabled = not self.auto_implement_enabled
        if self.auto_implement_enabled:
            self.notify("Auto-Implement enabled - will process approved features", severity="information")
        else:
            self.auto_implement_enabled = False
            self.notify("Auto-Implement disabled", severity="information")
        self._update_status_bar(self.active_board, self._load_features(self.active_board))

    def action_stop_auto(self) -> None:
        """Stop both auto-plan and auto-implement modes."""
        was_plan = self.auto_plan_enabled
        was_impl = self.auto_implement_enabled
        self.auto_plan_enabled = False
        self.auto_implement_enabled = False
        if was_plan or was_impl:
            self.notify("All auto-modes stopped", severity="information")
        else:
            self.notify("No auto-modes running", severity="information")
        self._update_status_bar(self.active_board, self._load_features(self.active_board))

    # Auto-run settings
    AUTO_RUN_INTERVAL = 60  # 60 seconds between runs
    AUTO_RETRY_COOLDOWN = 120  # seconds before retrying a failed item
    AUTO_MAX_RETRIES = 5  # max consecutive failures before permanent skip until restart
    _last_auto_plan_time: Optional[float] = None
    _last_auto_implement_time: Optional[float] = None

    def _auto_run_check(self) -> None:
        """Check for features and auto-run if enabled.
        
        Auto-Plan runs on plan-inbox/reviewing-plan/requested-input.
        Auto-Implement runs on approved/spec-writing.
        Each mode runs independently and in parallel.
        """
        # Check if we're rate limited
        if time.time() < self._rate_limit_until:
            remaining = int(self._rate_limit_until - time.time())
            if remaining % 60 == 0:  # Log every minute
                self._log_line(f"[rate-limit] Paused for {remaining}s more")
            return
        
        # Auto-Plan loop
        if self.auto_plan_enabled:
            self._auto_plan_check()
        
        # Auto-Implement loop  
        if self.auto_implement_enabled:
            self._auto_implement_check()

    def _auto_plan_check(self) -> None:
        """Check for plan-inbox features and auto-run planning if enabled."""
        # Don't run if a plan pipeline is already running
        if self._plan_running:
            logger.info("[auto-plan] Skipping - planning already in progress")
            return
        
        # Check if plan lock is held by another process
        if self._pipeline_lock.check('plan'):
            logger.info("[auto-plan] Skipping - plan lock held by another process")
            return
        
        now = time.time()
        if self._last_auto_plan_time is not None:
            elapsed = now - self._last_auto_plan_time
            if elapsed < self.AUTO_RUN_INTERVAL:
                return  # Silent - no need to spam logs
        
        logger.info(f"[auto-plan] Checking boards: {self._config.boards}")
        
        for board in self._config.boards:
            candidates = []
            stage_counts = {}
            # Include ideating (for debate), plan-inbox and reviewing-plan for auto-plan
            # Don't include "requested-input" (waiting for answers) or "approved" (ready for impl)
            plan_stages = ["ideating", "plan-inbox", "reviewing-plan"]
            for s in plan_stages:
                found = FeatureFile.list_all(board=board, stage=s)
                candidates.extend(found)
                stage_counts[s] = len(found)

            logger.info(f"[auto-plan] {board}: {', '.join(f'{s}={n}' for s, n in stage_counts.items())}")
            if self._auto_plan_queued:
                logger.info(f"[auto-plan] Currently queued: {self._auto_plan_queued}")
            if self._auto_plan_fail_cooldown:
                logger.info(f"[auto-plan] In cooldown: {list(self._auto_plan_fail_cooldown.keys())}")
            
            if not candidates:
                continue
            
            # Sort by most recently updated first (prefer items closer to completion)
            candidates.sort(key=lambda f: f._path.stat().st_mtime, reverse=True)
                
            for f in candidates:
                if f.slug in self._auto_plan_queued:
                    logger.info(f"[auto-plan] Skipping {f.slug} - already in queued set")
                    continue
                # Skip items in cooldown after failure
                if f.slug in self._auto_plan_fail_cooldown:
                    if now < self._auto_plan_fail_cooldown[f.slug]:
                        logger.info(f"[auto-plan] Skipping {f.slug} - in failure cooldown")
                        continue
                    else:
                        del self._auto_plan_fail_cooldown[f.slug]
                # Skip items that have exceeded max retries
                if self._auto_plan_fail_count.get(f.slug, 0) >= self.AUTO_MAX_RETRIES:
                    logger.info(f"[auto-plan] Skipping {f.slug} - exceeded max retries ({self.AUTO_MAX_RETRIES})")
                    continue
                self._last_auto_plan_time = now
                self._auto_plan_queued.add(f.slug)
                logger.info(f"[auto-plan] Starting: {f.title} ({f.current_stage})")
                self._auto_plan_feature(f)
                break
            else:
                continue
            break

    def _auto_implement_check(self) -> None:
        """Check for approved features and auto-run implementation if enabled.
        
        Checks phases in reverse order so we can pick up where we left off
        after rate limiting or interruption:
        - review (has fix feedback to apply)
        - testing 
        - implementing
        - spec-writing
        - approved (start fresh)
        """
        # Don't run if an implement pipeline is already running
        if hasattr(self, '_implement_running') and self._implement_running:
            logger.info("[auto-impl] Skipping - implementation already in progress")
            return
        
        # Check if impl lock is held by another process
        if self._pipeline_lock.check('impl'):
            logger.info("[auto-impl] Skipping - impl lock held by another process")
            return
        
        now = time.time()
        if self._last_auto_implement_time is not None:
            elapsed = now - self._last_auto_implement_time
            if elapsed < self.AUTO_RUN_INTERVAL:
                return

        logger.info(f"[auto-impl] Checking boards: {self._config.boards}")
        
        for board in self._config.boards:
            # Check in reverse order to pick up from where we left off
            for stage in ["review", "testing", "implementing", "spec-writing", "approved"]:
                candidates = FeatureFile.list_all(board=board, stage=stage)
                # Sort by most recently updated first
                candidates.sort(key=lambda f: f._path.stat().st_mtime, reverse=True)
                logger.info(f"[auto-impl] Board '{board}', stage '{stage}': {len(candidates)} features")
                for f in candidates:
                    if f.slug in self._auto_implement_queued:
                        logger.info(f"[auto-impl] Skipping {f.slug} - already in queued set")
                        continue
                    # Skip items in cooldown after failure
                    if f.slug in self._auto_implement_fail_cooldown:
                        if now < self._auto_implement_fail_cooldown[f.slug]:
                            logger.info(f"[auto-impl] Skipping {f.slug} - in failure cooldown")
                            continue
                        else:
                            del self._auto_implement_fail_cooldown[f.slug]
                    # Skip items that have exceeded max retries
                    if self._auto_implement_fail_count.get(f.slug, 0) >= self.AUTO_MAX_RETRIES:
                        logger.info(f"[auto-impl] Skipping {f.slug} - exceeded max retries ({self.AUTO_MAX_RETRIES})")
                        continue
                    self._last_auto_implement_time = now
                    self._auto_implement_queued.add(f.slug)
                    logger.info(f"[auto-impl] Starting: {f.title} ({f.current_stage})")
                    self._auto_implement_feature(f)
                    return  # Only process one feature at a time
            if self._auto_implement_queued:
                logger.info(f"[auto-impl] Currently queued: {self._auto_implement_queued}")
            if self._auto_implement_fail_cooldown:
                logger.info(f"[auto-impl] In cooldown: {list(self._auto_implement_fail_cooldown.keys())}")

    def _auto_plan_feature(self, feature: FeatureFile) -> None:
        """Auto-run planning or ideation on a feature."""
        # Route to different handler based on stage
        if feature.current_stage == "ideating":
            self._log_line(f"=== Auto-Ideating: {feature.title} ===")
            self._log_line(f"Stage: {feature.current_stage}")
            self._log_line("")
            self._plan_running = True
            self._run_ideating_async(feature)
        else:
            self._log_line(f"=== Auto-Planning: {feature.title} ===")
            self._log_line(f"Stage: {feature.current_stage}")
            self._log_line("")
            self._plan_running = True
            self._run_plan_async(feature)

    def _auto_implement_feature(self, feature: FeatureFile) -> None:
        """Auto-run implementation on a feature."""
        self._log_line(f"=== Auto-Implementing: {feature.title} ===")
        self._log_line(f"Stage: {feature.current_stage}")
        self._log_line("")
        self._implement_running = True
        self._run_implement_async(feature)

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

    def action_plan_only(self) -> None:
        """Run planning phase only on selected feature."""
        self.action_run_plan_only()

    def action_implement_only(self) -> None:
        """Run implementation phase only on selected feature."""
        self.action_run_implement_only()

    def action_run_plan_only(self) -> None:
        """Run planning phase only on selected feature."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        if f.current_stage not in STAGE_ACTIONS["plan"]:
            self.notify(
                f"Cannot run plan from {f.current_stage} "
                f"(allowed: {', '.join(STAGE_ACTIONS['plan'])})",
                severity="warning",
            )
            return
        if self._plan_running:
            self.notify("Plan pipeline already running", severity="warning")
            return

        self._show_log()
        self._log_line(f"=== Running PLAN only for: {f.title} ===")
        self._log_line(f"Stage: {f.current_stage}")
        self._log_line("")
        self._run_plan_async(f)

    def action_run_implement_only(self) -> None:
        """Run implementation phase only on selected feature."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return
        if f.current_stage not in STAGE_ACTIONS["implement"]:
            self.notify(
                f"Cannot run implementation from {f.current_stage} "
                f"(allowed: {', '.join(STAGE_ACTIONS['implement'])})",
                severity="warning",
            )
            return
        if self._implement_running:
            self.notify("Implement pipeline already running", severity="warning")
            return

        self._show_log()
        self._log_line(f"=== Running IMPLEMENT only for: {f.title} ===")
        self._log_line(f"Stage: {f.current_stage}")
        self._log_line("")
        self._run_implement_async(f)

    @work(thread=True, exclusive=True, group="pipeline")
    def _run_pipeline_async(self, feature: FeatureFile) -> None:
        """Run the pipeline in a background thread."""
        # Choose status and running flag based on feature stage
        if feature.current_stage in ("plan-inbox", "reviewing-plan", "requested-input", "approved", "spec-writing"):
            status = self._plan_agent_status
            self._plan_running = True
            self.running = True
        else:
            status = self._implement_agent_status
            self._implement_running = True
            self.running = True
        
        runner = AgentRunner(self._config)
        
        # Reset and populate agent status for this run
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
            self._plan_running = False
            self._implement_running = False
            status.running = False
            if feature.current_stage in ("plan-inbox", "reviewing-plan", "requested-input", "approved", "spec-writing"):
                self._auto_plan_queued.discard(feature.slug)
            else:
                self._auto_implement_queued.discard(feature.slug)
            # Determine which callback based on stage
            if feature.current_stage in ("plan-inbox", "reviewing-plan", "requested-input", "approved", "spec-writing"):
                self.app.call_from_thread(self._on_plan_done, feature.slug)
            else:
                self.app.call_from_thread(self._on_implement_done, feature.slug)

    @work(thread=True, exclusive=True, group="plan")
    def _run_plan_async(self, feature: FeatureFile) -> None:
        """Run planning phase in background thread."""
        import traceback
        
        # Acquire plan lock
        if not self._pipeline_lock.acquire('plan'):
            info = self._pipeline_lock.check('plan')
            if info:
                self.app.call_from_thread(
                    self._log_line,
                    f"[plan] Skipped - locked by PID {info.pid} on {info.hostname}"
                )
            else:
                self.app.call_from_thread(
                    self._log_line,
                    "[plan] Skipped - lock held by another process"
                )
            self._plan_running = False
            self._auto_plan_queued.discard(feature.slug)
            return
        
        self.running = True
        self._plan_running = True
        runner = AgentRunner(self._config)
        
        status = self._plan_agent_status
        status.lines = []
        status.phase = ""
        status.started_at = 0.0
        status.agent = runner.agent.name
        status.feature_slug = feature.slug
        status.running = True
        
        try:
            from phases import run_planning, run_plan_review
            # Run planning loop (max 3 retries)
            max_retries = 3
            retry_count = 0
            while feature.current_stage in ("plan-inbox", "requested-input") and retry_count < max_retries:
                plan_complete = run_planning(feature, runner, status=status)
                retry_count += 1
                if not plan_complete:
                    self.app.call_from_thread(self._log_line, f"[plan] Planning failed (attempt {retry_count})")
                    break
                if feature.current_stage != "reviewing-plan":
                    break
            # Run plan review if in reviewing-plan
            if feature.current_stage == "reviewing-plan":
                verdict, feedback = run_plan_review(feature, runner, status=status)
                if verdict == "PASS":
                    feature.move_to_stage("approved")
                    feature.add_history("PROMOTED", "Plan auto-approved")
                    feature.save()
                    self.app.call_from_thread(self._log_line, "=== Plan approved ===")
                else:
                    # Plan review failed - move back to plan-inbox with feedback
                    feature.add_history("PLAN_REVIEW_FAILED", f"Feedback: {feedback}")
                    feature.save()
                    feature.move_to_stage("plan-inbox")
                    self.app.call_from_thread(self._log_line, f"=== Plan rejected: {feedback[:100]}... ===")
            self.app.call_from_thread(self._log_line, "=== Planning completed ===")
            self._auto_plan_fail_count.pop(feature.slug, None)
            self._auto_plan_fail_cooldown.pop(feature.slug, None)
        except RateLimitError as e:
            self.app.call_from_thread(self._log_line, f"=== Rate limited: {e} ===")
            self.app.call_from_thread(self._handle_rate_limit, "plan")
            self._auto_plan_fail_count[feature.slug] = self._auto_plan_fail_count.get(feature.slug, 0) + 1
            self._auto_plan_fail_cooldown[feature.slug] = time.time() + self.AUTO_RETRY_COOLDOWN
        except Exception as e:
            tb = traceback.format_exc()
            self.app.call_from_thread(self._log_line, f"=== Planning failed: {e} ===")
            self.app.call_from_thread(self._log_line, f"=== Traceback:\n{tb} ===")
            self._auto_plan_fail_count[feature.slug] = self._auto_plan_fail_count.get(feature.slug, 0) + 1
            self._auto_plan_fail_cooldown[feature.slug] = time.time() + self.AUTO_RETRY_COOLDOWN
        finally:
            self.running = False
            self._plan_running = False
            self._auto_plan_queued.discard(feature.slug)
            status.running = False
            self._pipeline_lock.release('plan')
            restart = status.restart_requested
            status.restart_requested = False
            status.kill_requested = False
            if restart:
                refreshed = FeatureFile.find(feature.slug)
                if refreshed:
                    stage = refreshed.current_stage
                    if stage in ("plan-inbox", "reviewing-plan", "requested-input", "approved"):
                        self.app.call_from_thread(self._log_line, f"[user] Restarting plan agent for {feature.slug}")
                        self.app.call_from_thread(self._refresh_board, self.active_board)
                        self._run_plan_async(refreshed)
                        return

    @work(thread=True, exclusive=True, group="plan")
    def _run_ideating_async(self, feature: FeatureFile) -> None:
        """Run ideation phase in background thread."""
        import traceback
        
        # Acquire plan lock
        if not self._pipeline_lock.acquire('plan'):
            info = self._pipeline_lock.check('plan')
            if info:
                self.app.call_from_thread(
                    self._log_line,
                    f"[ideating] Skipped - locked by PID {info.pid} on {info.hostname}"
                )
            else:
                self.app.call_from_thread(
                    self._log_line,
                    "[ideating] Skipped - lock held by another process"
                )
            self._plan_running = False
            self._auto_plan_queued.discard(feature.slug)
            return
        
        self.running = True
        self._plan_running = True
        runner = AgentRunner(self._config)
        
        status = self._plan_agent_status
        status.lines = []
        status.phase = ""
        status.started_at = 0.0
        status.agent = runner.agent.name
        status.feature_slug = feature.slug
        status.running = True
        
        try:
            from phases import run_ideating
            run_ideating(feature, runner, status=status)
            self.app.call_from_thread(self._log_line, "=== Ideation complete ===")
            self._auto_plan_fail_count.pop(feature.slug, None)
            self._auto_plan_fail_cooldown.pop(feature.slug, None)
        except RateLimitError as e:
            self.app.call_from_thread(self._log_line, f"=== Rate limited: {e} ===")
            self.app.call_from_thread(self._handle_rate_limit, "plan")
            self._auto_plan_fail_count[feature.slug] = self._auto_plan_fail_count.get(feature.slug, 0) + 1
            self._auto_plan_fail_cooldown[feature.slug] = time.time() + self.AUTO_RETRY_COOLDOWN
        except Exception as e:
            tb = traceback.format_exc()
            self.app.call_from_thread(self._log_line, f"=== Ideation failed: {e} ===")
            self.app.call_from_thread(self._log_line, f"=== Traceback:\n{tb} ===")
            self._auto_plan_fail_count[feature.slug] = self._auto_plan_fail_count.get(feature.slug, 0) + 1
            self._auto_plan_fail_cooldown[feature.slug] = time.time() + self.AUTO_RETRY_COOLDOWN
        finally:
            self.running = False
            self._plan_running = False
            self._auto_plan_queued.discard(feature.slug)
            status.running = False
            self._pipeline_lock.release('plan')
            status.restart_requested = False
            status.kill_requested = False
            self.app.call_from_thread(self._refresh_board, self.active_board)

    @work(thread=True, exclusive=True, group="implement")
    def _run_implement_async(self, feature: FeatureFile) -> None:
        """Run implementation phase in background thread, starting from current stage."""
        
        # Acquire impl lock
        if not self._pipeline_lock.acquire('impl'):
            info = self._pipeline_lock.check('impl')
            if info:
                self.app.call_from_thread(
                    self._log_line,
                    f"[impl] Skipped - locked by PID {info.pid} on {info.hostname}"
                )
            else:
                self.app.call_from_thread(
                    self._log_line,
                    "[impl] Skipped - lock held by another process"
                )
            self._implement_running = False
            self._auto_implement_queued.discard(feature.slug)
            return
        
        self.running = True
        self._implement_running = True
        runner = AgentRunner(self._config)
        
        status = self._implement_agent_status
        status.lines = []
        status.phase = ""
        status.started_at = 0.0
        status.agent = runner.agent.name
        status.feature_slug = feature.slug
        status.running = True
        
        try:
            from phases import run_spec_writing, run_implementing, run_verify_tests, run_review_impl, run_pipeline_from_implementing
            
            # Start from the feature's current stage
            stage = feature.current_stage
            self.app.call_from_thread(self._log_line, f"[impl] Starting from stage: {stage} for {feature.title}")
            
            if stage == "spec-writing" or stage == "approved":
                # Start from spec writing
                self.app.call_from_thread(self._log_line, f"[impl] Starting spec_writing for {feature.title}")
                run_spec_writing(feature, runner, status=status)
                
            # After spec_writing (or if already past it), run implementing
            if feature.current_stage != "testing" and feature.current_stage != "review" and feature.current_stage != "final-human-approval":
                self.app.call_from_thread(self._log_line, f"[impl] Starting implementing (current stage: {feature.current_stage})")
                run_implementing(feature, runner, status=status)
            
            # After implementing, run tests
            if feature.current_stage != "review" and feature.current_stage != "final-human-approval":
                self.app.call_from_thread(self._log_line, f"[impl] After implementing, stage: {feature.current_stage}")
                self.app.call_from_thread(self._log_line, f"[impl] Starting verify_tests")
                test_verdict, test_fb = run_verify_tests(feature, runner, status=status)
            
            # Run review (if in review stage or after tests)
            if feature.current_stage == "review" and feature.current_stage != "final-human-approval":
                self.app.call_from_thread(self._log_line, f"[impl] Running review for {feature.title}")
                verdict, _ = run_review_impl(feature, runner, status=status)
                self.app.call_from_thread(self._log_line, f"[impl] Review verdict: {verdict}")
                if verdict == "PASS":
                    feature.move_to_stage("final-human-approval")
                    feature.add_history("PROMOTED", "Implementation approved")
                    feature.save()
                    self.app.call_from_thread(self._log_line, f"[impl] Moved to final-human-approval")
            elif feature.current_stage != "final-human-approval":
                # Also run review if not in review yet (for features coming from testing)
                self.app.call_from_thread(self._log_line, f"[impl] Starting review_impl")
                verdict, _ = run_review_impl(feature, runner, status=status)
                self.app.call_from_thread(self._log_line, f"[impl] Review verdict: {verdict}")
                if verdict == "PASS":
                    feature.move_to_stage("final-human-approval")
                    feature.add_history("PROMOTED", "Implementation approved")
                    feature.save()
                    self.app.call_from_thread(self._log_line, f"[impl] Moved to final-human-approval")
            self.app.call_from_thread(self._log_line, "=== Implementation completed ===")
            self._auto_implement_fail_count.pop(feature.slug, None)
            self._auto_implement_fail_cooldown.pop(feature.slug, None)
        except RateLimitError as e:
            self.app.call_from_thread(self._log_line, f"=== Rate limited: {e} ===")
            self.app.call_from_thread(self._handle_rate_limit, "implement")
            self._auto_implement_fail_count[feature.slug] = self._auto_implement_fail_count.get(feature.slug, 0) + 1
            self._auto_implement_fail_cooldown[feature.slug] = time.time() + self.AUTO_RETRY_COOLDOWN
        except Exception as e:
            self.app.call_from_thread(self._log_line, f"=== Implementation failed: {e} ===")
            self._auto_implement_fail_count[feature.slug] = self._auto_implement_fail_count.get(feature.slug, 0) + 1
            self._auto_implement_fail_cooldown[feature.slug] = time.time() + self.AUTO_RETRY_COOLDOWN
        finally:
            self.running = False
            self._implement_running = False
            status.running = False
            self._auto_implement_queued.discard(feature.slug)
            self._pipeline_lock.release('impl')
            restart = status.restart_requested
            status.restart_requested = False
            status.kill_requested = False
            if restart:
                refreshed = FeatureFile.find(feature.slug)
                if refreshed:
                    stage = refreshed.current_stage
                    if stage in ("plan-inbox", "reviewing-plan", "requested-input", "approved"):
                        self.app.call_from_thread(self._log_line, f"[user] Restarting as plan agent for {feature.slug} (stage: {stage})")
                        self.app.call_from_thread(self._refresh_board, self.active_board)
                        self._run_plan_async(refreshed)
                        return
                    elif stage in ("approved", "spec-writing", "implementing", "testing"):
                        self.app.call_from_thread(self._log_line, f"[user] Restarting implement agent for {feature.slug}")
                        self.app.call_from_thread(self._refresh_board, self.active_board)
                        self._run_implement_async(refreshed)
                        return
            self.app.call_from_thread(self._refresh_board, self.active_board)

    def _on_plan_done(self, slug: str) -> None:
        """Called on main thread after planning completes."""
        refreshed = FeatureFile.find(slug)
        if refreshed:
            self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()
        self._show_detail()
        self.notify("Planning finished", severity="information")

    def _on_implement_done(self, slug: str) -> None:
        """Called on main thread after implementation completes."""
        refreshed = FeatureFile.find(slug)
        if refreshed:
            self.selected_feature = refreshed
        self._refresh_board(self.active_board)
        self._update_detail_view()
        self._show_detail()
        self.notify("Implementation finished", severity="information")

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

    def action_edit_done_script(self) -> None:
        """Open modal to edit the done script for the selected feature."""
        f = self.selected_feature
        if not f:
            self.notify("No feature selected", severity="warning")
            return

        def handle_edit_done_script(result: str | None) -> None:
            if result is None:
                return  # Cancelled
            f.set_done_script(result)
            self.notify(f"Done script updated", severity="information")
            self._update_detail_view()

        self.push_screen(
            EditDoneScriptModal(f.done_script, self._scripts),
            callback=handle_edit_done_script,
        )

    def action_new_feature(self) -> None:
        """Open modal to create a new feature."""
        boards = self._config.boards
        default = self.active_board or (boards[0] if boards else "")

        def handle_new(result: tuple[str, str, str, str, str, bool] | None) -> None:
            if result is None:
                return  # Cancelled
            board, title, desc, done_script, item_type, requires_approval = result
            self._create_feature(board, title, desc, done_script, item_type, requires_approval)

        self.push_screen(
            NewFeatureModal(boards, default, self._scripts),
            callback=handle_new,
        )

    @work(thread=True, exclusive=True, group="create")
    def _create_feature(self, board: str, title: str, desc: str, done_script: str = "", item_type: str = "feature", requires_approval: bool = False) -> None:
        """Create a feature file in a background thread, then hand off to interactive planning."""
        try:
            feature = FeatureFile.create(board, title, desc, item_type=item_type, done_script=done_script, requires_human_approval=requires_approval)
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
        """Called on main thread after feature file is created."""
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
        self.notify(f"Feature created: {slug}", severity="information")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _acquire_lock(force: bool = False) -> PipelineLock:
    """Acquire a TUI lock. If another TUI is running in tmux, attach to it instead."""
    config = Config()
    lock = PipelineLock(config)
    
    if lock.acquire('tui', force=force):
        return lock
    
    # Lock acquisition failed — another TUI is running
    info = lock.check('tui')
    if not info:
        print("Another pipeline instance is already running.")
        print("If this is stale, run: pipeline lock clear tui")
        sys.exit(1)
    
    # Try to attach to tmux session if tmux is available
    if shutil.which("tmux"):
        project_dir = config.code_path or Path.cwd()
        from service import tmux_session_name
        session = tmux_session_name(project_dir)
        
        # Check if the tmux session exists
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True
        )
        
        if result.returncode == 0:
            # Session exists — attach or switch
            print(f"TUI is already running in tmux session '{session}'. Attaching...")
            
            if os.environ.get("TMUX"):
                # Already inside tmux — use switch-client
                attach_result = subprocess.run(
                    ["tmux", "switch-client", "-t", session]
                )
            else:
                # Not inside tmux — use attach-session
                attach_result = subprocess.run(
                    ["tmux", "attach-session", "-t", session]
                )
            
            if attach_result.returncode == 0:
                sys.exit(0)
            else:
                print(f"Failed to attach to tmux session '{session}'.")
                print("The session may have exited. Try again.")
                sys.exit(1)
    
    # No tmux session found (or tmux not installed) — show lock error
    print(f"Another TUI instance is already running (PID {info.pid} on {info.hostname}).")
    print(f"Started at {info.timestamp} by {info.username}")
    print(f"The running TUI is not accessible via tmux.")
    print(f"Use --force to start a new instance, or run: pipeline lock clear tui")
    sys.exit(1)


def _release_lock(lock: PipelineLock):
    """Release the lock unless we're in tmux mode (keep for reattach)."""
    if os.environ.get("TMUX"):
        return
    try:
        lock.release('tui')
    except Exception as e:
        logger.error(f"Failed to release lock: {e}")


def _tmux_signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT in tmux mode - detach instead of exit."""
    if os.environ.get("TMUX"):
        subprocess.run(["tmux", "detach-client"], capture_output=True)
        print(f"\nDetached from tmux (signal {signum}). Run 'pipeline tui' to reconnect.")
        while True:
            time.sleep(3600)
    else:
        sys.exit(0)


def run_tui(force: bool = False):
    signal.signal(signal.SIGTERM, _tmux_signal_handler)
    signal.signal(signal.SIGINT, _tmux_signal_handler)
    lock = _acquire_lock(force=force)
    atexit.register(_release_lock, lock)
    app = PipelineApp()
    app.run()
    _release_lock(lock)


if __name__ == "__main__":
    if handle_cli_commands(sys.argv):
        sys.exit(0)
    run_tui()
