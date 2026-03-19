"""Agent runner — launches AI agents interactively or headlessly."""

import json
import os
import signal
import select
import subprocess
import time
import logging
import threading
import errno
from logging.handlers import RotatingFileHandler
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
        handler = RotatingFileHandler(log_dir / "runner.log", maxBytes=5*1024*1024, backupCount=5)
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
        self._status_lock = threading.RLock()
        self._config = config
        self._agent_name = agent_name
        self._agent_config_override: Optional[AgentConfig] = None
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
        if self._agent_config_override:
            return self._agent_config_override
        if self._agent_name:
            return self._config.agents.get(self._agent_name, self._config.current_agent)
        return self._config.current_agent

    @property
    def workdir(self) -> Path:
        return self._workdir

    def for_phase(self, phase: str) -> "AgentRunner":
        """Create a new runner configured for a specific phase."""
        agent_config = self._config.get_agent_for_phase(phase)
        runner = AgentRunner(self._config, agent_config.name, self._workdir)
        runner._agent_config_override = agent_config
        return runner

    def for_cli_model(self, cli: str, model: Optional[str] = None) -> "AgentRunner":
        """Create a new runner with specific CLI and optional model override."""
        base_agent = self._config.agents.get(cli, self._config.current_agent)
        if model:
            runner = AgentRunner(self._config, cli, self._workdir)
            runner._agent_config_override = AgentConfig(
                name=base_agent.name,
                command=base_agent.command,
                headless_flag=base_agent.headless_flag,
                headless_extra_flags=base_agent.headless_extra_flags,
                model_flag=base_agent.model_flag,
                model=model,
            )
        else:
            runner = AgentRunner(self._config, cli, self._workdir)
            runner._agent_config_override = base_agent
        return runner

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
            
            console.print(f"[dim].tmp/{item_name}.instructions contains the task. Just talk to opencode naturally![/dim]")
            
            # Build command: opencode uses --prompt, claude uses positional arg for interactive TUI
            if self.agent.command.endswith("opencode"):
                cmd = [self.agent.command, "--prompt", f"Read {instructions_path} and follow the instructions exactly."]
            else:
                # claude: positional arg opens TUI with context
                cmd = [self.agent.command, f"Read {instructions_path} and follow the instructions exactly."]
        else:
            console.print(f"[dim]Just talk to opencode naturally![/dim]")
            cmd = [self.agent.command]
        
        # Add model flag if configured
        if self.agent.model:
            cmd.extend([self.agent.model_flag, self.agent.model])

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
            
            # Set up output and marker paths
            output_path = tmp_dir / f"{item_name}.output.json"
            marker_path = tmp_dir / f"{item_name}.complete"
            
            # Clean up old output and marker files from previous runs
            output_path.unlink(missing_ok=True)
            marker_path.unlink(missing_ok=True)
            
            full_prompt = self._prepend_context(prompt)
            
            # Read checkpoint if it exists
            checkpoint_context = self._read_checkpoint(item_name)
            if checkpoint_context:
                full_prompt += checkpoint_context
            
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
            # For opencode: "opencode run --format json <message>"
            # For claude: "claude -p <message> --dangerously-skip-permissions"
            cmd = [self.agent.command]
            
            if self.agent.command.endswith("opencode"):
                # opencode: run comes first, then flags, then message
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

            # Model flag goes at the end for ALL agents (consistent position)
            if self.agent.model:
                cmd.extend([self.agent.model_flag, self.agent.model])

            # Use the workdir
            cwd = str(workdir)

            # Strip CLAUDECODE so claude allows nested invocation
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

            # Set OPENCODE_PERMISSION for opencode headless runs (JSON format)
            if self.agent.command.endswith("opencode"):
                env["OPENCODE_PERMISSION"] = '{"*": "allow"}'

            if status is not None:
                with self._status_lock:
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
                start_new_session=True,
            )

            if status is not None:
                with self._status_lock:
                    status.pid = process.pid

            # Read stdout while process is running (streaming)
            output_lines = []
            is_json_format = "--format" in cmd and "json" in cmd
            
            # Monitor log activity - kill if no activity for 15 minutes
            log_watch_interval = 15 * 60  # 15 minutes
            last_log_time = time.time()
            
            start_time = time.time()
            
            # Debug logging
            logger.info(f"Starting {self.agent.name} in cwd={cwd} with cmd: {cmd}")
            
            # Set up file logging for debugging - always create log for opencode
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
                        with self._status_lock:
                            status.lines.append(f"[runner] Log failed: {e}")
                    log_file = None
            
            # Flag to track if we've warned about stuck agent
            warned_about_stuck = False
            
            while True:
                # Check if user requested kill from TUI
                if status is not None and status.kill_requested:
                    logger.warning(f"[runner] Kill requested by user. Terminating agent.")
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except OSError as e:
                        if e.errno == errno.ESRCH:
                            logger.debug(f"[runner] killpg SIGTERM: process group {process.pid} already dead")
                        elif e.errno == errno.EPERM:
                            logger.error(f"[runner] killpg SIGTERM: permission denied for process group {process.pid}")
                        else:
                            logger.warning(f"[runner] killpg SIGTERM failed for process group {process.pid}: {e}")
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        logger.warning(f"[runner] process.wait() timed out after SIGTERM, force-killing pid {process.pid}")
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except OSError as e:
                            if e.errno == errno.ESRCH:
                                logger.debug(f"[runner] killpg SIGKILL: process group {process.pid} already dead")
                            elif e.errno == errno.EPERM:
                                logger.error(f"[runner] killpg SIGKILL: permission denied for process group {process.pid}")
                            else:
                                logger.warning(f"[runner] killpg SIGKILL failed for process group {process.pid}: {e}")
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            logger.error(f"[runner] Process {process.pid} did not die after kill+wait")
                    if status is not None:
                        with self._status_lock:
                            status.lines.append("[runner] Agent killed by user")
                            status.running = False
                    break

                # Monitor log activity - warn if stuck
                # Use time since last log activity, not time since start
                if log_file:
                    log_file.flush()
                    log_mtime = log_path.stat().st_mtime
                    if log_mtime > last_log_time:
                        last_log_time = log_mtime
                        warned_about_stuck = False
                    else:
                        idle_time = time.time() - last_log_time
                        if idle_time > log_watch_interval:
                            # Log immediately on first occurrence, then every 30 seconds
                            if not warned_about_stuck:
                                logger.warning(f"[runner] No log activity for {int(idle_time)}s. Agent may be stuck. Feature: {feature_slug}, cwd: {cwd}")
                                warned_about_stuck = True

                            # Kill the process after 15 minutes of no activity
                            logger.warning(f"[runner] Killing agent after {int(idle_time)}s of no activity. Feature: {feature_slug}")
                            try:
                                os.killpg(process.pid, signal.SIGTERM)
                            except OSError as e:
                                if e.errno == errno.ESRCH:
                                    logger.debug(f"[runner] killpg SIGTERM: process group {process.pid} already dead")
                                elif e.errno == errno.EPERM:
                                    logger.error(f"[runner] killpg SIGTERM: permission denied for process group {process.pid}")
                                else:
                                    logger.warning(f"[runner] killpg SIGTERM failed for process group {process.pid}: {e}")
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                logger.warning(f"[runner] process.wait() timed out after SIGTERM, force-killing pid {process.pid}")
                                try:
                                    os.killpg(process.pid, signal.SIGKILL)
                                except OSError as e:
                                    if e.errno == errno.ESRCH:
                                        logger.debug(f"[runner] killpg SIGKILL: process group {process.pid} already dead")
                                    elif e.errno == errno.EPERM:
                                        logger.error(f"[runner] killpg SIGKILL: permission denied for process group {process.pid}")
                                    else:
                                        logger.warning(f"[runner] killpg SIGKILL failed for process group {process.pid}: {e}")
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    logger.error(f"[runner] Process {process.pid} did not die after kill+wait")
                            if status is not None:
                                with self._status_lock:
                                    status.lines.append(f"[runner] Agent killed after {int(idle_time)}s of no activity")
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
                    if status is not None:
                        with self._status_lock:
                            status.last_activity_at = time.time()
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
                                        with self._status_lock:
                                            status.lines.append(text)
                            elif event.get("type") == "tool_result":
                                content = event.get("part", {}).get("content", "")
                                if content:
                                    output_lines.append(content)
                                    if status is not None:
                                        with self._status_lock:
                                            status.lines.append(content)
                            elif event.get("type") == "tool_use":
                                tool_name = event.get("part", {}).get("name", "")
                                if tool_name:
                                    tool_line = f"[tool: {tool_name}]"
                                    output_lines.append(tool_line)
                                    if status is not None:
                                        with self._status_lock:
                                            status.lines.append(tool_line)
                            elif event.get("type") == "error":
                                error_msg = event.get("error", {}).get("message", "") or event.get("message", "") or str(event)
                                output_lines.append(error_msg)
                                if status is not None:
                                    with self._status_lock:
                                        status.lines.append(error_msg)
                            elif event.get("type") == "system":
                                system_msg = event.get("message", "") or str(event)
                                output_lines.append(system_msg)
                                if status is not None:
                                    with self._status_lock:
                                        status.lines.append(system_msg)
                            elif event.get("type") == "result":
                                if event.get("is_error"):
                                    result_text = event.get("result", "") or str(event)
                                    output_lines.append(result_text)
                                    if status is not None:
                                        with self._status_lock:
                                            status.lines.append(result_text)
                        except (json.JSONDecodeError, KeyError, TypeError):
                            # Not valid JSON or unexpected format, output as-is
                            output_lines.append(line.rstrip('\n'))
                            if status is not None:
                                with self._status_lock:
                                    status.lines.append(line.rstrip('\n'))
                    else:
                        output_lines.append(line.rstrip('\n'))
                        if status is not None:
                            with self._status_lock:
                                status.lines.append(line.rstrip('\n'))
                    
                    if status is not None:
                        with self._status_lock:
                            if len(status.lines) > 200:
                                status.lines = status.lines[-200:]
            
            stderr_output = process.stderr.read() if process.stderr else ""
            if stderr_output:
                logger.info(f"[runner] stderr ({len(stderr_output)} chars): {stderr_output[:500]}")

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
            
            # Check if we got the completion marker
            if marker_path.exists() and "PHASE_COMPLETE" in marker_path.read_text():
                got_completion_marker = True
            else:
                got_completion_marker = any("DONE" in line.upper() for line in output_lines)
            
            # Only check for API key errors if the phase did NOT complete successfully
            # If the agent wrote the completion marker, the phase succeeded - don't check for auth errors
            if not got_completion_marker:
                auth_error_phrases = [
                    "invalid api key",
                    "invalid_api_key",
                    "api key is invalid",
                    "authentication failed",
                    "unauthorized",
                    "invalid credentials",
                ]
                stderr_lower = (stderr_output or "").lower()
                if any(phrase in stderr_lower for phrase in auth_error_phrases):
                    raise RuntimeError(
                        f"{self.agent.name} authentication error — check your API key configuration.\n"
                        f"Output: {full_output[-300:]}"
                    )

            # Only check for rate limiting if the phase did NOT complete successfully
            # If the agent wrote the completion marker, the phase succeeded - don't check for rate limits
            if not got_completion_marker:
                output_lower = full_output.lower()
                rate_limit_phrases = [
                    "rate limit",
                    "rate limit exceeded",
                    "you are being rate limited",
                    "rate limited due to",
                    "too many requests",
                    "out of extra usage",
                    "out of usage",
                    "you're out of extra usage",
                ]
                if any(phrase in output_lower for phrase in rate_limit_phrases):
                    raise RateLimitError(full_output)
                if stderr_output:
                    stderr_lower = stderr_output.lower()
                    if any(phrase in stderr_lower for phrase in rate_limit_phrases):
                        raise RateLimitError(stderr_output)

            if process.returncode != 0:
                stderr_snippet = stderr_output[:500] if stderr_output else "(no stderr)"
                stdout_snippet = full_output[-500:] if full_output else "(no stdout)"
                raise RuntimeError(
                    f"{self.agent.name} exited with code {process.returncode}.\n"
                    f"stderr: {stderr_snippet}\n"
                    f"stdout (last 500 chars): {stdout_snippet}"
                )

            return full_output

        finally:
            if status is not None:
                with self._status_lock:
                    status.running = False
                    status.pid = None
                    status.last_activity_at = None
                    status.kill_requested = False
                    status.restart_requested = False
            if log_file:
                log_file.close()
                logger.info("[runner] Log file closed")
