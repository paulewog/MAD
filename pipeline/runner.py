"""Agent runner — launches AI agents interactively or headlessly."""

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from rich.console import Console

from agent_status import AgentStatus
from config import AgentConfig, Config

console = Console()


class AgentRunner:
    """Runs the configured AI agent in interactive or headless mode."""

    def __init__(self, config: Config, agent_name: str = None, workdir: Path = None):
        self._config = config
        self._agent_name = agent_name
        # Default workdir to MAD_DIR env var or current directory
        self._workdir = workdir or Path(os.environ.get("MAD_DIR", ".")).expanduser().resolve()

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
        return AgentRunner(self._config, agent_name)

    def interactive(self, workdir: Path, initial_message: str = None) -> None:
        """Launch the agent interactively in workdir.

        If initial_message is given, write it to CONTEXT.md in workdir first.
        """
        workdir = Path(workdir).expanduser().resolve()
        workdir.mkdir(parents=True, exist_ok=True)

        if initial_message:
            context_path = workdir / "CONTEXT.md"
            context_path.write_text(initial_message)

        cmd = [self.agent.command]
        if initial_message:
            cmd.append(initial_message)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        console.print(f"[dim]Launching {self.agent.name} interactively in {workdir}[/dim]")
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
        
        Prompt is passed via stdin to avoid shell escaping issues.
        """
        try:
            # Build command to pipe prompt via stdin
            cmd = [
                self.agent.command,
                self.agent.headless_flag,
            ] + self.agent.headless_extra_flags

            # Use the runner's workdir (defaults to MAD_DIR env var or current dir)
            cwd = str(self._workdir)

            # Strip CLAUDECODE so claude allows nested invocation
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

            if status is not None:
                status.started_at = time.time()
                status.running = True

            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            # Write prompt to stdin
            stdout, stderr_output = process.communicate(input=prompt)

            output_lines = []
            for line in stdout.splitlines():
                output_lines.append(line)
                if status is not None:
                    status.lines.append(line)
                    if len(status.lines) > 200:
                        status.lines = status.lines[-200:]

            if process.returncode != 0:
                stderr_snippet = stderr_output[:500] if stderr_output else "(no stderr)"
                raise RuntimeError(
                    f"{self.agent.name} exited with code {process.returncode}.\n"
                    f"stderr: {stderr_snippet}"
                )

            return "\n".join(output_lines)

        finally:
            if status is not None:
                status.running = False
