"""Agent runner — launches AI agents interactively or headlessly."""

import os
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

from rich.console import Console

from agent_status import AgentStatus
from config import AgentConfig, Config, read_context_file

console = Console()

# Set up file logging - less verbose
_log_dir = Path(__file__).parent.parent / ".mad" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "runner.log"

logging.basicConfig(
    level=logging.INFO,  # Changed from DEBUG
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("runner")


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

    def interactive(self, workdir: Path, initial_message: str = None) -> None:
        """Launch the agent interactively in workdir.

        If initial_message is given, write it to .tmp/<item>.instructions and use --prompt.
        """
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
    ) -> str:
        """Run the agent headlessly with the given prompt. Returns stdout.

        If status is provided, updates status.running, status.started_at, and
        appends each stdout line to status.lines (capped at 200 entries).
        stderr is captured for error messages but not added to status.lines.
        
        Instructions are written to .tmp/<item>.instructions file and read via --prompt.
        """
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
            
            # Debug logging
            logger.info(f"Starting {self.agent.name} with cmd: {cmd}")
            
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
            
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
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
            full_output = "\n".join(output_lines)

            # Check for rate limiting
            if "out of" in full_output.lower() and "usage" in full_output.lower():
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
                print(f"[runner] Log file closed", file=__import__('sys').stderr)
