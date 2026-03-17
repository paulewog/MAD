"""Script configuration and execution for MAD pipeline."""

import atexit
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import Config
from runner import AgentRunner
from agent_status import AgentStatus

log = logging.getLogger(__name__)

_pending_scripts: list[threading.Thread] = []
_pending_scripts_lock = threading.Lock()
_SCRIPT_WAIT_TIMEOUT = 600  # 10 minutes


def _wait_for_pending_scripts():
    """atexit handler: join all pending script threads so they complete before process exit."""
    with _pending_scripts_lock:
        threads = list(_pending_scripts)
    if not threads:
        return
    log.info(f"Waiting for {len(threads)} pending done script(s) to finish (timeout: {_SCRIPT_WAIT_TIMEOUT}s)...")
    for t in threads:
        t.join(timeout=_SCRIPT_WAIT_TIMEOUT)
        if t.is_alive():
            log.warning(f"Script thread {t.name} did not finish within timeout")


atexit.register(_wait_for_pending_scripts)


@dataclass
class ScriptConfig:
    id: str
    label: str
    command: str
    description: str = ""
    confirm: bool = True
    model: Optional[str] = None
    agent: Optional[str] = None


@dataclass
class ScriptStatus:
    script_id: str
    running: bool = False
    pid: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    lines: list = field(default_factory=list)
    exit_code: Optional[int] = None
    kill_requested: bool = False
    context: str = ""
    _agent_status: Optional[AgentStatus] = None


def load_scripts(mad_dir: Path) -> list[ScriptConfig]:
    scripts_path = mad_dir / "scripts.json"
    if not scripts_path.exists():
        return []
    try:
        data = json.loads(scripts_path.read_text())
    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse scripts.json: {e}")
        return []
    
    if not isinstance(data, list):
        log.warning("scripts.json must be an array")
        return []
    
    scripts = []
    seen_ids = set()
    for entry in data:
        if entry is None:
            continue
        if not entry.get("id"):
            log.warning("Script entry missing 'id', skipping")
            continue
        if not entry.get("label"):
            log.warning(f"Script '{entry['id']}' missing 'label', skipping")
            continue
        if not entry.get("command"):
            log.warning(f"Script '{entry['id']}' missing 'command', skipping")
            continue
        if not re.match(r'^[a-zA-Z0-9-]+$', entry['id']):
            log.warning(f"Script id '{entry['id']}' contains invalid characters, skipping")
            continue
        if entry['id'] in seen_ids:
            log.warning(f"Duplicate script id '{entry['id']}', skipping")
            continue
        seen_ids.add(entry['id'])
        
        description_val = entry.get('description')
        if description_val is None:
            description_val = ""
        elif not isinstance(description_val, str):
            description_val = str(description_val)
        
        confirm_val = entry.get('confirm', True)
        if not isinstance(confirm_val, bool):
            confirm_val = bool(confirm_val)
        
        model_val = entry.get('model')
        if model_val is not None and not isinstance(model_val, str):
            model_val = str(model_val)
        elif model_val == "":
            model_val = None
        
        scripts.append(ScriptConfig(
            id=entry['id'],
            label=entry['label'],
            command=entry['command'],
            description=description_val,
            confirm=confirm_val,
            model=model_val,
            agent=entry.get('agent'),
        ))
    return scripts


def save_scripts(mad_dir: Path, scripts: list[ScriptConfig]) -> bool:
    """Save scripts list to scripts.json."""
    scripts_path = mad_dir / "scripts.json"
    try:
        data = []
        for script in scripts:
            entry = {
                "id": script.id,
                "label": script.label,
                "command": script.command,
                "description": script.description,
                "confirm": script.confirm,
            }
            if script.model:
                entry["model"] = script.model
            if script.agent:
                entry["agent"] = script.agent
            data.append(entry)
        
        scripts_path.write_text(json.dumps(data, indent=2))
        return True
    except Exception as e:
        log.error(f"Failed to save scripts.json: {e}")
        return False


def run_script(script: ScriptConfig, config: Config, status: ScriptStatus, context: str = "") -> int:
    agent_name = script.agent or config._data.get('default_agent', 'claude')
    agent_config = config.agents.get(agent_name, config.current_agent)
    workdir = config.code_path or config.mad_dir
    runner = AgentRunner(config, agent_name, workdir)

    prompt = f"""# Script Execution: {script.label}

## Task
Execute the following command/task and handle any issues that arise:

```
{script.command}
```

{f'## Description\n{script.description}' if script.description else ''}
{f'## Additional Context\n{context}' if context else ''}

## Instructions
- Execute the command above
- If errors occur, attempt to diagnose and fix them
- Report the outcome clearly
"""

    original_model = agent_config.model
    if script.model:
        agent_config.model = script.model

    agent_status = AgentStatus(feature_slug=f"script-{script.id}")
    status._agent_status = agent_status
    
    status.running = True
    status.started_at = datetime.now().isoformat()
    status.pid = None
    status.lines = []

    try:
        runner.headless(
            prompt=prompt,
            workdir=workdir,
            status=agent_status,
            phase_key=f"script-{script.id}",
            output_schema={"summary": "string", "success": "bool"},
        )
        status.lines = list(agent_status.lines)
        status.exit_code = 0
        return 0
    except Exception as e:
        status.lines = list(agent_status.lines)
        status.lines.append(f"Error: {e}")
        status.exit_code = 1
        return 1
    finally:
        if script.model:
            agent_config.model = original_model
        status.running = False
        status.finished_at = datetime.now().isoformat()
        try:
            _save_script_log(config, script, status)
        except Exception as log_error:
            log.warning(f"Failed to save script log: {log_error}")


def _save_script_log(config: Config, script: ScriptConfig, status: ScriptStatus):
    workdir = config.code_path or config.mad_dir
    log_dir = workdir / '.mad' / 'logs'
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    log_path = log_dir / f"script-{script.id}-{timestamp}.log"
    log_path.write_text('\n'.join(status.lines))


def execute_done_script(feature, config: Config) -> None:
    """Execute the done script for a feature if configured."""
    if not feature.done_script:
        return

    scripts = load_scripts(config.mad_dir)
    script = next((s for s in scripts if s.id == feature.done_script), None)

    if not script:
        log.warning(f"Done script '{feature.done_script}' not found in scripts.json")
        return

    feature.add_history("DONE", f"Done script '{script.id}' triggered")

    status = ScriptStatus(script_id=script.id)

    def run_in_background():
        context = f"Triggered by completion of {feature.item_type}: {feature.title}"
        try:
            run_script(script, config, status, context)
            log.info(f"Done script '{script.id}' completed for feature {feature.id}")
            try:
                from state import FeatureFile as FF
                updated = FF(feature.path)
                updated.add_history("DONE", f"Done script '{script.id}' completed (exit 0)")
            except Exception as he:
                log.warning(f"Failed to write completion history: {he}")
        except Exception as e:
            log.error(f"Done script '{script.id}' failed for feature {feature.id}: {e}")
            try:
                from state import FeatureFile as FF
                updated = FF(feature.path)
                updated.add_history("DONE", f"Done script '{script.id}' failed: {e}")
            except Exception as he:
                log.warning(f"Failed to write failure history: {he}")
        finally:
            with _pending_scripts_lock:
                if thread in _pending_scripts:
                    _pending_scripts.remove(thread)

    thread = threading.Thread(
        target=run_in_background,
        daemon=True,
        name=f"done-script-{script.id}-{feature.id}"
    )
    with _pending_scripts_lock:
        _pending_scripts.append(thread)
    thread.start()
