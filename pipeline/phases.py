"""Automated phase orchestration for the MAD pipeline.

Each phase function takes a FeatureFile and AgentRunner, runs the appropriate
headless agent call, updates the feature file, and moves it to the next stage.
"""

import datetime
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

logger = logging.getLogger("pipeline")
_logging_initialized = False


def _ensure_logging():
    global _logging_initialized
    if _logging_initialized:
        return
    _logging_initialized = True
    try:
        config = Config()
        log_dir = (config.code_path / ".mad" / "logs" if config.code_path else get_mad_dir() / "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "pipeline.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    except Exception:
        pass

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


def _spec_to_string(value) -> str:
    """Convert impl_spec/test_spec value to string for prompt substitution.
    
    Handles both dict (parsed JSON) and string values.
    """
    import json
    if not value:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, indent=2)
    return str(value)


PHASE_CONFIG = {
    "planning": {
        "runner_phase": "planning",
        "history_tag": "PLANNING",
        "status_label": "planning",
        "template": "plan-headless.md",
        "output_schema": {
            "questions": "list[dict] | null",
            "plan": "string | null",
        },
    },
    "reviewing_plan": {
        "runner_phase": "reviewing_plan",
        "history_tag": "PLAN_REVIEW",
        "status_label": "reviewing-plan",
        "template": "review-plan.md",
        "output_schema": {
            "verdict": "string",
            "feedback": "string | null",
        },
    },
    "spec_impl": {
        "runner_phase": "spec_writing",
        "history_tag": "SPEC_WRITING",
        "status_label": "spec: implementation",
        "template": "impl-spec.md",
        "output_schema": {
            "implementation_spec": "string",
        },
    },
    "spec_test": {
        "runner_phase": "spec_writing",
        "history_tag": "SPEC_WRITING",
        "status_label": "spec: tests",
        "template": "test-spec.md",
        "output_schema": {
            "test_spec": "string",
        },
    },
    "implementing": {
        "runner_phase": "implementing",
        "history_tag": "IMPLEMENTING",
        "status_label": "implementing",
        "template": "implement.md",
        "output_schema": {
            "summary": "string",
            "files_changed": "list[string]",
        },
    },
    "fix_feedback": {
        "runner_phase": "fix_feedback",
        "history_tag": "FIX_FEEDBACK",
        "status_label": "fixing feedback",
        "template": "fix-feedback.md",
        "output_schema": {
            "summary": "string",
            "files_changed": "list[string]",
        },
    },
    "testing": {
        "runner_phase": "testing",
        "history_tag": "TESTING",
        "status_label": "verifying tests",
        "template": "verify-tests.md",
        "output_schema": {
            "verdict": "string",
            "test_results": "dict",
            "feedback": "string | null",
        },
    },
    "review_impl": {
        "runner_phase": "review",
        "history_tag": "REVIEW",
        "status_label": "review",
        "template": "review-impl.md",
        "output_schema": {
            "verdict": "string",
            "feedback": "string | null",
        },
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
    _ensure_logging()
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
        output = runner.headless(
            prompt,
            status=status,
            phase_key=phase_key,
            output_schema=config.get("output_schema"),
        )
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


def _parse_json_output(output: str, field: str):
    """Parse JSON output from agent and extract a specific field.

    Args:
        output: The raw output from the agent (JSON string)
        field: The field name to extract (e.g., "implementation_spec", "test_spec")

    Returns:
        The extracted field value as a parsed object (dict/list) if valid JSON,
        or as a string if not valid JSON, or empty string if not found
    """
    import json
    import re

    if not output or not output.strip():
        return ""

    # Try direct JSON parse first (output file should be pure JSON)
    try:
        result = json.loads(output.strip())
        if field in result and result[field]:
            return result[field]
    except json.JSONDecodeError:
        pass

    # Fallback: regex extraction
    try:
        json_match = re.search(r'\{[\s\S]*\}', output)
        if json_match:
            result = json.loads(json_match.group())
            if field in result and result[field]:
                return result[field]
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
    
    # Try to parse JSON - direct parse first, then regex fallback
    result = None
    json_parsed = False
    try:
        result = json.loads(output.strip())
    except json.JSONDecodeError:
        try:
            json_match = re.search(r'\{[\s\S]*\}', output)
            if json_match:
                result = json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError):
            pass

    if result is not None:
        if "verdict" in result:
            json_parsed = True
            if "PASS" in result["verdict"].upper():
                verdict = "PASS"
            if "feedback" in result and result["feedback"]:
                feedback = str(result["feedback"])
            elif "feedback" in result:
                feedback = ""
        elif "tests_pass" in result or "test_results" in result:
            json_parsed = True
            tests_pass = result.get("tests_pass", None)
            test_results = result.get("test_results", {})
            failed_count = test_results.get("failed", 0) + test_results.get("errors", 0)
            if tests_pass is True or (test_results and failed_count == 0):
                verdict = "PASS"
                feedback = ""
            else:
                feedback = json.dumps(result, indent=2)

    if not json_parsed:
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
    """Extract the most recent review feedback from dedicated review arrays."""
    latest_impl = feature.get_latest_impl_review()
    if latest_impl and latest_impl.get("verdict") == "FAIL":
        return latest_impl.get("feedback", "")
    latest_plan = feature.get_latest_plan_review()
    if latest_plan and latest_plan.get("verdict") == "FAIL":
        return latest_plan.get("feedback", "")
    return "No previous feedback available."


def run_planning(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> bool:
    """Generate a plan for the feature.
    
    Returns True if plan is complete, False if human input is needed (questions).
    """
    runner = runner.for_phase("planning")
    logger.info(f"[planning] Starting: {feature.title}")
    feature.add_history("PLANNING", "Starting planning")
    feature.save()

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

    template = "plan-bug.md" if feature.item_type == "bug" else "plan-headless.md"
    prompt = _build_prompt(template, {
        "{title}": feature.title,
        "{description}": feature.get_section("Description") or "(no description)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "planning",
    }, "planning")
    
    if questions_context:
        prompt += questions_context
    
    if feedback_context:
        prompt += feedback_context

    output = _run_phase("planning", feature, runner, prompt, status=status,
                        start_message="Starting planning", skip_history_start=True)

    # Try to parse JSON output for questions and plan
    import json
    import re

    # Try direct JSON parse first, then regex fallback
    result = None
    try:
        result = json.loads(output.strip())
    except json.JSONDecodeError:
        json_match = re.search(r'\{[\s\S]*\}', output)
        if json_match:
            try:
                result = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

    if result:
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
            _git_commit(feature, "planning complete")
            _delete_checkpoint(feature)
            console.print("[green]Plan generated. Feature moved to reviewing-plan.[/green]")
            return True

        logger.warning(f"[planning] JSON found but no valid questions or plan for {feature.title}")

    # Fallback: treat entire output as plan
    output_stripped = output.strip()
    if not output_stripped:
        feature.add_history("PLANNING", "FAILED: Agent did not produce a plan")
        feature.save()
        return False

    console.print("[yellow]No valid JSON found, using fallback[/yellow]")
    feature.set_plan(output_stripped)
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

    feedback_section = f"## Previous Review Feedback (must address):\n{feedback}" if feedback else ""

    prompt = _build_prompt("review-plan.md", {
        "{title}": feature.title,
        "{description}": feature.get_section("Description") or "(none)",
        "{plan}": feature.plan or "(none)",
        "{feedback_section}": feedback_section,
    }, "reviewing_plan")

    output = _run_phase("reviewing_plan", feature, runner, prompt, status=status,
                        start_message="Starting plan review")

    verdict, review_feedback = _parse_verdict(output)

    feature.add_plan_review(verdict, review_feedback)
    feature.add_history("PLAN_REVIEW", f"Verdict: {verdict}")
    feature.save()
    _git_commit(feature, "plan review complete")
    _delete_checkpoint(feature)

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
    _git_commit(feature, "spec writing complete")
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
        "{impl_spec}": _spec_to_string(feature.impl_spec) or "(no impl spec)",
        "{test_spec}": _spec_to_string(feature.test_spec) or "(no test spec)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "implementing",
    }, "implementing")

    latest_feedback = _get_latest_feedback(feature)
    if latest_feedback and latest_feedback != "No previous feedback available.":
        prompt += f"\n\n## Previous Review Feedback (you MUST address these issues):\n{latest_feedback}\n"
    
    output = _run_phase("implementing", feature, runner, prompt, status=status,
                        start_message="Starting implementation")
    summary = _parse_json_output(output, "summary")
    impl_notes = _strip_markdown(str(summary)) if summary else _strip_markdown(output.strip())
    feature.set_impl_notes(impl_notes)
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
    feedback: str = "",
    status: Optional[AgentStatus] = None,
) -> None:
    """Run the fix-feedback phase - focuses on fixing issues from review feedback."""
    if not feedback:
        latest = feature.get_latest_impl_review()
        if latest:
            feedback = latest.get("feedback", "")
    runner = runner.for_phase("fix_feedback")
    console.print(f"\n[bold blue]Fixing Review Feedback:[/bold blue] {feature.title}")

    prompt = _build_prompt("fix-feedback.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{impl_spec}": _spec_to_string(feature.impl_spec) or "(no impl spec)",
        "{test_spec}": _spec_to_string(feature.test_spec) or "(no test spec)",
        "{impl_notes}": _spec_to_string(feature.impl_notes) or "(no impl notes)",
        "{feedback}": feedback or "(no feedback)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "fix-feedback",
    }, "fix_feedback")

    output = _run_phase("fix_feedback", feature, runner, prompt, status=status,
                        start_message="Starting fix feedback")

    existing_notes = feature.impl_notes or ""
    summary = _parse_json_output(output, "summary")
    fix_summary = _strip_markdown(str(summary)) if summary else _strip_markdown(output.strip())
    updated = f"{existing_notes}\n\nFix Feedback\n\n{fix_summary}"
    feature.set_impl_notes(updated)
    feature.add_history("FIX_FEEDBACK", f"Fixed issues per review feedback via {runner.agent.name}")
    feature.save()

    feature.move_to_stage("testing")
    feature.add_history("TESTING", "Moved to testing phase after fixing feedback")
    feature.save()
    _git_commit(feature, "fix feedback complete")
    _delete_checkpoint(feature)
    console.print("[green]Feedback fixed. Feature moved to testing.[/green]")


def run_verify_tests(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> tuple[str, str]:
    """Verify tests pass for the implemented feature. Returns (verdict, feedback)."""
    import json
    import re
    
    runner = runner.for_phase("testing")
    console.print(f"\n[bold blue]Verifying Tests:[/bold blue] {feature.title}")

    prompt = _build_prompt("verify-tests.md", {
        "{title}": feature.title,
        "{test_spec}": _spec_to_string(feature.test_spec) or "(no test spec)",
        "{impl_notes}": _spec_to_string(feature.impl_notes) or "(no impl notes)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "verifying-tests",
    }, "testing")

    output = _run_phase("testing", feature, runner, prompt, status=status,
                        start_message="Starting test verification")

    verdict, feedback = _parse_verdict(output)
    
    test_results = {}
    try:
        result = json.loads(output.strip())
    except json.JSONDecodeError:
        try:
            json_match = re.search(r'\{[\s\S]*\}', output)
            if json_match:
                result = json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError):
            result = None
    
    if result and "test_results" in result:
        test_results = result.get("test_results", {})
    
    if test_results:
        feature.set_test_results({
            "ts": datetime.datetime.now().isoformat(),
            "verdict": verdict,
            "results": test_results,
            "feedback": feedback,
        })

    feature.add_history("TESTING", f"Test verification: {verdict} via {runner.agent.name}")
    feature.save()
    _git_commit(feature, "test verification complete")
    _delete_checkpoint(feature)

    if verdict == "PASS":
        feature.move_to_stage("review")
        console.print("[green]Tests verified. Feature moved to review.[/green]")
    else:
        feature.move_to_stage("review")
        console.print(f"[yellow]Test verification FAILED. Feature moved to review for inspection.[/yellow]")

    return verdict, feedback


def run_review_impl(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> tuple[str, str]:
    """Run the review phase. Returns (verdict, feedback)."""
    runner = runner.for_phase("review")

    prompt = _build_prompt("review-impl.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{test_spec}": _spec_to_string(feature.test_spec) or "(no test spec)",
        "{impl_notes}": _spec_to_string(feature.impl_notes) or "(no impl notes)",
    }, "review_impl")

    output = _run_phase("review_impl", feature, runner, prompt, status=status,
                        start_message="Starting review")

    verdict, feedback = _parse_verdict(output)

    feature.add_impl_review(verdict, feedback)
    feature.add_history("REVIEW", f"Verdict: {verdict}")

    if verdict == "PASS":
        feature.save()
        feature.move_to_stage("final-human-approval")
        _git_commit(feature, "review passed")
        _delete_checkpoint(feature)
        console.print(f"[green]Review PASSED. Feature moved to final-human-approval.[/green]")
    else:
        feature.save()
        feature.move_to_stage("implementing")
        _git_commit(feature, "review failed")
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
    Then runs spec_writing -> implementing -> verify_tests -> review_impl
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
        for attempt in range(1, max_plan_review_attempts + 1):
            latest_plan_review = feature.get_latest_plan_review()
            plan_feedback = latest_plan_review.get("feedback", "") if latest_plan_review else ""
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
                feature.add_history("PLAN_REVIEW", "Sending back for re-planning")
                feature.save()
                plan_ok = run_planning(feature, runner, status=status)
                if not plan_ok:
                    # Questions raised during re-planning, cannot continue review loop
                    return
                # Plan produced - run_planning already moved to reviewing-plan
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

    _run_impl_test_review_loop(feature, runner, status)


def _run_impl_test_review_loop(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
    max_review_attempts: int = 5,
) -> None:
    """Shared implementation/test/review loop used by both pipeline entry points."""
    for attempt in range(1, max_review_attempts + 1):
        if attempt == 1:
            run_implementing(feature, runner, status=status)
        else:
            feedback = _get_latest_feedback(feature)
            run_fix_feedback(feature, runner, feedback, status=status)

        # Verify tests pass
        test_verdict, test_feedback = run_verify_tests(feature, runner, status=status)
        if test_verdict != "PASS":
            # Tests failed — loop back to fix
            console.print(
                f"[yellow]Test verification failed (attempt {attempt}/{max_review_attempts}). "
                f"Retrying...[/yellow]"
            )
            continue

        # Code review
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


def run_pipeline_from_implementing(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Resume pipeline from implementing stage.

    Called when a feature is in implementing (e.g., after failed review retry).
    Runs: implementing -> verify_tests -> review_impl
    Retries up to 5 times on FAIL before giving up.
    """
    try:
        console.print(Panel(
            f"[bold]Resuming pipeline from implementing:[/bold] {feature.title}",
            border_style="cyan",
        ))
        _run_impl_test_review_loop(feature, runner, status)
    except Exception as e:
        _delete_checkpoint(feature)
        raise


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
