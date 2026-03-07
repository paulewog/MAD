"""Agent runner — launches AI agents interactively or headlessly."""

import json
import os
import select
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

from rich.console import Console

from agent_status import AgentStatus
from config import AgentConfig, Config, read_context_file, get_mad_dir

console = Console()

logger = logging.getLogger("runner")
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
        handler = logging.FileHandler(log_dir / "runner.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    except Exception:
        pass


class RateLimitError(Exception):
    """Raised when the AI agent is rate limited."""
    pass


class AgentRunner:
    """Runs the configured AI agent in interactive or headless mode."""

    def __init__(self, config: Config, agent_name: str = None, workdir: Path = None):
        self._config = config
        self._agent_name = agent_name
        if workdir is not None:
            self._workdir = workdir
        else:
            code_path = config.code_path
            if code_path is not None:
                self._workdir = code_path
            else:
                self._workdir = Path(os.environ.get("MAD_DIR", ".")).expanduser().resolve()

    @property
    def agent(self) -> AgentConfig:
        if self._agent_name:
            return self._config.agents.get(self._agent_name, self._config.current_agent)
        return self._config.current_agent

    @property
    def workdir(self) -> Path:
        return self._workdir

    def for_phase(self, phase: str) -> "AgentRunner":
        """Create a new runner configured for a specific phase."""
        agent_name = self._config.agent_for_phase.get(phase)
        return AgentRunner(self._config, agent_name, self._workdir)

    def _build_completion_block(self, output_path: Path, marker_path: Path, schema: dict) -> str:
        """Build the output/completion instructions block appended to every prompt."""
        schema_desc = "\n".join(f'  - "{k}": {v}' for k, v in schema.items())
        return f"""

## Output and Completion

When you have finished all work:

1. Write your output as a single valid JSON object to:
   {output_path}

   Required fields:
{schema_desc}

2. Create this marker file:
   {marker_path}

   Write exactly: PHASE_COMPLETE

3. Exit immediately after creating the marker file. Do not wait for input.
"""

    def _prepend_context(self, prompt: str) -> str:
        """Prepend code path and context file info to prompt."""
        code_path = self._config.code_path
        context_file = self._config.context_file
        
        header = []
        if code_path:
            header.append(f"Code Path: {code_path}")
        header.append(f"Context File: {context_file}")
        
        context_content = read_context_file(self._config)
        
        parts = ["\n".join(header), ""]
        if context_content:
            parts.append(context_content)
            parts.append("")
        parts.append(prompt)
        
        return "\n".join(parts)

    def _read_checkpoint(self, feature_slug: str) -> Optional[str]:
        """Read checkpoint file and return resume context string, or None."""
        if not feature_slug or feature_slug == 'default':
            return None
        
        checkpoint_dir = self._workdir / '.mad' / 'checkpoints'
        checkpoint_path = checkpoint_dir / f'{feature_slug}.checkpoint.json'
        
        if not checkpoint_path.exists():
            return None
        
        try:
            raw = checkpoint_path.read_text()
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f'[runner] Invalid/unreadable checkpoint at {checkpoint_path}: {e}')
            return None
        
        if not isinstance(data, dict):
            logger.warning(f'[runner] Checkpoint is not a JSON object: {checkpoint_path}')
            return None
        if 'feature_slug' not in data or 'phase' not in data:
            logger.warning(f'[runner] Checkpoint missing required fields (feature_slug, phase): {checkpoint_path}')
            return None
        
        completed = data.get('completed_steps', [])
        next_step = data.get('next_step', 'Unknown')
        notes = data.get('notes', '')
        files = data.get('files_modified', [])
        phase = data.get('phase', 'unknown')
        
        lines = [
            '',
            '## Resuming from checkpoint',
            f'A previous agent session was interrupted during the "{phase}" phase.',
            'Here is the progress so far:',
            '',
        ]
        if completed:
            lines.append('Completed steps:')
            for step in completed:
                lines.append(f'  - {step}')
            lines.append('')
        lines.append(f'Next step: {next_step}')
        if notes:
            lines.append(f'Notes: {notes}')
        if files:
            lines.append(f'Files modified: {", ".join(files)}')
        lines.append('')
        lines.append('Resume from where the previous agent left off. Do NOT redo completed work.')
        lines.append('')
        
        logger.info(f'[runner] Loaded checkpoint for {feature_slug}: {len(completed)} completed steps')
        return '\n'.join(lines)

    def interactive(self, workdir: Path, initial_message: str = None) -> None:
        """Launch the agent interactively in workdir.

        If initial_message is given, write it to .tmp/<item>.instructions and use --prompt.
        """
        _ensure_logging()
        workdir = Path(workdir).expanduser().resolve()
        workdir.mkdir(parents=True, exist_ok=True)

        # Remove CLAUDECODE to allow nested invocation
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env["TERM"] = "xterm-256color"
        
        console.print(f"[dim]Launching {self.agent.name} interactively in {workdir}[/dim]")
        
        if initial_message:
            full_message = self._prepend_context(initial_message)
            
            # Create .tmp directory
            tmp_dir = workdir / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            
            # Extract item name from workdir or use default
            item_name = workdir.name
            
            # Write instructions to .tmp/<item>.instructions
            instructions_path = tmp_dir / f"{item_name}.instructions"
            instructions_path.write_text(full_message)
            logger.info(f"[runner] Interactive instructions for '{item_name}': {len(full_message)} chars")
            logger.info(f"[runner] Instructions preview: {full_message[:200]}...")
            
            console.print(f"[dim].tmp/{item_name}.instructions contains the task. Just talk to kilo naturally![/dim]")
            
            # Build command: kilo uses --prompt, claude uses positional arg for interactive TUI
            if self.agent.command == "kilo":
                cmd = [self.agent.command, "--prompt", f"Read {instructions_path} and follow the instructions exactly."]
            else:
                # claude: positional arg opens TUI with context
                cmd = [self.agent.command, f"Read {instructions_path} and follow the instructions exactly."]
        else:
            console.print(f"[dim]Just talk to kilo naturally![/dim]")
            cmd = [self.agent.command]
        
        # Use subprocess.run with shell=False for proper argument handling
        subprocess.run(cmd, cwd=str(workdir), env=env)

    def headless(
        self,
        prompt: str,
        workdir: Path = None,
        status: Optional[AgentStatus] = None,
        phase_key: str = None,
        output_schema: dict = None,
    ) -> str:
        """Run the agent headlessly with the given prompt. Returns stdout.

        If status is provided, updates status.running, status.started_at, and
        appends each stdout line to status.lines (capped at 200 entries).
        stderr is captured for error messages but not added to status.lines.
        
        Instructions are written to .tmp/<item>.instructions file and read via --prompt.
        """
        _ensure_logging()
        log_file = None
        try:
            workdir = workdir or self._workdir
            workdir = Path(workdir).expanduser().resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            
            # Get item name from status if available
            item_name = "default"
            if status is not None:
                item_name = getattr(status, 'feature_slug', None) or item_name
            
            # Create .tmp directory
            tmp_dir = workdir / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            
            # Delete any existing .instructions* files for this item
            for f in tmp_dir.glob(f"{item_name}.instructions*"):
                f.unlink()
            
            full_prompt = self._prepend_context(prompt)
            
            # Read checkpoint if it exists
            checkpoint_context = self._read_checkpoint(item_name)
            if checkpoint_context:
                full_prompt += checkpoint_context
            
            # Set up output and marker paths
            output_path = tmp_dir / f"{item_name}.output.json"
            marker_path = tmp_dir / f"{item_name}.complete"

            # Delete any existing marker file before starting
            marker_path.unlink(missing_ok=True)

            if phase_key and output_schema:
                full_prompt += self._build_completion_block(output_path, marker_path, output_schema)
            else:
                # Legacy fallback for phases not yet using the new system
                full_prompt += f"\n\nIMPORTANT: Write your complete output as JSON to this file: {output_path}\n"
                full_prompt += "Write ONLY valid JSON to this file. Nothing else.\n"
                full_prompt += "After writing the file, write DONE on its own line to signal completion.\n"
            
            # Write instructions to .tmp/<item>.instructions file
            instructions_path = tmp_dir / f"{item_name}.instructions"
            instructions_path.write_text(full_prompt)
            logger.info(f"[runner] Wrote instructions for '{item_name}': {len(full_prompt)} chars")
            logger.info(f"[runner] Instructions preview: {full_prompt[:200]}...")
            
            # Build command:
            # For kilo: "kilo run --format json --auto <message>"
            # For claude: "claude -p <message> --dangerously-skip-permissions"
            cmd = [self.agent.command]
            
            if self.agent.command == "kilo":
                # kilo: run comes first, then flags, then message
                for f in self.agent.headless_extra_flags:
                    if not f.startswith("-"):
                        cmd.append(f)
                for f in self.agent.headless_extra_flags:
                    if f.startswith("-"):
                        cmd.append(f)
                cmd.append(f"Read {instructions_path} and follow the instructions exactly.")
            else:
                # claude: -p flag, then message, then extra flags
                if self.agent.headless_flag:
                    cmd.append(self.agent.headless_flag)
                cmd.append(f"Read {instructions_path} and follow the instructions exactly.")
                cmd.extend(self.agent.headless_extra_flags)

            # Use the workdir
            cwd = str(workdir)

            # Strip CLAUDECODE so claude allows nested invocation
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

            if status is not None:
                status.started_at = time.time()
                status.running = True

            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            # Read stdout while process is running (streaming)
            output_lines = []
            is_json_format = "--format" in cmd and "json" in cmd
            
            # Monitor log activity - kill if no activity for 15 minutes
            log_watch_interval = 15 * 60  # 15 minutes
            last_log_time = time.time()
            
            start_time = time.time()
            
            # Debug logging
            logger.info(f"Starting {self.agent.name} in cwd={cwd} with cmd: {cmd}")
            
            # Set up file logging for debugging - always create log for kilo
            log_file = None
            feature_slug = "unknown"
            if status is not None:
                feature_slug = getattr(status, 'feature_slug', 'unknown') or 'unknown'
                logger.info(f"status.feature_slug = {feature_slug!r}")
                
            if status is not None and feature_slug != 'unknown':
                log_dir = self._workdir
                try:
                    log_dir = log_dir.resolve()
                    (log_dir / "logs").mkdir(parents=True, exist_ok=True)
                    timestamp = int(time.time())
                    log_path = log_dir / "logs" / f"{self.agent.name}-{feature_slug}-{timestamp}.log"
                    log_file = open(log_path, "w")
                    logger.info(f"Logging to: {log_path}")
                except Exception as e:
                    if status is not None:
                        status.lines.append(f"[runner] Log failed: {e}")
                    log_file = None
            
            # Flag to track if we've warned about stuck agent
            warned_about_stuck = False
            
            while True:
                # Monitor log activity - warn if stuck
                elapsed = time.time() - start_time
                
                # Check log file for activity (if we have a log file)
                if log_file:
                    log_file.flush()
                    log_mtime = log_path.stat().st_mtime
                    if log_mtime > last_log_time:
                        last_log_time = log_mtime
                    elif elapsed > log_watch_interval:
                        # Log immediately on first occurrence, then every 30 seconds
                        if not warned_about_stuck:
                            logger.warning(f"[runner] No log activity for {int(elapsed)}s. Agent may be stuck. Feature: {feature_slug}, cwd: {cwd}")
                            warned_about_stuck = True
                        elif int(elapsed) % 30 == 0:
                            logger.warning(f"[runner] No log activity for {int(elapsed)}s. Agent may be stuck. Feature: {feature_slug}, cwd: {cwd}")
                        
                        # Kill the process after 15 minutes of no activity
                        logger.warning(f"[runner] Killing agent after {int(elapsed)}s of no activity. Feature: {feature_slug}")
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        if status is not None:
                            status.lines.append(f"[runner] Agent killed after {int(elapsed)}s of no activity")
                            status.running = False
                        break
                
                line = ""
                if process.stdout:
                    ready, _, _ = select.select([process.stdout], [], [], 0)
                    if ready:
                        line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    # Check for completion marker
                    if line.strip().upper() == "DONE":
                        logger.info(f"[runner] Received DONE signal")
                    
                    # Write raw line to log file for debugging
                    if log_file:
                        log_file.write(line)
                        log_file.flush()
                    
                    # If using JSON format (kilo), extract text from JSON events
                    if is_json_format:
                        try:
                            import json
                            event = json.loads(line.strip())
                            if event.get("type") == "text":
                                text = event.get("part", {}).get("text", "")
                                if text:
                                    output_lines.append(text)
                                    if status is not None:
                                        status.lines.append(text)
                            elif event.get("type") == "tool_result":
                                content = event.get("part", {}).get("content", "")
                                if content:
                                    output_lines.append(content)
                                    if status is not None:
                                        status.lines.append(content)
                            elif event.get("type") == "tool_use":
                                tool_name = event.get("part", {}).get("name", "")
                                if tool_name:
                                    tool_line = f"[tool: {tool_name}]"
                                    output_lines.append(tool_line)
                                    if status is not None:
                                        status.lines.append(tool_line)
                        except (json.JSONDecodeError, KeyError, TypeError):
                            # Not valid JSON or unexpected format, output as-is
                            output_lines.append(line.rstrip('\n'))
                            if status is not None:
                                status.lines.append(line.rstrip('\n'))
                    else:
                        output_lines.append(line.rstrip('\n'))
                        if status is not None:
                            status.lines.append(line.rstrip('\n'))
                    
                    if status is not None and len(status.lines) > 200:
                        status.lines = status.lines[-200:]
            
            stderr_output = process.stderr.read() if process.stderr else ""
            
            # Try to read output from file first (preferred)
            output_from_file = ""
            if output_path.exists():
                try:
                    output_from_file = output_path.read_text()
                    logger.info(f"[runner] Read {len(output_from_file)} chars from output file")
                except Exception as e:
                    logger.warning(f"[runner] Failed to read output file: {e}")
            
            # Use file output if available, otherwise fall back to stdout
            full_output = output_from_file if output_from_file else "\n".join(output_lines)
            if not output_from_file:
                logger.warning(f"[runner] No output file found, using stdout")
            
            # Check if we got the completion marker - if so, don't check for rate limit
            if marker_path.exists() and "PHASE_COMPLETE" in marker_path.read_text():
                got_completion_marker = True
            else:
                got_completion_marker = any("DONE" in line.upper() for line in output_lines)
            
            # Only check for rate limiting if we didn't get a completion marker
            if not got_completion_marker:
                output_lower = full_output.lower()
                rate_limit_phrases = [
                    "rate limit",
                    "rate limit exceeded", 
                    "you are being rate limited",
                    "rate limited due to",
                    "too many requests",
                ]
                if any(phrase in output_lower for phrase in rate_limit_phrases):
                    raise RateLimitError(full_output)

            if process.returncode != 0:
                stderr_snippet = stderr_output[:500] if stderr_output else "(no stderr)"
                raise RuntimeError(
                    f"{self.agent.name} exited with code {process.returncode}.\n"
                    f"stderr: {stderr_snippet}"
                )

            return full_output

        finally:
            if status is not None:
                status.running = False
            if log_file:
                log_file.close()
                logger.info("[runner] Log file closed")
