"""Automated phase orchestration for the MAD pipeline.

Each phase function takes a FeatureFile and AgentRunner, runs the appropriate
headless agent call, updates the feature file, and moves it to the next stage.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from agent_status import AgentStatus
from config import Config, get_mad_dir
from runner import AgentRunner
from state import FeatureFile

# Set up logging - use code_path if set, otherwise use mad_dir from cwd
_config = Config()
_log_dir = (_config.code_path / ".mad" / "logs" if _config.code_path else get_mad_dir() / "logs")
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "pipeline.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
    ]
)
logger = logging.getLogger("pipeline")

# Redirect console.print to logger to avoid cluttering terminal
class _LogConsole:
    def print(self, msg, **kwargs):
        # Extract the message text from rich formatting
        import re
        # Strip rich markup like [bold], [dim], etc.
        clean_msg = re.sub(r'\[/?[a-z0-9]+\]', '', str(msg))
        logger.info(clean_msg.strip())

console = _LogConsole()

PROMPTS_DIR = Path(__file__).parent / "prompts"


PHASE_CONFIG = {
    "planning": {
        "runner_phase": "planning",
        "history_tag": "PLANNING",
        "status_label": "planning",
        "template": "plan-headless.md",
        "done_marker": "DONE",
    },
    "reviewing_plan": {
        "runner_phase": "reviewing_plan",
        "history_tag": "PLAN_REVIEW",
        "status_label": "reviewing-plan",
        "template": "review-plan.md",
        "done_marker": "DONE",
    },
    "spec_impl": {
        "runner_phase": "spec_writing",
        "history_tag": "SPEC_WRITING",
        "status_label": "spec: implementation",
        "template": "impl-spec.md",
        "done_marker": "DONE",
    },
    "spec_test": {
        "runner_phase": "spec_writing",
        "history_tag": "SPEC_WRITING",
        "status_label": "spec: tests",
        "template": "test-spec.md",
        "done_marker": "TESTDONE",
    },
    "implementing": {
        "runner_phase": "implementing",
        "history_tag": "IMPLEMENTING",
        "status_label": "implementing",
        "template": "implement.md",
        "done_marker": "DONE",
    },
    "fix_feedback": {
        "runner_phase": "fix_feedback",
        "history_tag": "FIX_FEEDBACK",
        "status_label": "fixing feedback",
        "template": "fix-feedback.md",
        "done_marker": "DONE",
    },
    "writing_tests": {
        "runner_phase": "testing",
        "history_tag": "TESTING",
        "status_label": "writing tests",
        "template": "write-tests.md",
        "done_marker": "DONE",
    },
    "review_impl": {
        "runner_phase": "review",
        "history_tag": "REVIEW",
        "status_label": "review",
        "template": "review-impl.md",
        "done_marker": "DONE",
    },
}


assert all(cfg["history_tag"].replace("_", "").isalpha() for cfg in PHASE_CONFIG.values()), "Invalid history_tag in PHASE_CONFIG"


def _build_prompt(template_name: str, replacements: dict[str, str], phase_key: Optional[str] = None) -> str:
    """Load a prompt template and apply variable substitutions.
    
    Raises FileNotFoundError if template doesn't exist.
    """
    path = PROMPTS_DIR / template_name
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    template = path.read_text()
    
    checkpoint_path = PROMPTS_DIR / "_checkpoint-instructions.md"
    if checkpoint_path.exists() and "{checkpoint_instructions}" in template:
        checkpoint_text = checkpoint_path.read_text()
        if phase_key and phase_key in PHASE_CONFIG:
            checkpoint_text = checkpoint_text.replace("{done_marker}", PHASE_CONFIG[phase_key]["done_marker"])
        template = template.replace("{checkpoint_instructions}", checkpoint_text)
    
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _run_phase(
    phase_key: str,
    feature: FeatureFile,
    runner: AgentRunner,
    prompt: str,
    status: Optional[AgentStatus] = None,
    start_message: str = "",
    skip_history_start: bool = False,
) -> str:
    """Execute a phase: update status, log, add history, call headless, return output.
    
    Args:
        phase_key: Key into PHASE_CONFIG
        feature: The feature being processed
        runner: AgentRunner (already scoped via .for_phase() by caller)
        prompt: The fully-built prompt string
        status: Optional AgentStatus to update
        start_message: Custom start message (defaults to 'Starting {history_tag}')
        skip_history_start: If True, skip the initial add_history/save (for sub-phases)
    
    Returns:
        The raw output string from runner.headless()
    
    Raises:
        RuntimeError: If runner.headless() returns empty output
    """
    config = PHASE_CONFIG[phase_key]
    
    if not skip_history_start:
        msg = start_message or f"Starting {config['history_tag'].lower().replace('_', ' ')}"
        feature.add_history(config["history_tag"], msg)
        feature.save()
    
    if status is not None:
        status.phase = config["status_label"]
        status.agent = runner.agent.name
    
    logger.info(f"[{config['runner_phase']}] Running: {feature.title}")
    
    try:
        output = runner.headless(prompt, status=status)
    except Exception as e:
        logger.error(f"[{config['runner_phase']}] headless() failed for {feature.title}: {e}")
        feature.add_history(config["history_tag"], f"FAILED: {e}")
        feature.save()
        raise
    
    if not output or not output.strip():
        logger.error(f"[{config['runner_phase']}] Empty output for {feature.title}")
        feature.add_history(config["history_tag"], "FAILED: Empty output from agent")
        feature.save()
        raise RuntimeError(f"Phase {phase_key} produced empty output for {feature.title}")
    
    return output


def _parse_json_output(output: str, field: str) -> str:
    """Parse JSON output from agent and extract a specific field.
    
    Args:
        output: The raw output from the agent (JSON string)
        field: The field name to extract (e.g., "implementation_spec", "test_spec")
    
    Returns:
        The extracted field value, or empty string if not found
    """
    import json
    import re
    
    if not output or not output.strip():
        return ""
    
    try:
        # Find JSON in output
        json_match = re.search(r'\{[\s\S]*\}', output)
        if json_match:
            result = json.loads(json_match.group())
            if field in result and result[field]:
                return str(result[field])
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    
    return ""


def _parse_verdict(output: str) -> tuple[str, str]:
    """Parse verdict and feedback from review output.
    
    Tries JSON first, falls back to text parsing.
    Returns (verdict, feedback) where verdict is 'PASS' or 'FAIL'.
    """
    import json
    import re
    
    verdict = "FAIL"
    feedback = output.strip()
    
    try:
        json_match = re.search(r'\{[\s\S]*\}', output)
        if json_match:
            result = json.loads(json_match.group())
            if "verdict" in result:
                if "PASS" in result["verdict"].upper():
                    verdict = "PASS"
            if "feedback" in result and result["feedback"]:
                feedback = str(result["feedback"])
            elif "feedback" in result:
                feedback = ""
    except (json.JSONDecodeError, AttributeError):
        verdict_match = re.search(r"\*?\*?VERDICT\*?\*?:\s*(\w+)", output, re.IGNORECASE)
        if verdict_match:
            if "PASS" in verdict_match.group(1).upper():
                verdict = "PASS"
        feedback_match = re.search(r"\*?\*?FEEDBACK\*?\*?:\s*(.+)", output, re.DOTALL)
        if feedback_match:
            feedback = feedback_match.group(1).strip()
        elif verdict == "FAIL":
            verdict_pos = output.upper().find("VERDICT:")
            if verdict_pos >= 0:
                feedback = output[verdict_pos + 8:].strip()
    
    feedback = _strip_markdown(feedback)
    return verdict, feedback


def _git_commit(feature: FeatureFile, stage: str) -> None:
    """Commit current pipeline state to git for safety."""
    try:
        repo_root = Path(__file__).parent.parent
        # Stage the feature file and any changes in .mad/boards
        subprocess.run(
            ["git", "add", "-A", ".mad/boards/", ".mad/logs/"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=repo_root,
            capture_output=True,
        )
        if result.returncode == 0:
            logger.info(f"[git] No changes to commit for {feature.title}")
            return
        
        # Commit with descriptive message
        msg = f"MAD: {feature.title} - {stage}"
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        logger.info(f"[git] Committed: {msg}")
    except Exception as e:
        logger.warning(f"[git] Commit failed: {e}")


def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts/ directory."""
    path = PROMPTS_DIR / name
    return path.read_text()


def _delete_checkpoint(feature: FeatureFile) -> None:
    """Delete checkpoint file for a feature. Safe to call even if no checkpoint exists."""
    config = Config()
    checkpoint_dir = config.mad_dir / 'checkpoints'
    checkpoint_path = checkpoint_dir / f'{feature.slug}.checkpoint.json'
    try:
        checkpoint_path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f'[checkpoint] Failed to delete {checkpoint_path}: {e}')


def _strip_markdown(text: str) -> str:
    """Strip common markdown formatting for clean plain-text storage."""
    import re
    # Remove bold/italic markers **text* and *text*
    text = re.sub(r"\*+\s*(.+?)\s*\*+", r"\1", text)
    # Remove headers # Header
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    # Remove code blocks ```code```
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code `code`
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove bullet points - and *
    text = re.sub(r"^[\-\*]\s+", "", text, flags=re.MULTILINE)
    # Remove numbered lists 1. 2.
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    # Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _get_latest_feedback(feature: FeatureFile) -> str:
    """Extract the most recent review feedback from feature history."""
    if not feature.history:
        return "No previous feedback available."
    
    # Find the last REVIEW entry with feedback
    import json
    try:
        history = json.loads(feature.history) if isinstance(feature.history, str) else feature.history
    except (json.JSONDecodeError, TypeError):
        # Fallback: try to find it in the raw text
        return feature.history if feature.history else "No previous feedback available."
    
    # Find the last REVIEW entry
    for entry in reversed(history):
        if entry.get("stage") == "REVIEW" and entry.get("note"):
            note = entry.get("note", "")
            # Extract feedback after "Verdict: FAIL — "
            if "Verdict: FAIL" in note:
                parts = note.split("Verdict: FAIL", 1)
                if len(parts) > 1:
                    return parts[1].strip()
            return note
    return "No previous feedback available."


def run_planning(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> bool:
    """Generate a plan for the feature.
    
    Returns True if plan is complete, False if human input is needed (questions).
    """
    import sys
    
    runner = runner.for_phase("planning")
    # console.print(f"\n[bold blue]Planning:[/bold blue] {feature.title}")
    logger.info(f"[planning] Starting: {feature.title}")
    feature.add_history("PLANNING", "Starting planning")
    feature.save()
    print(f"[DEBUG] Starting planning for {feature.title}", file=sys.stderr)
    sys.stderr.flush()

    # Check if there are previous answers to include
    questions = feature.questions
    questions_context = ""
    if questions:
        answered = [q for q in questions if q.get("answer")]
        if answered:
            questions_context = "\n\n## Previous Answers:\n"
            for q in answered:
                questions_context += f"- Q: {q['question']}\n  A: {q['answer']}\n"

    # Check if there's previous review feedback to include
    feedback_context = ""
    latest_feedback = _get_latest_feedback(feature)
    if latest_feedback and latest_feedback != "No previous feedback available.":
        feedback_context = f"\n\n## Previous Review Feedback (you MUST address these issues):\n{latest_feedback}\n"

    template = _load_prompt("plan-headless.md")
    # Use string replace instead of .format() to avoid issues with curly braces in template
    prompt = template.replace("{title}", feature.title)
    prompt = prompt.replace("{description}", feature.get_section("Description") or "(no description)")
    prompt = prompt.replace("{feature_slug}", feature.slug)
    prompt = prompt.replace("{feature_id}", feature.id)
    prompt = prompt.replace("{phase}", "planning")
    
    if questions_context:
        prompt += questions_context
    
    if feedback_context:
        prompt += feedback_context

    if status is not None:
        status.phase = "planning"
        status.agent = runner.agent.name
    print(f"[DEBUG] About to call runner.headless()", file=sys.stderr)
    sys.stderr.flush()
    output = runner.headless(prompt, status=status)
    print(f"[DEBUG] headless returned, output length: {len(output)}", file=sys.stderr)
    print(f"[DEBUG] output repr: {repr(output[:500])}", file=sys.stderr)
    sys.stderr.flush()

    # Try to parse JSON output for questions and plan
    import json
    import re
    
    console.print(f"[dim]Planning output length: {len(output)} chars[/dim]")
    
    # Extract JSON from output
    json_match = re.search(r'\{[\s\S]*\}', output)
    if json_match:
        matched = json_match.group()
        console.print(f"[dim]Matched JSON: {matched[:200]}...[/dim]")
        try:
            result = json.loads(matched)
            console.print(f"[dim]Parsed JSON keys: {list(result.keys())}[/dim]")
            
            # Handle questions - must be a list
            if "questions" in result and isinstance(result["questions"], list) and result["questions"]:
                questions = result["questions"]
                feature.set_questions(questions)
                feature.add_history("PLANNING", f"Questions raised via {runner.agent.name}")
                feature.save()
                feature.move_to_stage("requested-input")
                console.print(f"[yellow]Plan has {len(questions)} questions for human. Moving to requested-input.[/yellow]")
                return False
            
            # Handle plan - must be a string
            if "plan" in result and isinstance(result.get("plan"), str) and result["plan"]:
                feature.set_plan(result["plan"])
                feature.add_history("PLANNING", f"Plan generated via {runner.agent.name}")
                feature.save()
                feature.move_to_stage("reviewing-plan")
                console.print("[green]Plan generated. Feature moved to reviewing-plan.[/green]")
                return True
            
            console.print(f"[yellow]JSON found but no valid questions or plan. result={result}[/yellow]")
                
        except json.JSONDecodeError as e:
            console.print(f"[red]JSON decode error: {e}, matched: {matched[:100]}[/red]")
        except Exception as e:
            console.print(f"[red]Error processing planning output: {e}[/red]")
            raise
    
    # Fallback: treat entire output as plan (but not if it's just the completion marker)
    output_stripped = output.strip()
    
    # Remove the completion marker before using as fallback
    output_for_fallback = output_stripped
    for marker in ["PLAN_COMPLETE", "plan_complete"]:
        idx = output_for_fallback.upper().find(marker)
        if idx >= 0:
            output_for_fallback = output_for_fallback[:idx].strip()
    
    # Check if we only had the completion marker (no actual content)
    if not output_for_fallback:
        console.print(f"[red]Agent only output completion marker without a plan! Output: {output_stripped[:100]}[/red]")
        feature.add_history("PLANNING", f"FAILED: Agent did not produce a plan")
        feature.save()
        return False
        
    console.print("[yellow]No valid JSON found, using fallback[/yellow]")
    feature.set_plan(output_for_fallback)
    feature.add_history("PLANNING", f"Plan generated via {runner.agent.name}")
    feature.save()
    feature.move_to_stage("reviewing-plan")
    _git_commit(feature, "planning complete")
    _delete_checkpoint(feature)
    
    console.print("[green]Plan generated. Feature moved to reviewing-plan.[/green]")
    return True


def run_plan_review(
    feature: FeatureFile,
    runner: AgentRunner,
    feedback: str = "",
    status: Optional[AgentStatus] = None,
) -> tuple[str, str]:
    """Review the plan and return (verdict, feedback).
    
    If feedback is provided from a previous failed review, use it to improve the plan.
    """
    runner = runner.for_phase("reviewing_plan")
    console.print(f"\n[bold blue]Reviewing Plan:[/bold blue] {feature.title}")
    logger.info(f"[plan-review] Starting: {feature.title}")
    feature.add_history("PLAN_REVIEW", "Starting plan review")
    feature.save()

    template = _load_prompt("review-plan.md")
    
    if feedback:
        feedback_section = f"## Previous Review Feedback (must address):\n{feedback}"
    else:
        feedback_section = ""
    
    prompt = template.replace("{title}", feature.title)
    prompt = prompt.replace("{description}", feature.get_section("Description") or "(none)")
    prompt = prompt.replace("{plan}", feature.plan or "(none)")
    prompt = prompt.replace("{feedback_section}", feedback_section)

    if status is not None:
        status.phase = "reviewing-plan"
        status.agent = runner.agent.name
    output = runner.headless(prompt, status=status)

    verdict, review_feedback = _parse_verdict(output)
    
    feature.add_history("PLAN_REVIEW", f"Verdict: {verdict} — {review_feedback}")
    
    return verdict, review_feedback


def run_spec_writing(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Run two headless calls to generate implementation spec and test spec."""
    runner = runner.for_phase("spec_writing")
    console.print(f"\n[bold blue]Spec Writing:[/bold blue] {feature.title}")
    logger.info(f"[spec-writing] Starting: {feature.title}")

    feature.move_to_stage("spec-writing")
    feature.add_history("SPEC_WRITING", "Starting spec generation")
    feature.save()

    impl_prompt = _build_prompt("impl-spec.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "spec-implementation",
    }, "spec_impl")

    output = _run_phase("spec_impl", feature, runner, impl_prompt, status=status)
    
    # Parse JSON output
    impl_spec = _parse_json_output(output, "implementation_spec")
    if not impl_spec:
        impl_spec = output.strip()
    feature.set_impl_spec(impl_spec)
    feature.add_history("SPEC_WRITING", f"Implementation spec generated via {runner.agent.name}")
    feature.save()

    test_prompt = _build_prompt("test-spec.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "spec-test",
    }, "spec_test")

    output = _run_phase("spec_test", feature, runner, test_prompt, status=status, skip_history_start=True)
    
    # Parse JSON output
    test_spec = _parse_json_output(output, "test_spec")
    if not test_spec:
        test_spec = output.strip()
    feature.set_test_spec(test_spec)
    feature.add_history("SPEC_WRITING", f"Test spec generated via {runner.agent.name}")
    feature.save()

    feature.move_to_stage("implementing")
    _delete_checkpoint(feature)
    console.print("[green]Specs generated. Feature moved to implementing.[/green]")


def run_implementing(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Run the implementation phase headlessly."""
    runner = runner.for_phase("implementing")
    console.print(f"\n[bold blue]Implementing:[/bold blue] {feature.title}")
    logger.info(f"[implementing] Starting: {feature.title}")

    prompt = _build_prompt("implement.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{impl_spec}": feature.impl_spec or "(no impl spec)",
        "{test_spec}": feature.test_spec or "(no test spec)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "implementing",
    }, "implementing")

    latest_feedback = _get_latest_feedback(feature)
    if latest_feedback and latest_feedback != "No previous feedback available.":
        prompt += f"\n\n## Previous Review Feedback (you MUST address these issues):\n{latest_feedback}\n"
    
    output = _run_phase("implementing", feature, runner, prompt, status=status,
                        start_message="Starting implementation")
    feature.set_impl_notes(output.strip())
    feature.add_history("IMPLEMENTING", f"Implementation completed via {runner.agent.name}")
    feature.save()

    feature.move_to_stage("testing")
    feature.add_history("TESTING", "Moved to testing phase")
    feature.save()
    _git_commit(feature, "implementation complete")
    _delete_checkpoint(feature)
    console.print("[green]Implementation done. Feature moved to testing.[/green]")


def run_fix_feedback(
    feature: FeatureFile,
    runner: AgentRunner,
    feedback: str,
    status: Optional[AgentStatus] = None,
) -> None:
    """Run the fix-feedback phase - focuses on fixing issues from review feedback."""
    runner = runner.for_phase("fix_feedback")
    console.print(f"\n[bold blue]Fixing Review Feedback:[/bold blue] {feature.title}")

    prompt = _build_prompt("fix-feedback.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{impl_spec}": feature.impl_spec or "(no impl spec)",
        "{test_spec}": feature.test_spec or "(no test spec)",
        "{impl_notes}": feature.impl_notes or "(no impl notes)",
        "{feedback}": feedback or "(no feedback)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "fix-feedback",
    }, "fix_feedback")

    output = _run_phase("fix_feedback", feature, runner, prompt, status=status,
                        start_message="Starting fix feedback")

    feature.set_impl_notes(output.strip())
    feature.add_history("FIX_FEEDBACK", f"Fixed issues per review feedback via {runner.agent.name}")
    feature.save()

    feature.move_to_stage("testing")
    feature.add_history("TESTING", "Moved to testing phase after fixing feedback")
    feature.save()
    console.print("[green]Feedback fixed. Feature moved to testing.[/green]")


def run_writing_tests(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Run the test-writing phase headlessly."""
    runner = runner.for_phase("testing")
    console.print(f"\n[bold blue]Writing Tests:[/bold blue] {feature.title}")

    prompt = _build_prompt("write-tests.md", {
        "{title}": feature.title,
        "{test_spec}": feature.test_spec or "(no test spec)",
        "{impl_notes}": feature.impl_notes or "(no impl notes)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "writing-tests",
    }, "writing_tests")

    output = _run_phase("writing_tests", feature, runner, prompt, status=status,
                        start_message="Starting test writing")

    current_notes = feature.impl_notes
    updated = f"{current_notes}\n\n### Tests Written\n\n{output.strip()}"
    feature.set_impl_notes(updated)
    feature.add_history("TESTING", f"Tests written via {runner.agent.name}")
    feature.save()

    feature.move_to_stage("review")
    _delete_checkpoint(feature)
    console.print("[green]Tests written. Feature moved to review.[/green]")


def run_review_impl(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> tuple[str, str]:
    """Run the review phase. Returns (verdict, feedback)."""
    runner = runner.for_phase("review")
    console.print(f"\n[bold blue]Reviewing:[/bold blue] {feature.title}")
    feature.add_history("REVIEW", "Starting review")
    feature.save()

    template = _load_prompt("review-impl.md")
    prompt = template.replace("{title}", feature.title)
    prompt = prompt.replace("{plan}", feature.plan or "(no plan)")
    prompt = prompt.replace("{test_spec}", feature.test_spec or "(no test spec)")
    prompt = prompt.replace("{impl_notes}", feature.impl_notes or "(no impl notes)")

    if status is not None:
        status.phase = "review"
        status.agent = runner.agent.name
    output = runner.headless(prompt, status=status)

    verdict, feedback = _parse_verdict(output)
    
    feature.add_history("REVIEW", f"Verdict: {verdict} — {feedback}")

    console.print(f"[dim]Review verdict: {verdict}[/dim]")
    console.print(f"[dim]Review feedback: {feedback[:100]}...[/dim]" if len(feedback) > 100 else f"[dim]Review feedback: {feedback}[/dim]")

    if verdict == "PASS":
        feature.save()
        feature.move_to_stage("final-human-approval")
        _delete_checkpoint(feature)
        console.print(f"[green]Review PASSED. Feature moved to final-human-approval.[/green]")
    else:
        # Clean up old feedback first, then add new feedback
        _cleanup_impl_notes_for_retry(feature)
        current_notes = feature.impl_notes
        updated = f"{current_notes}\n\n### Review Feedback\n\n{feedback}"
        feature.set_impl_notes(updated)
        feature.save()
        feature.move_to_stage("implementing")
        _delete_checkpoint(feature)
        console.print(f"[yellow]Review FAILED. Feature moved back to implementing.[/yellow]")

    return verdict, feedback


def run_pipeline(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Orchestrate the full automated run from plan-inbox through completion.

    If feature is in plan-inbox, runs planning first.
    Then runs plan review (with retries) before moving to spec writing.
    Then runs spec_writing -> implementing -> writing_tests -> review_impl
    Retries review_impl up to 5 times on FAIL before giving up.
    """
    try:
        _run_pipeline_impl(feature, runner, status)
    except Exception as e:
        _delete_checkpoint(feature)
        raise


def _run_pipeline_impl(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Internal implementation of run_pipeline."""
    console.print(Panel(
        f"[bold]Running full pipeline for:[/bold] {feature.title}",
        border_style="cyan",
    ))

    # Phase 0: Planning loop (plan-inbox -> planning -> [requested-input | reviewing-plan])
    if feature.current_stage == "plan-inbox" or feature.current_stage == "requested-input":
        # Keep running planning until no more questions
        while feature.current_stage in ("plan-inbox", "requested-input"):
            plan_complete = run_planning(feature, runner, status=status)
            if not plan_complete:
                # Questions were raised, stop and wait for human input
                console.print("[yellow]Pipeline paused - human input needed. Run pipeline again after answering questions.[/yellow]")
                return
            # Plan complete, should be in reviewing-plan now
            if feature.current_stage != "reviewing-plan":
                break
    
    # Phase 0b: Plan review (with retries)
    if feature.current_stage == "reviewing-plan":
        max_plan_review_attempts = 3
        plan_feedback = None
        for attempt in range(1, max_plan_review_attempts + 1):
            verdict, plan_feedback = run_plan_review(
                feature, runner, feedback=plan_feedback, status=status
            )
            
            if verdict == "PASS":
                feature.move_to_stage("approved")
                feature.add_history("PROMOTED", "Plan approved, moving to spec-writing")
                feature.save()
                _git_commit(feature, "plan approved")
                break
            
            if attempt < max_plan_review_attempts:
                console.print(
                    f"[yellow]Plan review attempt {attempt}/{max_plan_review_attempts} failed. "
                    f"Retrying...[/yellow]"
                )
                # Go back to planning with feedback
                feature.move_to_stage("plan-inbox")
                feature.add_history("PLAN_REVIEW", f"Feedback: {plan_feedback}")
                feature.save()
                run_planning(feature, runner, status=status)
                # Move back to reviewing for next attempt
                feature.move_to_stage("reviewing-plan")
        else:
            # Exhausted retries
            console.print(
                f"[red]Plan review failed after {max_plan_review_attempts} attempts. "
                f"Moving to final-human-approval for manual intervention.[/red]"
            )
            feature.add_history("FINAL_HUMAN_APPROVAL", "Plan review exhausted retries")
            feature.move_to_stage("final-human-approval")
            feature.save()
            return
    
    # Phase 1: Spec writing (only runs if in approved or later)
    run_spec_writing(feature, runner, status=status)

    max_review_attempts = 5
    for attempt in range(1, max_review_attempts + 1):
        if attempt == 1:
            # First attempt: full implementation
            run_implementing(feature, runner, status=status)
        else:
            # Subsequent attempts: focus on fixing review feedback
            # Get the latest feedback from history
            feedback = _get_latest_feedback(feature)
            run_fix_feedback(feature, runner, feedback, status=status)

        # Phase 3: Writing tests
        run_writing_tests(feature, runner, status=status)

        # Phase 4: Review
        verdict, feedback = run_review_impl(feature, runner, status=status)

        if verdict == "PASS":
            console.print(Panel(
                f"[bold green]Pipeline complete![/bold green] Feature is in final-human-approval.",
                border_style="green",
            ))
            return

        if attempt < max_review_attempts:
            console.print(
                f"[yellow]Review attempt {attempt}/{max_review_attempts} failed. "
                f"Fixing feedback and retrying...[/yellow]"
            )
        else:
            console.print(
                f"[red]Review failed after {max_review_attempts} attempts. "
                f"Moving to final-human-approval for manual intervention.[/red]"
            )
            feature.add_history("FINAL_HUMAN_APPROVAL", "Pipeline exhausted review retries")
            feature.save()
            feature.move_to_stage("final-human-approval")


def _cleanup_impl_notes_for_retry(feature: FeatureFile) -> None:
    """Clean up Implementation Notes before retry - remove old accumulated feedback, keep latest.
    
    This keeps only the last "### Review Feedback" section so impl_notes doesn't grow forever.
    """
    import re
    
    impl_notes = feature.impl_notes
    if not impl_notes:
        return
    
    # Find ALL "### Review Feedback" sections and keep only the LAST one
    # This prevents infinite growth while preserving the most recent feedback
    matches = list(re.finditer(r"^### Review Feedback", impl_notes, re.MULTILINE))
    if len(matches) > 1:
        # Keep everything up to and including the last review feedback
        last_match = matches[-1]
        cleaned = impl_notes[:last_match.start()].strip()
        feature.set_impl_notes(cleaned)


def run_pipeline_from_implementing(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Resume pipeline from implementing stage.
    
    Called when a feature is in implementing (e.g., after failed review retry).
    Runs: implementing -> writing_tests -> review_impl
    Retries review up to 2 times on FAIL before giving up.
    """
    try:
        _run_pipeline_from_implementing_impl(feature, runner, status)
    except Exception as e:
        _delete_checkpoint(feature)
        raise


def _run_pipeline_from_implementing_impl(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Internal implementation of run_pipeline_from_implementing."""
    console.print(Panel(
        f"[bold]Resuming pipeline from implementing:[/bold] {feature.title}",
        border_style="cyan",
    ))

    # Clean up any old review feedback before starting
    _cleanup_impl_notes_for_retry(feature)
    
    max_review_attempts = 5
    for attempt in range(1, max_review_attempts + 1):
        # Clean up old feedback before each retry attempt
        if attempt > 1:
            _cleanup_impl_notes_for_retry(feature)
        
        if attempt == 1:
            # First attempt: full implementation
            run_implementing(feature, runner, status=status)
        else:
            # Subsequent attempts: focus on fixing review feedback
            feedback = _get_latest_feedback(feature)
            run_fix_feedback(feature, runner, feedback, status=status)

        # Phase 2: Writing tests
        run_writing_tests(feature, runner, status=status)

        # Phase 3: Review
        verdict, feedback = run_review_impl(feature, runner, status=status)

        if verdict == "PASS":
            console.print(Panel(
                f"[bold green]Pipeline complete![/bold green] Feature is in final-human-approval.",
                border_style="green",
            ))
            return

        if attempt < max_review_attempts:
            console.print(
                f"[yellow]Review attempt {attempt}/{max_review_attempts} failed. "
                f"Fixing feedback and retrying...[/yellow]"
            )
        else:
            console.print(
                f"[red]Review failed after {max_review_attempts} attempts. "
                f"Moving to final-human-approval for manual intervention.[/red]"
            )
            feature.add_history("FINAL_HUMAN_APPROVAL", "Pipeline exhausted review retries")
            feature.save()
            feature.move_to_stage("final-human-approval")


def update_design_doc(feature: FeatureFile, runner: AgentRunner) -> bool:
    """Update the design document to mark a feature as complete.
    
    Returns True if the design doc was updated, False otherwise.
    """
    runner = runner.for_phase("design_update")
    design_ref = feature.design_ref
    if not design_ref:
        return False
    
    if ":" not in design_ref:
        console.print(f"[yellow]Warning:[/yellow] Invalid design_ref format: {design_ref}")
        console.print(f"[dim]Expected format: filename:search_term[/dim]")
        return False
    
    filename, search_term = design_ref.split(":", 1)
    
    # Resolve the design doc path
    if Path(filename).is_absolute():
        design_doc_path = Path(filename)
    else:
        # Relative to the feature file's board directory
        config = Config()
        design_doc_path = config.boards_dir / feature.board / ".." / filename
        # Try relative to where pipeline is run
        if not design_doc_path.exists():
            design_doc_path = Path.cwd() / filename
        if not design_doc_path.exists():
            design_doc_path = Path.home() / filename
    
    if not design_doc_path.exists():
        console.print(f"[yellow]Warning:[/yellow] Design doc not found: {design_doc_path}")
        return False
    
    design_doc_content = design_doc_path.read_text()
    
    # Call the AI agent to update the design doc
    template = _load_prompt("update-design.md")
    prompt = template.replace("{design_doc_content}", design_doc_content)
    prompt = prompt.replace("{search_term}", search_term)
    
    console.print(f"[dim]Updating design doc: {design_doc_path.name}[/dim]")
    
    output = runner.headless(prompt)
    
    # Check the response
    if output.startswith("AMBIGUOUS:"):
        console.print(f"[yellow]Ambiguous match in design doc:[/yellow]")
        console.print(output)
        console.print("[dim]Please update the design doc manually[/dim]")
        return False
    elif output.startswith("ALREADY_COMPLETE"):
        console.print(f"[dim]Design doc item already checked off[/dim]")
        return False
    elif output.startswith("NOT_FOUND"):
        console.print(f"[yellow]Warning:[/yellow] Could not find '{search_term}' in design doc")
        return False
    else:
        # Write the updated content
        design_doc_path.write_text(output)
        console.print(f"[green]Updated design doc:[/green] {design_doc_path.name}")
        return True
