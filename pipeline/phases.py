"""Automated phase orchestration for the MAD pipeline.

Each phase function takes a FeatureFile and AgentRunner, runs the appropriate
headless agent call, updates the feature file, and moves it to the next stage.
"""

import datetime
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

import json5
import jsonschema
from rich.console import Console
from rich.panel import Panel

from agent_status import AgentStatus
from config import Config, get_mad_dir
from runner import AgentRunner, RateLimitError
from state import FeatureFile

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
            "summary": "string",
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
    "reviewing_spec": {
        "runner_phase": "reviewing_spec",
        "history_tag": "SPECREVIEW",
        "status_label": "reviewing-spec",
        "template": "review-spec.md",
        "output_schema": {
            "verdict": "string",
            "summary": "string",
            "feedback": "string | null",
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
            "summary": "string",
            "feedback": "string | null",
        },
    },
    "ideating": {
        "runner_phase": "ideating",
        "history_tag": "IDEATING",
        "status_label": "ideating",
        "template": "ideating.md",
        "output_schema": {
            "summary": "string",
        },
    },
}


assert all(cfg["history_tag"].replace("_", "").isalpha() for cfg in PHASE_CONFIG.values()), "Invalid history_tag in PHASE_CONFIG"

PHASE_SCHEMAS = {
    "planning": {
        "type": "object",
        "properties": {
            "questions": {"type": ["array", "null"]},
            "plan": {"type": ["string", "null"]},
        },
    },
    "reviewing_plan": {
        "type": "object",
        "required": ["verdict"],
        "properties": {
            "verdict": {"type": "string"},
            "summary": {"type": "string"},
            "feedback": {"type": ["string", "null"]},
        },
    },
    "review_impl": {
        "type": "object",
        "required": ["verdict"],
        "properties": {
            "verdict": {"type": "string"},
            "summary": {"type": "string"},
            "feedback": {"type": ["string", "null"]},
        },
    },
    "spec_impl": {
        "type": "object",
        "required": ["implementation_spec"],
        "properties": {
            "implementation_spec": {"type": "string"},
        },
    },
    "spec_test": {
        "type": "object",
        "required": ["test_spec"],
        "properties": {
            "test_spec": {"type": "string"},
        },
    },
    "reviewing_spec": {
        "type": "object",
        "required": ["verdict"],
        "properties": {
            "verdict": {"type": "string"},
            "summary": {"type": "string"},
            "feedback": {"type": ["string", "null"]},
        },
    },
    "implementing": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "files_changed": {"type": "array"},
        },
    },
    "fix_feedback": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "files_changed": {"type": "array"},
        },
    },
}


def _validate_schema(result: dict, schema: dict, field: str) -> None:
    """Validate parsed JSON against a schema, logging a warning on mismatch.
    
    This performs graceful degradation - it logs a warning but never blocks
    the pipeline over a schema mismatch.
    """
    try:
        jsonschema.validate(instance=result, schema=schema)
    except jsonschema.ValidationError as e:
        logger.warning(f"Schema validation failed for field '{field}': {e.message}")


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
    config = PHASE_CONFIG[phase_key]

    if not skip_history_start:
        msg = start_message or f"Starting {config['history_tag'].lower().replace('_', ' ')}"
        feature.add_history(config["history_tag"], msg)
        feature.save()
    
    if status is not None:
        status.phase = config["status_label"]
        status.agent = runner.agent.name
    
    logger.info(f"[{config['runner_phase']}] Running: {feature.title}")
    
    board_for_context = None
    if config["runner_phase"] in ("planning", "implementing", "spec_writing"):
        board_for_context = feature.board
    
    try:
        output = runner.headless(
            prompt,
            status=status,
            phase_key=phase_key,
            output_schema=config.get("output_schema"),
            board=board_for_context,
        )
    except Exception as e:
        logger.error(f"[{config['runner_phase']}] headless() failed for {feature.title}: {e}")
        feature.add_history(config["history_tag"], f"FAILED: {e}")
        feature.save()
        feature.append_pipeline_log(config['runner_phase'], f"Phase {config['runner_phase']} failed: {e}")
        raise
    
    if not output or not output.strip():
        logger.error(f"[{config['runner_phase']}] Empty output for {feature.title}")
        feature.add_history(config["history_tag"], "FAILED: Empty output from agent")
        feature.save()
        feature.append_pipeline_log(config['runner_phase'], f"Phase {config['runner_phase']} failed: empty output from agent")
        raise RuntimeError(f"Phase {phase_key} produced empty output for {feature.title}")
    
    feature.append_pipeline_log(config['runner_phase'], f"Phase {config['runner_phase']} completed successfully")
    return output


def _extract_json_robust(text: str) -> list[str]:
    """Extract JSON blocks from text using brace-counting algorithm.
    
    This function correctly handles:
    - Arbitrary nesting depth
    - Escaped braces inside strings
    - Multiple JSON objects in mixed content
    
    Args:
        text: The text to search for JSON blocks
        
    Returns:
        List of raw string candidates (JSON blocks) found in the text
    """
    if not text:
        return []
    
    candidates = []
    text_len = len(text)
    i = 0
    
    while i < text_len:
        if text[i] == '{':
            depth = 1
            start = i
            i += 1
            in_string = False
            escape_next = False
            
            while i < text_len and depth > 0:
                char = text[i]
                
                if escape_next:
                    escape_next = False
                    i += 1
                    continue
                
                if char == '\\':
                    escape_next = True
                    i += 1
                    continue
                
                if char == '"':
                    in_string = not in_string
                elif not in_string:
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                
                i += 1
            
            if depth == 0:
                candidates.append(text[start:i])
        else:
            i += 1
    
    return candidates


def _parse_json_output(output: str, field: str, schema: dict = None):
    """Parse JSON output from agent and extract a specific field.

    Args:
        output: The raw output from the agent (JSON string)
        field: The field name to extract (e.g., "implementation_spec", "test_spec")
        schema: Optional JSON Schema for validation

    Returns:
        The extracted field value as a parsed object (dict/list) if valid JSON,
        or as a string if not valid JSON, or empty string if not found
    """
    if not output or not output.strip():
        return ""

    output_stripped = output.strip()

    # Step 1: Try direct json.loads()
    try:
        result = json.loads(output_stripped)
        if isinstance(result, dict) and field in result and result[field]:
            if schema:
                _validate_schema(result, schema, field)
            return result[field]
    except (json.JSONDecodeError, ValueError):
        pass

    # Step 2: Try json5.loads() for relaxed parsing
    try:
        result = json5.loads(output_stripped)
        if isinstance(result, dict) and field in result and result[field]:
            if schema:
                _validate_schema(result, schema, field)
            return result[field]
    except (json.JSONDecodeError, ValueError):
        pass

    # Step 3: Extract and parse candidates using brace-counting
    candidates = _extract_json_robust(output)
    for candidate in reversed(candidates):
        # Try json.loads first
        try:
            result = json.loads(candidate)
            if isinstance(result, dict) and field in result and result[field]:
                if schema:
                    _validate_schema(result, schema, field)
                return result[field]
        except (json.JSONDecodeError, ValueError):
            pass
        
        # Try json5.loads()
        try:
            result = json5.loads(candidate)
            if isinstance(result, dict) and field in result and result[field]:
                if schema:
                    _validate_schema(result, schema, field)
                return result[field]
        except (json.JSONDecodeError, ValueError):
            pass

    # Step 4: Log warning on complete failure
    logger.warning(f"JSON parsing failed for field '{field}'. Output preview: {output_stripped[:200]}")

    return ""


def _parse_verdict(output: str) -> tuple[str, str, str]:
    """Parse verdict, summary, and feedback from review output.
    
    Tries JSON first, falls back to text parsing.
    Returns (verdict, summary, feedback) where verdict is 'PASS' or 'FAIL'.
    """
    verdict = "FAIL"
    summary = ""
    feedback = output.strip()
    
    # Try to parse JSON - direct parse first, then robust extraction fallback
    result = None
    json_parsed = False
    try:
        result = json.loads(output.strip())
    except json.JSONDecodeError:
        try:
            result = json5.loads(output.strip())
        except (json.JSONDecodeError, ValueError):
            candidates = _extract_json_robust(output)
            for candidate in reversed(candidates):
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        result = parsed
                        break
                except json.JSONDecodeError:
                    pass
                try:
                    parsed = json5.loads(candidate)
                    if isinstance(parsed, dict):
                        result = parsed
                        break
                except (json.JSONDecodeError, ValueError):
                    pass

    if result is not None:
        # Reject plan-shaped output: only if it has "plan" but NOT "verdict" AND NOT other review fields
        if "plan" in result and "verdict" not in result and "summary" not in result:
            logger.warning("Review agent returned a plan instead of a verdict — ignoring JSON")
            result = None
            feedback = "SYSTEM ERROR: Review agent returned a plan instead of a review verdict. This indicates an agent configuration or prompting issue, not a problem with the plan itself. The plan should be reviewed again without re-planning."

    if result is not None:
        if "verdict" in result:
            json_parsed = True
            if "PASS" in result["verdict"].upper():
                verdict = "PASS"
            summary_raw = result.get("summary")
            if summary_raw is None or summary_raw == 0 or summary_raw is False:
                summary = f"Review {verdict} - no summary provided"
                logger.warning(f"Review verdict parsed but summary was missing or empty")
            else:
                summary = str(summary_raw).strip()
                if not summary:
                    summary = f"Review {verdict} - no summary provided"
                    logger.warning(f"Review verdict parsed but summary was missing or empty")
            if len(summary) > 500:
                summary = summary[:497] + "..."
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
                summary = "Tests passed - all checks clean"
                feedback = ""
            else:
                summary = f"Tests failed - {failed_count} failure(s)"
                feedback = json.dumps(result, indent=2)

    if not json_parsed:
        verdict_match = re.search(r"\*?\*?VERDICT\*?\*?:\s*(\w+)", output, re.IGNORECASE)
        if verdict_match:
            if "PASS" in verdict_match.group(1).upper():
                verdict = "PASS"
        summary_match = re.search(r"\*?\*?SUMMARY\*?\*?:\s*(.+?)(?=\n\*?\*?FEEDBACK|$)", output, re.DOTALL | re.IGNORECASE)
        if summary_match:
            summary = summary_match.group(1).strip()
        feedback_match = re.search(r"\*?\*?FEEDBACK\*?\*?:\s*(.+)", output, re.DOTALL)
        if feedback_match:
            feedback = feedback_match.group(1).strip()
        elif verdict == "FAIL":
            verdict_pos = output.upper().find("VERDICT:")
            if verdict_pos >= 0:
                after_verdict = output[verdict_pos + 8:].strip()
                if after_verdict and after_verdict.upper() not in ("PASS", "FAIL"):
                    feedback = after_verdict
        if not summary:
            if feedback and not re.match(r'^VERDICT:\s*(PASS|FAIL)\s*$', feedback.strip(), re.IGNORECASE):
                first_sentence = feedback.split('. ')[0]
                if len(first_sentence) > 500:
                    first_sentence = first_sentence[:497] + "..."
                summary = first_sentence
            else:
                summary = f"Review {verdict} - no summary provided"
                logger.warning(f"Review verdict parsed but summary was missing or empty")
    
    if len(summary) > 500:
        summary = summary[:497] + "..."
    
    summary = _strip_markdown(summary)
    feedback = _strip_markdown(feedback)
    return verdict, summary, feedback


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


def _build_failure_summary(reviews: list, loop_name: str) -> str:
    """Build a structured failure summary from a list of review dicts.
    
    Args:
        reviews: List of review dicts, each with 'ts', 'verdict', 'summary', 'feedback'
        loop_name: One of "plan", "spec", "impl" (for context, currently unused)
    
    Returns:
        A human-readable text block with failure history, or empty string if no failures.
    """
    import difflib
    
    fail_reviews = [r for r in reviews if r.get("verdict") == "FAIL"]
    
    if not fail_reviews:
        return ""
    
    lines = []
    n = len(fail_reviews)
    lines.append(f"## Failure History ({n} prior attempt{'s' if n > 1 else ''})")
    lines.append("")
    
    recurring_issue = False
    recurring_phrase = ""
    max_consecutive = 0
    consecutive_start = 0
    
    contradictory_feedback = False
    contradiction_pair = ()
    
    for i, review in enumerate(fail_reviews):
        feedback = review.get("feedback") or ""
        first_sentence = feedback.split('.')[0] if feedback else ""
        key_complaint = first_sentence[:200].strip()
        
        lines.append(f"Attempt {i + 1}: {key_complaint}")
        
        if i > 0:
            prev_feedback = fail_reviews[i - 1].get("feedback") or ""
            prev_first = prev_feedback.split('.')[0].lower() if prev_feedback else ""
            curr_lower = feedback.lower() if feedback else ""
            
            matcher = difflib.SequenceMatcher(None, prev_first, curr_lower)
            match = matcher.find_longest_match(0, len(prev_first), 0, len(curr_lower))
            if match.size >= 20:
                phrase = prev_first[match.a:match.a + match.size]
                if phrase.strip():
                    max_consecutive += 1
                    if max_consecutive == 0:
                        consecutive_start = i - 1
                    max_consecutive += 1
                    if max_consecutive >= 3:
                        recurring_issue = True
                        recurring_phrase = phrase.strip()
            
            too_x_pattern = re.compile(r'\btoo\s+(\w+)\b')
            not_x_pattern = re.compile(r'\bnot\s+(\w+)\s+enough\b')
            add_pattern = re.compile(r'\badd\s+(\w+)\b')
            remove_pattern = re.compile(r'\bremove\s+(\w+)\b')
            
            too_matches = too_x_pattern.findall(prev_feedback)
            not_matches = not_x_pattern.findall(prev_feedback)
            add_matches = add_pattern.findall(prev_feedback)
            remove_matches = remove_pattern.findall(prev_feedback)
            
            for word in too_matches:
                if f"not {word} enough" in curr_lower:
                    contradictory_feedback = True
                    contradiction_pair = (i, i + 1)
                    break
            
            for word in add_matches:
                if f"remove {word}" in curr_lower:
                    contradictory_feedback = True
                    contradiction_pair = (i, i + 1)
                    break
    
    lines.append("")
    
    if recurring_issue and recurring_phrase:
        count = len([r for r in fail_reviews if (r.get("feedback") or "").lower().find(recurring_phrase.lower()) >= 0])
        lines.append(f"⚠ RECURRING ISSUE: The same core problem has appeared in {count} consecutive attempts: \"{recurring_phrase}\"")
        lines.append("")
    
    if contradictory_feedback and contradiction_pair:
        lines.append(f"⚠ CONTRADICTORY FEEDBACK: Reviews have given conflicting guidance between attempts {contradiction_pair[0]} and {contradiction_pair[1]}.")
        lines.append("")
    
    latest_feedback = fail_reviews[-1].get("feedback") or ""
    lines.append("Full feedback from most recent attempt:")
    lines.append(latest_feedback)
    
    return "\n".join(lines)


def _detect_stuck(reviews: list, threshold: int = 3) -> bool:
    """Detect if the pipeline appears stuck on the same issue.
    
    Args:
        reviews: List of review dicts with 'verdict' and 'feedback' keys
        threshold: Number of consecutive FAIL reviews to consider stuck (default 3)
    
    Returns:
        True if stuck, False otherwise.
    """
    import difflib
    
    if len(reviews) < threshold:
        return False
    
    last_n = reviews[-threshold:]
    
    for review in last_n:
        if review.get("verdict") != "FAIL":
            return False
    
    complaints = []
    for review in last_n:
        feedback = review.get("feedback") or ""
        first_sentence = feedback.split('.')[0].lower() if feedback else ""
        cleaned = re.sub(r'[^\w\s]', '', first_sentence)
        complaints.append(cleaned.strip())
    
    for i in range(len(complaints) - 1):
        matcher = difflib.SequenceMatcher(None, complaints[i], complaints[i + 1])
        match = matcher.find_longest_match(0, len(complaints[i]), 0, len(complaints[i + 1]))
        if match.size < 20:
            return False
    
    return True


def _get_latest_feedback(feature: FeatureFile) -> str:
    """Extract the most recent review feedback from dedicated review arrays.
    
    Now delegates to _build_failure_summary for backward compatibility.
    """
    impl_summary = _build_failure_summary(feature.impl_reviews, "impl")
    if impl_summary:
        return impl_summary
    plan_summary = _build_failure_summary(feature.plan_reviews, "plan")
    if plan_summary:
        return plan_summary
    return "No previous feedback available."


def _validate_exploration_artifacts(agent_output: str) -> tuple[bool, str]:
    """Return (True, '') if output shows exploration evidence, else (False, reason).
    
    Handles empty strings and outputs without questions gracefully.
    """
    if not agent_output or not agent_output.strip():
        return True, ''
    
    import json
    import json5
    result = None
    try:
        result = json.loads(agent_output.strip())
    except (json.JSONDecodeError, ValueError):
        try:
            result = json5.loads(agent_output.strip())
        except (json.JSONDecodeError, ValueError):
            pass
    
    if result is None:
        import re
        json_blocks = re.findall(r'```(?:json)?\s*([\s\S]*?)\s*```', agent_output)
        for block in json_blocks:
            try:
                result = json.loads(block.strip())
                break
            except json.JSONDecodeError:
                try:
                    result = json5.loads(block.strip())
                    break
                except (json.JSONDecodeError, ValueError):
                    pass
    
    if result is None:
        return True, ''
    
    if not isinstance(result, dict):
        return True, ''
    
    questions = result.get("questions")
    if not questions or not isinstance(questions, list) or len(questions) == 0:
        return True, ''
    
    # Check for exploration_summary field (new approach)
    exploration_summary = result.get("exploration_summary")
    if exploration_summary and isinstance(exploration_summary, str) and len(exploration_summary.strip()) > 0:
        return True, ''
    
    # Fall back to old phrase-based check (for backwards compatibility)
    exploration_indicators = [
        'i searched for', 'i found in the code', 'reading the file', 'looking at the',
        'exploring the codebase', 'using grep', 'using read', 'using glob',
        'grep results', 'file contents', 'i read', 'i looked at', 'the code shows',
        'in the source', 'searching for', 'found in', 'read the'
    ]
    output_lower = agent_output.lower()
    if any(ind in output_lower for ind in exploration_indicators):
        return True, ''
    
    # Instead of failing, just warn - log a warning but don't block
    logger.warning("Agent asked questions without exploration_summary or evidence of exploration. Allowing to continue.")
    return True, ''


def _check_question_antipatterns(questions: list) -> tuple[bool, str]:
    """Return (True, '') if questions look legitimate, else (False, reason)."""
    antipatterns = [
        (r'does this happen.*all.*items?', 'Item scope can be determined by reading code paths'),
        (r'what.*steps.*to.*reproduce', 'Reproduction steps can be inferred from error location and code flow'),
        (r'what.*operating system', 'OS-specific issues should be evident from error messages or code'),
        (r'does.*work.*different.*phase', 'Phase-specific behaviour can be found by reading the code'),
        (r'can you.*describe.*steps', 'Reproduction steps can be inferred from code'),
    ]
    for q in questions:
        text = q.get('question', '').lower()
        for pattern, reason in antipatterns:
            if re.search(pattern, text):
                return False, f"Question '{q.get('question', '')}' is a generic anti-pattern: {reason}"
    return True, ''


def run_ideating(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Run the ideation debate process for the feature.
    
    Executes multiple rounds in cycles (one pass through rounds list = one cycle),
    then runs synthesis with verdict evaluation to decide whether to continue.
    """
    config = Config()
    rounds = config.ideating_rounds
    
    if not rounds:
        logger.warning("[ideating] No ideating rounds configured, skipping")
        return
    
    max_rounds = feature.ideation_max_rounds or config.ideating_max_rounds
    rounds_per_cycle = len(rounds)
    
    logger.info(f"[ideating] Starting ideation for: {feature.title}")
    feature.add_history("IDEATING", "Starting ideation debate")
    feature.clear_ideation_cycle_verdicts()
    feature.save()
    
    conversations_dir = config.boards_dir / feature.board / ".conversations"
    conversations_dir.mkdir(parents=True, exist_ok=True)
    
    existing_convos = list(conversations_dir.glob(f"{feature.slug}.ideating.*"))
    next_num = 1
    if existing_convos:
        nums = []
        for f in existing_convos:
            try:
                nums.append(int(f.name.split(".")[-1]))
            except ValueError:
                pass
        if nums:
            next_num = max(nums) + 1
    
    total_rounds_completed = 0
    cycle_number = 0
    summaries = []
    full_outputs = []
    termination_reason = None
    final_ideation = ""
    
    while total_rounds_completed < max_rounds:
        cycle_number += 1
        cycle_start_round = total_rounds_completed
        
        for round_config in rounds:
            if total_rounds_completed >= max_rounds:
                break
            
            current_round = total_rounds_completed + 1
            round_cli = round_config.cli
            round_model = round_config.model
            
            round_runner = runner.for_cli_model(round_cli, round_model)
            
            is_proposer = (current_round == 1)
            if is_proposer:
                role_instruction = "Generate initial ideas and approach for this feature. Be thorough and explore different angles."
                previous_discussion = ""
            else:
                if current_round % 2 == 1:
                    role_instruction = "Refine and improve upon the previous critique. Address the points raised and provide a better approach."
                else:
                    role_instruction = "Critique the previous proposal. Find weaknesses, gaps, and areas that need more thought."
                
                previous_discussion = "\n\n".join([
                    f"Round {i+1}: {s}" for i, s in enumerate(summaries)
                ])
            
            summaries_text = f"Previous contributions:\n" + "\n".join([f"- {s}" for s in summaries]) if summaries else "This is the first round - no previous discussion."
            
            transcript_paths_text = "\n".join([
                f"- Full transcript: {conversations_dir / f'{feature.slug}.ideating.{i+1}'}"
                for i in range(len(full_outputs))
            ]) if full_outputs else "No full transcripts yet."
            
            # Cap existing ideation to avoid blowing context window after many cycles
            existing_ideation = feature.Ideation or ""
            if len(existing_ideation) > 3000:
                existing_ideation = existing_ideation[:3000] + "\n\n[... truncated for context limit ...]"
            previous_ideation = f"\n\n## Existing Ideation\n{existing_ideation}" if existing_ideation else ""
            
            ideation_prompt_section = ""
            if feature.ideation_prompt:
                ideation_prompt_section = (
                    "\n\n## User Direction\n"
                    f"{feature.ideation_prompt}\n\n"
                    "The above user direction should guide your ideation — "
                    "prioritize it over default assumptions, but still fulfill your assigned role below."
                )
            
            prompt = _build_prompt("ideating.md", {
                "{title}": feature.title,
                "{description}": feature.description,
                "{previous_ideation}": previous_ideation,
                "{ideation_prompt_section}": ideation_prompt_section,
                "{role_instruction}": role_instruction,
                "{discussion_summary}": summaries_text,
                "{full_transcripts}": transcript_paths_text,
                "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
            })
            
            feature.add_history("IDEATING", f"Starting round {current_round} ({round_cli}/{round_model or 'default'})")
            feature.save()
            
            if status is not None:
                status.phase = f"ideating round {current_round} (cycle {cycle_number})"
                status.agent = round_cli
            
            output = runner.headless(
                prompt,
                status=status,
                phase_key="ideating",
                output_schema={"summary": "string"},
            )
            
            if not output or not output.strip():
                logger.error(f"[ideating] Empty output for round {current_round}")
                feature.add_history("IDEATING", f"Round {current_round} failed: empty output")
                feature.save()
                feature.append_pipeline_log('ideating', f"Ideation round {current_round} failed: empty output")
                raise RuntimeError(f"Ideation round {current_round} produced empty output")
            
            full_output_path = conversations_dir / f"{feature.slug}.ideating.{next_num}"
            full_output_path.write_text(output)
            full_outputs.append(output)
            next_num += 1
            
            try:
                result = json.loads(output)
                summary = result.get("summary", "")
            except json.JSONDecodeError:
                summary = output
            
            feature.add_ideation_summary(summary)
            summaries.append(summary)
            
            total_rounds_completed += 1
            feature.append_pipeline_log('ideating', f"Ideation round {current_round} completed (cycle {cycle_number})")
        
        previous_verdicts_text = ""
        if feature.ideation_cycle_verdicts:
            lines = []
            for v in feature.ideation_cycle_verdicts:
                lines.append(f"- Cycle {v['cycle']}: {v['verdict']} - {v['reason']}")
            previous_verdicts_text = "Previous cycle verdicts:\n" + "\n".join(lines)
        
        # Cap summaries to avoid exceeding context window.
        # Include full summaries for the current cycle, condensed for earlier ones.
        if len(summaries) > rounds_per_cycle * 2:
            earlier = summaries[:-(rounds_per_cycle)]
            recent = summaries[-(rounds_per_cycle):]
            earlier_condensed = "\n".join([f"- Round {i+1}: {s[:200]}..." if len(s) > 200 else f"- Round {i+1}: {s}" for i, s in enumerate(earlier)])
            summaries_text_for_synthesis = (
                f"## Earlier Rounds (condensed)\n{earlier_condensed}\n\n"
                + "\n\n".join([f"## Round {len(earlier)+i+1}\n{s}" for i, s in enumerate(recent)])
            )
        else:
            summaries_text_for_synthesis = "\n\n".join([f"## Round {i+1}\n{s}" for i, s in enumerate(summaries)])

        compact_prompt = _build_prompt("compact.md", {
            "{summaries}": summaries_text_for_synthesis,
            "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
            "{cycle_number}": str(cycle_number),
            "{total_rounds_completed}": str(total_rounds_completed),
            "{previous_verdicts_section}": previous_verdicts_text,
        })
        
        feature.add_history("IDEATING", f"Running synthesis (cycle {cycle_number})")
        feature.save()
        
        if status is not None:
            status.phase = f"ideating synthesis (cycle {cycle_number})"
        
        synthesis_runner = runner.for_cli_model(rounds[-1].cli, rounds[-1].model)
        synthesis_output = synthesis_runner.headless(
            compact_prompt,
            status=status,
            phase_key="ideating",
            output_schema={"Ideation": "string", "verdict": "string", "verdict_reason": "string"},
        )
        
        verdict = "progress"
        verdict_reason = "Could not parse verdict"
        
        if synthesis_output and synthesis_output.strip():
            try:
                result = json.loads(synthesis_output)
                final_ideation = result.get("Ideation", "")
                verdict_raw = result.get("verdict", "progress")
                verdict = "progress"
                if isinstance(verdict_raw, str):
                    verdict = verdict_raw.lower().strip()
                verdict_reason_raw = result.get("verdict_reason")
                verdict_reason = "No reason given"
                if isinstance(verdict_reason_raw, str):
                    verdict_reason = verdict_reason_raw
            except json.JSONDecodeError:
                final_ideation = synthesis_output
                verdict = "progress"
                verdict_reason = "Synthesis output was not valid JSON"
        
        if verdict not in ("consensus", "stall", "progress"):
            verdict = "progress"
        
        feature.add_ideation_cycle_verdict(cycle_number, verdict, verdict_reason, total_rounds_completed)
        feature.append_pipeline_log('ideating', f"Ideation synthesis cycle {cycle_number}: {verdict} - {verdict_reason}")
        
        if termination_reason == "hard_cap":
            break
        
        if total_rounds_completed >= max_rounds:
            termination_reason = "hard_cap"
            feature.add_history("IDEATING", f"Hard cap reached ({max_rounds} rounds)")
            break
        
        if verdict == "consensus":
            termination_reason = "consensus"
            feature.add_history("IDEATING", f"Consensus reached after {total_rounds_completed} rounds")
            break
        
        if verdict == "stall":
            termination_reason = "stall"
            feature.add_history("IDEATING", f"Stall detected after {total_rounds_completed} rounds")
            break
        
        feature.add_history("IDEATING", f"Cycle {cycle_number}: progress, continuing")
    
    if termination_reason and final_ideation:
        termination_note = {
            "consensus": f"\n\n[Ideation concluded by consensus after {total_rounds_completed} rounds, {cycle_number} cycles.]",
            "stall": f"\n\n[Ideation concluded by stall detection after {total_rounds_completed} rounds, {cycle_number} cycles. Full convergence was not achieved.]",
            "hard_cap": f"\n\n[Ideation concluded by hard cap ({max_rounds} rounds, {cycle_number} cycles).]",
        }.get(termination_reason, "")
        final_ideation += termination_note
    
    if final_ideation:
        feature.set_Ideation(final_ideation)
    
    feature.add_history("IDEATING", "Ideation complete")
    feature.save()
    feature.move_to_stage("ideas")
    logger.info(f"[ideating] Completed: {feature.title} ({termination_reason}, {total_rounds_completed} rounds, {cycle_number} cycles)")


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

    previous_plan_context = ""
    if feature.plan and feature.plan.strip():
        previous_plan_context = f"\n\n## Previous Plan (refine rather than rewrite):\n{feature.plan}\n"

    template = "plan-bug.md" if feature.item_type == "bug" else "plan-headless.md"
    prompt = _build_prompt(template, {
        "{title}": feature.title,
        "{description}": feature.get_section("Description") or "(no description)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "planning",
        "{previous_plan}": previous_plan_context,
        "{ideation_synthesis}": feature.Ideation or "(no ideation)",
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
    }, "planning")
    
    if questions_context:
        prompt += questions_context
    
    if feedback_context:
        prompt += feedback_context

    dependency_context = ""
    if feature.depends_on:
        from state import resolve_dependencies, FeatureFile as SF
        all_features = SF.list_all(board=feature.board)
        dep_status = resolve_dependencies(feature.id, all_features)
        feature_map = {f.id: f for f in all_features}
        dep_lines = []
        for dep_id in feature.depends_on:
            dep_f = feature_map.get(dep_id)
            if dep_id in dep_status['dangling_deps']:
                dep_lines.append(f"- {dep_id}: MISSING (broken reference)")
            elif dep_f:
                stage = dep_f.current_stage
                if stage == 'rejected':
                    dep_lines.append(f"- {dep_f.title} ({dep_id}): REJECTED — plan must adapt to work without this dependency")
                elif stage == 'done':
                    dep_lines.append(f"- {dep_f.title} ({dep_id}): COMPLETED")
                else:
                    dep_lines.append(f"- {dep_f.title} ({dep_id}): IN PROGRESS ({stage})")
        if dep_lines:
            dependency_context = "\n\n## Dependencies\nThis feature depends on the following features. Adapt your plan based on their status:\n" + "\n".join(dep_lines) + "\n"

    if dependency_context:
        prompt += dependency_context

    output = _run_phase("planning", feature, runner, prompt, status=status,
                        start_message="Starting planning", skip_history_start=True)

    # Try to parse JSON output for questions and plan
    # Try direct JSON parse first, then robust extraction fallback
    result = None
    try:
        result = json.loads(output.strip())
    except json.JSONDecodeError:
        try:
            result = json5.loads(output.strip())
        except (json.JSONDecodeError, ValueError):
            candidates = _extract_json_robust(output)
            for candidate in reversed(candidates):
                try:
                    result = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    pass
                try:
                    result = json5.loads(candidate)
                    break
                except (json.JSONDecodeError, ValueError):
                    pass

    if result:
        # Validate exploration before accepting questions
        if "questions" in result and isinstance(result["questions"], list) and result["questions"]:
            is_valid, err = _validate_exploration_artifacts(output)
            if not is_valid:
                feature.add_history("PLANNING", f"FAILED: {err}")
                feature.save()
                logger.warning(f"[planning] Exploration validation failed for {feature.title}: {err}")
                raise RuntimeError(f"Planning validation failed: {err}")

            is_valid, err = _check_question_antipatterns(result["questions"])
            if not is_valid:
                feature.add_history("PLANNING", f"FAILED: {err}")
                feature.save()
                logger.warning(f"[planning] Anti-pattern question detected for {feature.title}: {err}")
                raise RuntimeError(f"Planning validation failed: {err}")

        # Handle questions - must be a list
        if "questions" in result and isinstance(result["questions"], list) and result["questions"]:
            questions = result["questions"]
            # Get existing answered questions to preserve
            existing_answers = {q.get("question"): q.get("answer") for q in feature.questions if q.get("answer")}
            # Merge new questions with existing answers
            merged_questions = []
            for q in questions:
                q_text = q.get("question", "")
                prev_answer = existing_answers.get(q_text, "")
                merged_questions.append({
                    "question": q_text,
                    "answer": prev_answer
                })
            feature._data["questions"] = merged_questions
            feature._save()
            
            # Check if all questions have answers - if so, skip to reviewing-plan
            unanswered = [q for q in merged_questions if not q.get("answer")]
            if not unanswered:
                # All questions answered, continue with plan if available
                if "plan" in result and isinstance(result.get("plan"), str) and result["plan"]:
                    feature.set_plan(result["plan"])
                    if "exploration_summary" in result and isinstance(result.get("exploration_summary"), str):
                        feature.set_plan_exploration_summary(result["exploration_summary"])
                    feature.add_history("PLANNING", f"Plan generated via {runner.agent.name} (questions answered)")
                    feature.save()
                    feature.move_to_stage("reviewing-plan")
                    _git_commit(feature, "planning complete")
                    _delete_checkpoint(feature)
                    console.print("[green]All questions answered. Plan generated. Moving to reviewing-plan.[/green]")
                    return True
                else:
                    # No plan yet but questions answered - this shouldn't happen normally
                    feature.add_history("PLANNING", f"All questions answered via {runner.agent.name}")
                    feature.save()
                    console.print("[yellow]All questions answered but no plan yet. Will need another planning run.[/yellow]")
                    return False
            else:
                # Has unanswered questions - move to requested-input
                feature.add_history("PLANNING", f"Questions raised via {runner.agent.name}")
                if "exploration_summary" in result and isinstance(result.get("exploration_summary"), str):
                    feature.set_plan_exploration_summary(result["exploration_summary"])
                feature.save()
                feature.move_to_stage("requested-input")
                console.print(f"[yellow]Plan has {len(unanswered)} unanswered questions. Moving to requested-input.[/yellow]")
                return False

        # Handle plan - must be a string
        if "plan" in result and isinstance(result.get("plan"), str) and result["plan"]:
            feature.set_plan(result["plan"])
            if "exploration_summary" in result and isinstance(result.get("exploration_summary"), str):
                feature.set_plan_exploration_summary(result["exploration_summary"])
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


def _is_system_error_in_latest_review(feature: FeatureFile) -> bool:
    """Check if the latest plan review failed due to a system error."""
    latest_review = feature.get_latest_plan_review()
    if not latest_review:
        return False
    
    feedback = latest_review.get('feedback', '').lower()
    
    system_error_patterns = [
        'system error',
        'returned a plan instead of a review verdict',
        'api error',
        'authentication error', 
        'invalid api key',
        'rate limit',
        'json parse error',
        'malformed json',
        'no valid json',
        'empty output',
        'network error',
        'timeout'
    ]
    
    return any(pattern in feedback for pattern in system_error_patterns)


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
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
    }, "reviewing_plan")

    try:
        output = _run_phase("reviewing_plan", feature, runner, prompt, status=status,
                            start_message="Starting plan review")
    except (RateLimitError, RuntimeError) as e:
        error_msg = str(e)
        if 'authentication error' in error_msg.lower() or 'api key' in error_msg.lower():
            system_feedback = f"SYSTEM ERROR: Authentication failure - {error_msg}. This is not a plan issue. Review should be retried after fixing API configuration."
        elif isinstance(e, RateLimitError):
            system_feedback = f"SYSTEM ERROR: Rate limit exceeded - {error_msg}. This is not a plan issue. Review should be retried after rate limit clears."
        else:
            system_feedback = f"SYSTEM ERROR: Agent execution failed - {error_msg}. This is not a plan issue. Review should be retried."
        
        feature.add_plan_review("FAIL", "System error during review", system_feedback)
        feature.add_history("PLAN_REVIEW_FAILED", f"System error: {error_msg}")
        feature.save()
        return "FAIL", system_feedback

    verdict, summary, review_feedback = _parse_verdict(output)

    feature.add_plan_review(verdict, summary, review_feedback)
    feature.add_history("PLAN_REVIEW", f"Verdict: {verdict} - {summary}")
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
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
    }, "spec_impl")

    latest_spec_review = feature.get_latest_spec_review()
    if latest_spec_review and latest_spec_review.get("verdict") == "FAIL" and latest_spec_review.get("feedback"):
        impl_prompt += f"\n\n## Previous Spec Review Feedback (you MUST address these issues):\n{latest_spec_review['feedback']}\n"

    output = _run_phase("spec_impl", feature, runner, impl_prompt, status=status)
    
    # Parse JSON output
    impl_spec = _parse_json_output(output, "implementation_spec", PHASE_SCHEMAS.get("spec_impl"))
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
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
    }, "spec_test")

    latest_spec_review = feature.get_latest_spec_review()
    if latest_spec_review and latest_spec_review.get("verdict") == "FAIL" and latest_spec_review.get("feedback"):
        test_prompt += f"\n\n## Previous Spec Review Feedback (you MUST address these issues):\n{latest_spec_review['feedback']}\n"

    output = _run_phase("spec_test", feature, runner, test_prompt, status=status, skip_history_start=True)
    
    # Parse JSON output
    test_spec = _parse_json_output(output, "test_spec", PHASE_SCHEMAS.get("spec_test"))
    if not test_spec:
        test_spec = output.strip()
    feature.set_test_spec(test_spec)
    feature.add_history("SPEC_WRITING", f"Test spec generated via {runner.agent.name}")
    feature.save()

    _git_commit(feature, "spec writing complete")
    _delete_checkpoint(feature)
    console.print("[green]Specs generated.[/green]")


def run_spec_review(
    feature: FeatureFile,
    runner: AgentRunner,
    feedback: str = "",
    status: Optional[AgentStatus] = None,
) -> tuple[str, str]:
    """Review the specs and return (verdict, feedback).

    If feedback is provided from a previous failed review, use it to improve the review.
    """
    runner = runner.for_phase("reviewing_spec")

    feedback_section = f"## Previous Spec Review Feedback (must address):\n{feedback}" if feedback else ""

    prompt = _build_prompt("review-spec.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{impl_spec}": _spec_to_string(feature.impl_spec) or "(no impl spec)",
        "{test_spec}": _spec_to_string(feature.test_spec) or "(no test spec)",
        "{feedback_section}": feedback_section,
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
    }, "reviewing_spec")

    try:
        output = _run_phase("reviewing_spec", feature, runner, prompt, status=status,
                            start_message="Starting spec review")
    except (RateLimitError, RuntimeError) as e:
        error_msg = str(e)
        if 'authentication error' in error_msg.lower() or 'api key' in error_msg.lower():
            system_feedback = f"SYSTEM ERROR: Authentication failure - {error_msg}. This is not a spec issue. Review should be retried after fixing API configuration."
        elif isinstance(e, RateLimitError):
            system_feedback = f"SYSTEM ERROR: Rate limit exceeded - {error_msg}. This is not a spec issue. Review should be retried after rate limit clears."
        else:
            system_feedback = f"SYSTEM ERROR: Agent execution failed - {error_msg}. This is not a spec issue. Review should be retried."

        feature.add_spec_review("FAIL", "System error during review", system_feedback)
        feature.add_history("SPECREVIEW", f"System error: {error_msg}")
        feature.save()
        return "FAIL", system_feedback

    verdict, summary, review_feedback = _parse_verdict(output)

    feature.add_spec_review(verdict, summary, review_feedback)
    feature.add_history("SPECREVIEW", f"Verdict: {verdict} - {summary}")
    feature.save()
    _git_commit(feature, "spec review complete")
    _delete_checkpoint(feature)

    return verdict, review_feedback


def run_implementing(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> None:
    """Run the implementation phase headlessly."""
    runner = runner.for_phase("implementing")
    console.print(f"\n[bold blue]Implementing:[/bold blue] {feature.title}")
    logger.info(f"[implementing] Starting: {feature.title}")

    latest_feedback = _get_latest_feedback(feature)
    feedback_section = f"{latest_feedback}\n" if latest_feedback and latest_feedback != "No previous feedback available." else "(No previous feedback)"

    prompt = _build_prompt("implement.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{impl_spec}": _spec_to_string(feature.impl_spec) or "(no impl spec)",
        "{test_spec}": _spec_to_string(feature.test_spec) or "(no test spec)",
        "{feature_slug}": feature.slug,
        "{feature_id}": feature.id,
        "{phase}": "implementing",
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
        "{feedback_section}": feedback_section,
    }, "implementing")
    
    output = _run_phase("implementing", feature, runner, prompt, status=status,
                        start_message="Starting implementation")
    summary = _parse_json_output(output, "summary", PHASE_SCHEMAS.get("implementing"))
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
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
    }, "fix_feedback")

    output = _run_phase("fix_feedback", feature, runner, prompt, status=status,
                        start_message="Starting fix feedback")

    existing_notes = feature.impl_notes or ""
    summary = _parse_json_output(output, "summary", PHASE_SCHEMAS.get("fix_feedback"))
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
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
    }, "testing")

    output = _run_phase("testing", feature, runner, prompt, status=status,
                        start_message="Starting test verification")

    verdict, summary, feedback = _parse_verdict(output)
    
    test_results = {}
    try:
        result = json.loads(output.strip())
    except json.JSONDecodeError:
        try:
            result = json5.loads(output.strip())
        except (json.JSONDecodeError, ValueError):
            candidates = _extract_json_robust(output)
            for candidate in reversed(candidates):
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        result = parsed
                        break
                except json.JSONDecodeError:
                    pass
                try:
                    parsed = json5.loads(candidate)
                    if isinstance(parsed, dict):
                        result = parsed
                        break
                except (json.JSONDecodeError, ValueError):
                    pass
            else:
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
        feature.move_to_stage("implementing")
        console.print(f"[yellow]Test verification FAILED. Feature moved back to implementing.[/yellow]")

    return verdict, feedback


def run_review_impl(
    feature: FeatureFile,
    runner: AgentRunner,
    status: Optional[AgentStatus] = None,
) -> tuple[str, str]:
    """Run the review phase. Returns (verdict, feedback)."""
    runner = runner.for_phase("review")

    latest_feedback = _get_latest_feedback(feature)
    if latest_feedback and latest_feedback != "No previous feedback available.":
        feedback_section = f"\n\n## Previous Review Feedback (you MUST address these issues):\n{latest_feedback}\n"
    else:
        feedback_section = ""

    prompt = _build_prompt("review-impl.md", {
        "{title}": feature.title,
        "{plan}": feature.plan or "(no plan)",
        "{test_spec}": _spec_to_string(feature.test_spec) or "(no test spec)",
        "{impl_notes}": _spec_to_string(feature.impl_notes) or "(no impl notes)",
        "{feature_file_path}": str(Config().mad_dir / "boards" / feature.board / feature.current_stage / f"{feature.slug}.json"),
        "{feedback_section}": feedback_section,
    }, "review_impl")

    output = _run_phase("review_impl", feature, runner, prompt, status=status,
                        start_message="Starting review")

    verdict, summary, feedback = _parse_verdict(output)

    feature.add_impl_review(verdict, summary, feedback)
    feature.add_history("REVIEW", f"Verdict: {verdict} - {summary}")

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
        max_total_plan_reviews = 6
        if len(feature.plan_reviews) >= max_total_plan_reviews:
            console.print(
                f"[red]Plan has already been reviewed {len(feature.plan_reviews)} times total. "
                f"Moving to final-human-approval for manual intervention.[/red]"
            )
            feature.add_history("FINAL_HUMAN_APPROVAL", f"Plan review exhausted {len(feature.plan_reviews)} total reviews")
            feature.move_to_stage("final-human-approval")
            feature.save()
            return

        max_plan_review_attempts = 3
        system_error_count = 0
        
        for attempt in range(1, max_plan_review_attempts + 1):
            latest_plan_review = feature.get_latest_plan_review()
            plan_feedback = latest_plan_review.get("feedback", "") if latest_plan_review else ""
            
            verdict, plan_feedback = run_plan_review(
                feature, runner, feedback=plan_feedback, status=status
            )
            
            if verdict == "PASS":
                config = Config()
                terminal_stage = config.get_terminal_stage(feature.board)
                if terminal_stage == "plan":
                    feature.move_to_stage("final-human-approval")
                    feature.add_history("TERMINAL_STAGE", "Terminal stage reached (plan) — moved to final-human-approval")
                    feature.save()
                    _git_commit(feature, "terminal stage reached (plan)")
                    console.print("[green]Plan approved. Terminal stage 'plan' reached — moved to final-human-approval.[/green]")
                    return
                elif feature.requires_human_approval:
                    feature.move_to_stage("awaiting-human-approval")
                    feature.add_history("AWAITING_HUMAN_APPROVAL", "Plan approved by AI, awaiting human approval")
                else:
                    feature.move_to_stage("approved")
                    feature.add_history("PROMOTED", "Plan approved, moving to spec-writing")
                feature.save()
                _git_commit(feature, "plan approved")
                break
            
            # Check if this was a system error
            if _is_system_error_in_latest_review(feature):
                system_error_count += 1
                if attempt < max_plan_review_attempts:
                    console.print(
                        f"[yellow]Plan review attempt {attempt}/{max_plan_review_attempts} failed due to system error. "
                        f"Retrying review directly...[/yellow]"
                    )
                    continue  # Retry review without re-planning
                else:
                    # Exhausted retries with system errors
                    console.print(
                        f"[red]Plan review failed after {max_plan_review_attempts} attempts due to persistent system errors. "
                        f"Moving to final-human-approval for manual intervention.[/red]"
                    )
                    feature.add_history("FINAL_HUMAN_APPROVAL", f"Plan review exhausted retries due to system errors (system errors: {system_error_count}, total attempts: {attempt})")
                    feature.move_to_stage("final-human-approval")
                    feature.save()
                    return
            else:
                # Real plan failure - go back to planning
                if attempt < max_plan_review_attempts:
                    console.print(
                        f"[yellow]Plan review attempt {attempt}/{max_plan_review_attempts} failed due to plan issues. "
                        f"Sending back for re-planning...[/yellow]"
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
            feature.add_history("FINAL_HUMAN_APPROVAL", f"Plan review exhausted retries (system errors: {system_error_count}, total attempts: {max_plan_review_attempts})")
            feature.move_to_stage("final-human-approval")
            feature.save()
            return

    if feature.current_stage != "approved":
        logger.info(f"[pipeline] Feature '{feature.title}' is in '{feature.current_stage}', not 'approved'. Stopping pipeline.")
        return

    # Phase 1: Spec writing + spec review loop
    max_spec_review_attempts = 3
    for spec_attempt in range(1, max_spec_review_attempts + 1):
        # Write specs (or re-write on retry)
        run_spec_writing(feature, runner, status=status)
        
        # Review specs
        latest_spec_review = feature.get_latest_spec_review()
        spec_feedback = latest_spec_review.get("feedback", "") if latest_spec_review else ""
        
        verdict, spec_feedback = run_spec_review(
            feature, runner, feedback=spec_feedback, status=status
        )
        
        if verdict == "PASS":
            config = Config()
            terminal_stage = config.get_terminal_stage(feature.board)
            if terminal_stage == "spec":
                feature.move_to_stage("final-human-approval")
                feature.add_history("TERMINAL_STAGE", "Terminal stage reached (spec) — moved to final-human-approval")
                feature.save()
                _git_commit(feature, "terminal stage reached (spec)")
                console.print("[green]Specs approved. Terminal stage 'spec' reached — moved to final-human-approval.[/green]")
                return
            feature.move_to_stage("implementing")
            feature.add_history("SPECREVIEW", "Specs approved, moving to implementing")
            feature.save()
            _git_commit(feature, "spec review passed")
            break
        else:
            if spec_attempt < max_spec_review_attempts:
                console.print(
                    f"[yellow]Spec review attempt {spec_attempt}/{max_spec_review_attempts} failed. "
                    f"Re-running spec writing with feedback...[/yellow]"
                )
            else:
                # Exhausted retries — proceed to implementing anyway (graceful degradation)
                console.print(
                    f"[yellow]Spec review failed after {max_spec_review_attempts} attempts. "
                    f"Proceeding to implementing anyway.[/yellow]"
                )
                feature.move_to_stage("implementing")
                feature.add_history("SPECREVIEW", f"Spec review exhausted {max_spec_review_attempts} attempts, proceeding anyway")
                feature.save()
                _git_commit(feature, "spec review exhausted retries")

    # Phase 2: Implement, test, review loop
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
