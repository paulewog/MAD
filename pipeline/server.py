"""Headless pipeline server — single source of truth for all pipeline state.

Manages features, boards, agents, and scripts. Accepts commands from:
  - Local TUI clients via Unix socket (NDJSON framing)
  - Remote WebUI via bidirectional WebSocket to Golang server

State is broadcast to all connected clients after every mutation.
"""

import asyncio
import json
import logging
import os
import signal
import socket as sock
import time
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from agent_status import AgentStatus
from config import Config, BUILTIN_AGENTS, PIPELINE_PHASES, get_mad_dir, socket_path_for_project
from framing import JsonFramer
from lock import PipelineLock
from runner import AgentRunner, RateLimitError
from scripts import ScriptConfig, ScriptStatus, load_scripts, run_script
from state import STAGES, STAGE_ACTIONS, FeatureFile

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

logger = logging.getLogger("pipeline")

# Auto-run cooldown and retry constants
AUTO_RETRY_COOLDOWN = 120.0  # seconds before retrying a failed auto-run
MAX_AUTO_RETRIES = 5  # max consecutive failures before giving up


def _get_checkpoint_info(feature) -> Optional[dict]:
    """Read checkpoint file for a feature, or None if not found."""
    try:
        config = Config()
        checkpoint_path = config.mad_dir / "checkpoints" / f"{feature.slug}.checkpoint.json"
        if not checkpoint_path.exists():
            return None
        with open(checkpoint_path) as f:
            data = json.load(f)
        return {
            "exists": True,
            "last_checkpoint": data.get("last_checkpoint", ""),
            "completed_steps_count": len(data.get("completed_steps", [])),
            "next_step": data.get("next_step", "")[:200] if data.get("next_step") else "",
        }
    except Exception as e:
        logger.warning(f"[server] Failed to read checkpoint for {feature.slug}: {e}")
        return None


class LocalClient:
    """Tracks one TUI client connected via Unix socket."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.closed = False

    async def send(self, msg: dict) -> None:
        if self.closed:
            return
        try:
            await JsonFramer.write_message(self.writer, msg)
        except Exception:
            self.closed = True

    def close(self) -> None:
        self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass


class PipelineServer:
    """Headless server that manages all pipeline state."""

    def __init__(self, socket_path: str = None):
        self._socket_path = socket_path or socket_path_for_project()
        self._local_clients: set[LocalClient] = set()
        self._unix_server: Optional[asyncio.AbstractServer] = None
        self._golang_ws = None
        self._agent_processes: list = []

        # Config
        self._config = Config()
        self._config.setup_boards()

        # Golang WS config
        server_cfg = self._config.server
        self._golang_ws_url: Optional[str] = None
        self._golang_api_key: Optional[str] = None
        self._golang_client_id: Optional[str] = None
        if server_cfg:
            url = server_cfg.url.rstrip("/")
            # Convert http(s) to ws(s) if needed
            if url.startswith("http://"):
                url = "ws://" + url[7:]
            elif url.startswith("https://"):
                url = "wss://" + url[8:]
            self._golang_ws_url = url + "/ws"
            self._golang_api_key = server_cfg.api_key
            self._golang_client_id = server_cfg.client_id

        # State — moved from tui.py
        self._plan_agent_status = AgentStatus()
        self._implement_agent_status = AgentStatus()
        self._plan_running = False
        self._implement_running = False
        self._auto_plan_enabled = False
        self._auto_implement_enabled = False
        self._auto_plan_queued: set[str] = set()
        self._auto_implement_queued: set[str] = set()
        self._auto_plan_fail_cooldown: dict[str, float] = {}
        self._auto_implement_fail_cooldown: dict[str, float] = {}
        self._auto_plan_fail_count: dict[str, int] = {}
        self._auto_implement_fail_count: dict[str, int] = {}
        self._rate_limit_until: float = 0.0
        self._scripts: list[ScriptConfig] = load_scripts(self._config.mad_dir)
        self._script_status: Optional[ScriptStatus] = None
        self._pipeline_lock = PipelineLock(self._config)
        self._log_buffer: list[str] = []
        self._shutting_down = False

    # ── Startup / Shutdown ──────────────────────────────────────────────

    def run(self) -> None:
        """Blocking entry point — run the server event loop."""
        self._setup_logging()
        self._restore_auto_mode()
        asyncio.run(self._start())

    def _setup_logging(self) -> None:
        """Configure logging for the server process."""
        log_dir = self._config.mad_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "server.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[
                RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5),
                logging.StreamHandler(),  # also log to stderr for journald
            ],
        )
        logger.info("[server] Logging initialized")

    def _restore_auto_mode(self) -> None:
        """Restore auto-mode state from persistent state file."""
        state_file = self._config.mad_dir / ".server-state.json"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
                self._auto_plan_enabled = state.get("auto_plan", False)
                self._auto_implement_enabled = state.get("auto_impl", False)
                logger.info(f"[server] Restored auto-mode: plan={self._auto_plan_enabled}, impl={self._auto_implement_enabled}")
            except Exception as e:
                logger.warning(f"[server] Failed to restore auto-mode: {e}")

    def _save_auto_mode(self) -> None:
        """Persist auto-mode state so it survives server restarts."""
        state_file = self._config.mad_dir / ".server-state.json"
        try:
            with open(state_file, "w") as f:
                json.dump({
                    "auto_plan": self._auto_plan_enabled,
                    "auto_impl": self._auto_implement_enabled,
                }, f)
        except Exception as e:
            logger.warning(f"[server] Failed to save auto-mode: {e}")

    async def _start(self) -> None:
        """Start all connections and begin serving."""
        self._cleanup_stale_socket()

        # Register signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))

        # Start Unix socket server
        self._unix_server = await asyncio.start_unix_server(
            self._handle_local_client, path=self._socket_path
        )
        os.chmod(self._socket_path, 0o600)
        logger.info(f"[server] Listening on {self._socket_path}")

        # Start Golang WebSocket connection (background task)
        golang_task = None
        if self._golang_ws_url and HAS_WEBSOCKETS:
            golang_task = asyncio.create_task(self._connect_golang())

        # Start periodic tasks
        broadcast_task = asyncio.create_task(self._periodic_broadcast())
        auto_run_task = asyncio.create_task(self._auto_run_loop())

        logger.info("[server] Pipeline server started")

        # Wait until shutdown
        try:
            await self._unix_server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            for task in [golang_task, broadcast_task, auto_run_task]:
                if task:
                    task.cancel()

    async def _shutdown(self) -> None:
        """Graceful shutdown sequence."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("[server] Shutdown signal received")

        # 1. Stop accepting new connections
        if self._unix_server:
            self._unix_server.close()
            await self._unix_server.wait_closed()

        # 2. Close local TUI clients
        for client in list(self._local_clients):
            client.close()
        self._local_clients.clear()

        # 3. Close Golang WebSocket
        if self._golang_ws:
            try:
                await self._golang_ws.close()
            except Exception:
                pass

        # 4. Signal running agents to stop
        for proc in self._agent_processes:
            try:
                proc.terminate()
            except Exception:
                pass

        # 5. Clean up socket file
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

        logger.info("[server] Shutdown complete")

    def _cleanup_stale_socket(self) -> None:
        """Remove stale socket from a previous crash.

        If the socket exists, try connecting. If refused (no listener), unlink.
        If something IS listening, raise — another server is running.
        """
        path = self._socket_path
        if not os.path.exists(path):
            return
        test = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
        try:
            test.connect(path)
            test.close()
            raise RuntimeError(f"Another server is already listening on {path}")
        except ConnectionRefusedError:
            os.unlink(path)
            logger.info(f"[server] Removed stale socket {path}")
        finally:
            test.close()

    # ── Local TUI Client Handling ───────────────────────────────────────

    async def _handle_local_client(self, reader: asyncio.StreamReader,
                                    writer: asyncio.StreamWriter) -> None:
        """Handle a TUI client connected via Unix socket."""
        client = LocalClient(reader, writer)
        self._local_clients.add(client)
        logger.info(f"[server] TUI client connected ({len(self._local_clients)} total)")

        # Send full state on connect
        await client.send(self._build_state_broadcast())

        try:
            while not client.closed:
                try:
                    msg = await JsonFramer.read_message(reader)
                except ConnectionError:
                    break
                except json.JSONDecodeError:
                    logger.warning("[server] Bad JSON from TUI client")
                    continue

                # Dispatch command and send response
                response = await self._dispatch_command(msg, source="tui")
                await client.send(response)
        except Exception as e:
            logger.warning(f"[server] TUI client error: {e}")
        finally:
            client.close()
            self._local_clients.discard(client)
            logger.info(f"[server] TUI client disconnected ({len(self._local_clients)} total)")

    # ── Golang WebSocket Connection ─────────────────────────────────────

    async def _connect_golang(self) -> None:
        """Maintain persistent bidirectional WebSocket to Golang server.

        Absorbs the role of server_client.py.
        """
        backoff = 1.0
        max_backoff = 30.0

        while not self._shutting_down:
            try:
                async with websockets.connect(
                    self._golang_ws_url,
                    additional_headers={"Authorization": f"Bearer {self._golang_api_key}"},
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    self._golang_ws = ws

                    # Register with Go server
                    await ws.send(json.dumps({
                        "type": "register",
                        "client_id": self._golang_client_id,
                        "api_key": self._golang_api_key,
                    }))
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(response)
                    if data.get("type") != "ack":
                        logger.warning(f"[server] Golang registration rejected: {data}")
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, max_backoff)
                        continue

                    logger.info(f"[server] Connected to Golang server as '{self._golang_client_id}'")
                    backoff = 1.0

                    # Push full state immediately
                    await self._push_state_to_golang()

                    # Listen for incoming WebUI commands
                    async for message in ws:
                        await self._handle_golang_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[server] Golang connection error: {e}")
                self._golang_ws = None

            if not self._shutting_down:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def _handle_golang_message(self, raw: str) -> None:
        """Handle an incoming message from Go server (originated from WebUI)."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[server] Bad JSON from Golang")
            return

        msg_type = data.get("type")

        # Map Go server message types to internal commands
        cmd_map = {
            "answer_questions": "answer_questions",
            "set_auto_mode": "set_auto_mode",
            "start_agent": "start_agent",
            "create_idea": "create_idea",
            "move_feature": "move_feature",
            "edit_description": "edit_description",
            "edit_done_script": "edit_done_script",
            "edit_title": "edit_title",
            "edit_item_type": "edit_item_type",
            "edit_ideation_prompt": "edit_ideation_prompt",
            "edit_depends_on": "edit_depends_on",
            "run_script": "run_script",
            "set_agent_for_phase": "set_agent_for_phase",
            "restart_tui": "restart_server",
        }

        if msg_type not in cmd_map:
            return

        # Convert Go message to internal command format
        cmd = cmd_map[msg_type]
        args = {k: v for k, v in data.items() if k != "type"}
        internal = {"cmd": cmd, "args": args}

        response = await self._dispatch_command(internal, source="webui")

        # For move_feature, send result back to Go server
        if msg_type == "move_feature" and data.get("request_id"):
            success = response.get("type") == "ok"
            error = response.get("message", "") if not success else ""
            await self._send_move_result(data["request_id"], success, error)

    async def _send_move_result(self, request_id: str, success: bool, error: str = "") -> None:
        """Send move result back to Golang server."""
        if not self._golang_ws:
            return
        msg = {"type": "move_result", "request_id": request_id, "success": success}
        if error:
            msg["error"] = error
        try:
            await self._golang_ws.send(json.dumps(msg))
        except Exception as e:
            logger.warning(f"[server] Failed to send move_result: {e}")

    async def _push_state_to_golang(self) -> None:
        """Push full state to Golang server (same format as old server_client.py)."""
        if not self._golang_ws:
            return
        try:
            msg = self._build_golang_state_message()
            await self._golang_ws.send(json.dumps(msg))
        except Exception as e:
            logger.warning(f"[server] Failed to push state to Golang: {e}")

    # ── Command Dispatch ────────────────────────────────────────────────

    async def _dispatch_command(self, msg: dict, source: str = "tui") -> dict:
        """Route a command to the appropriate handler.

        Returns a response dict with the original request 'id' (if present).
        """
        req_id = msg.get("id")
        cmd = msg.get("cmd", "")
        args = msg.get("args", {})

        try:
            result = await self._handle_command(cmd, args, source)
            response = {"type": "ok", "data": result}
        except Exception as e:
            logger.warning(f"[server] Command '{cmd}' failed: {e}")
            response = {"type": "error", "message": str(e)}

        if req_id is not None:
            response["id"] = req_id

        return response

    async def _handle_command(self, cmd: str, args: dict, source: str) -> dict:
        """Execute a command and return result data."""
        handler = {
            "get_state": self._cmd_get_state,
            "move_feature": self._cmd_move_feature,
            "create_idea": self._cmd_create_idea,
            "start_agent": self._cmd_start_agent,
            "stop_agent": self._cmd_stop_agent,
            "set_auto_mode": self._cmd_set_auto_mode,
            "edit_description": self._cmd_edit_description,
            "edit_title": self._cmd_edit_title,
            "edit_done_script": self._cmd_edit_done_script,
            "edit_item_type": self._cmd_edit_item_type,
            "edit_ideation_prompt": self._cmd_edit_ideation_prompt,
            "edit_depends_on": self._cmd_edit_depends_on,
            "answer_questions": self._cmd_answer_questions,
            "run_script": self._cmd_run_script,
            "set_agent_for_phase": self._cmd_set_agent_for_phase,
            "restart_server": self._cmd_restart_server,
        }.get(cmd)

        if not handler:
            raise ValueError(f"Unknown command: {cmd}")

        result = await handler(args)

        # Broadcast updated state after any mutation (except get_state)
        if cmd != "get_state":
            await self._broadcast_state()

        return result

    # ── Command Handlers ────────────────────────────────────────────────

    async def _cmd_get_state(self, args: dict) -> dict:
        """Return full state."""
        return self._build_state_broadcast().get("data", {})

    async def _cmd_move_feature(self, args: dict) -> dict:
        """Move feature to a different stage."""
        feature_id = args.get("feature_id", "")
        target_stage = args.get("target_stage", "")
        reason = args.get("reason", "")

        feature = FeatureFile.find(feature_id)
        if not feature:
            raise ValueError(f"Feature {feature_id} not found")
        if target_stage not in STAGES:
            raise ValueError(f"Invalid target stage: {target_stage}")

        logger.info(f"[server] Moving {feature_id} to {target_stage}")

        if target_stage == "ideas" and feature.current_stage == "awaiting-human-approval" and reason:
            feature.add_plan_review("FAIL", f"Human rejected plan: {reason}", reason)
            feature.add_history("REJECTED", f"Plan rejected by human: {reason}")
        elif target_stage == "implementing" and feature.current_stage == "final-human-approval" and reason:
            from phases import _get_latest_feedback
            prev_feedback = _get_latest_feedback(feature)
            full_feedback = f"Human rejection: {reason}\n\nPrevious review feedback:\n{prev_feedback}"
            feature.add_impl_review("FAIL", f"Human rejected from final-human-approval: {reason}", full_feedback)
            feature.add_history("IMPLEMENTING", f"Sent back to implementing: {reason or 'No reason given'}")
        else:
            feature.add_history(target_stage.upper(), "Moved via server command")

        feature.move_to_stage(target_stage)

        from state import resolve_dependencies
        all_features = self._load_all_features()
        dep_status = resolve_dependencies(feature.id, all_features)
        if dep_status['blocked']:
            blocked_note = "Warning: moved while dependencies unresolved"
            if dep_status['dangling_deps']:
                blocked_note += f" (dangling: {dep_status['dangling_deps']})"
            feature.add_history(target_stage.upper(), blocked_note)

        if target_stage == "rejected":
            impl_stages_set = {"approved", "spec-writing", "implementing", "testing", "review"}
            for other in all_features:
                if feature.id in other.depends_on and other.current_stage in impl_stages_set:
                    other.add_history("PLAN-INBOX", f"Dependency {feature.id} ({feature.title}) was rejected — re-planning required")
                    other.move_to_stage("plan-inbox")
                    logger.info(f"[server] Moved {other.slug} to plan-inbox: dependency {feature.slug} rejected")

        if target_stage == "done":
            from scripts import execute_done_script
            try:
                execute_done_script(feature, self._config)
            except Exception as e:
                logger.warning(f"[server] Done script failed: {e}")

        return {"moved": True, "slug": feature.slug, "stage": target_stage}

    async def _cmd_create_idea(self, args: dict) -> dict:
        """Create a new feature/idea."""
        title = args.get("title", "")
        board = args.get("board", "")
        description = args.get("description", "")
        item_type = args.get("item_type", "feature")
        done_script = args.get("done_script", "")
        requires_human_approval = args.get("requires_human_approval", False)

        if not title or not board:
            raise ValueError("title and board are required")

        feature = FeatureFile.create(
            board, title, description,
            item_type=item_type,
            done_script=done_script,
            requires_human_approval=requires_human_approval,
        )
        logger.info(f"[server] Created idea: {feature.title} ({feature.id})")
        return {"id": feature.id, "slug": feature.slug}

    async def _cmd_start_agent(self, args: dict) -> dict:
        """Start plan or implement agent for a feature."""
        feature_id = args.get("feature_id", "")
        action = args.get("action", "")

        feature = FeatureFile.find(feature_id)
        if not feature:
            raise ValueError(f"Feature {feature_id} not found")
        if action not in STAGE_ACTIONS:
            raise ValueError(f"Unknown action: {action}")
        if feature.current_stage not in STAGE_ACTIONS[action]:
            raise ValueError(f"Cannot {action} from stage {feature.current_stage}")

        if action == "plan":
            if self._plan_running:
                raise ValueError("Plan agent already running")
            asyncio.get_running_loop().run_in_executor(None, self._run_plan_sync, feature)
        elif action == "implement":
            if self._implement_running:
                raise ValueError("Implement agent already running")
            asyncio.get_running_loop().run_in_executor(None, self._run_implement_sync, feature)

        return {"started": True, "action": action, "feature": feature.slug}

    async def _cmd_stop_agent(self, args: dict) -> dict:
        """Stop a running agent."""
        phase = args.get("phase", "")
        if phase == "plan" and self._plan_running:
            self._plan_agent_status.kill_requested = True
            return {"stopped": "plan"}
        elif phase == "implement" and self._implement_running:
            self._implement_agent_status.kill_requested = True
            return {"stopped": "implement"}
        raise ValueError(f"No {phase} agent running")

    async def _cmd_set_auto_mode(self, args: dict) -> dict:
        """Toggle auto-plan or auto-implement."""
        mode = args.get("mode", "")
        enabled = args.get("enabled", False)
        if mode == "plan":
            self._auto_plan_enabled = enabled
        elif mode == "impl":
            self._auto_implement_enabled = enabled
        else:
            raise ValueError(f"Unknown mode: {mode}")
        self._save_auto_mode()
        return {"mode": mode, "enabled": enabled}

    async def _cmd_edit_description(self, args: dict) -> dict:
        feature = FeatureFile.find(args.get("feature_id", ""))
        if not feature:
            raise ValueError("Feature not found")
        feature.set_description(args.get("description", ""))
        feature.add_history(feature.current_stage.upper(), "Description edited")
        return {"edited": "description"}

    async def _cmd_edit_title(self, args: dict) -> dict:
        feature = FeatureFile.find(args.get("feature_id", ""))
        if not feature:
            raise ValueError("Feature not found")
        feature.set_title(args.get("title", ""))
        feature.add_history(feature.current_stage.upper(), "Title edited")
        return {"edited": "title"}

    async def _cmd_edit_done_script(self, args: dict) -> dict:
        feature = FeatureFile.find(args.get("feature_id", ""))
        if not feature:
            raise ValueError("Feature not found")
        feature.set_done_script(args.get("done_script", ""))
        feature.add_history(feature.current_stage.upper(), "Done script edited")
        return {"edited": "done_script"}

    async def _cmd_edit_item_type(self, args: dict) -> dict:
        feature = FeatureFile.find(args.get("feature_id", ""))
        if not feature:
            raise ValueError("Feature not found")
        feature.set_item_type(args.get("item_type", ""))
        feature.add_history(feature.current_stage.upper(), "Item type edited")
        return {"edited": "item_type"}

    async def _cmd_edit_ideation_prompt(self, args: dict) -> dict:
        feature = FeatureFile.find(args.get("feature_id", ""))
        if not feature:
            raise ValueError("Feature not found")
        feature.set_ideation_prompt(args.get("ideation_prompt", ""))
        feature.add_history(feature.current_stage.upper(), "Ideation prompt edited")
        return {"edited": "ideation_prompt"}

    async def _cmd_edit_depends_on(self, args: dict) -> dict:
        feature = FeatureFile.find(args.get("feature_id", ""))
        if not feature:
            raise ValueError("Feature not found")
        depends_on = args.get("depends_on", [])
        if not isinstance(depends_on, list):
            raise ValueError("depends_on must be a list")
        if depends_on:
            all_features = self._load_all_features()
            from state import detect_cycle
            if detect_cycle(feature.id, depends_on, all_features):
                raise ValueError("Circular dependency detected")
        feature.set_depends_on(depends_on)
        dep_display = ", ".join(depends_on) if depends_on else "cleared"
        feature.add_history(feature.current_stage.upper(), f"Dependencies updated: {dep_display}")
        return {"edited": "depends_on"}

    async def _cmd_answer_questions(self, args: dict) -> dict:
        feature_id = args.get("feature_id", "")
        answers = args.get("answers", [])
        feature = FeatureFile.find(feature_id)
        if not feature:
            raise ValueError(f"Feature {feature_id} not found")
        if feature.current_stage != "requested-input":
            raise ValueError(f"Feature not in requested-input (is {feature.current_stage})")
        for i, a in enumerate(answers):
            ans_text = a.get("answer", "")
            if ans_text:
                feature.answer_question(i, ans_text)
        feature.add_history("PLAN-INBOX", "Questions answered")
        feature.move_to_stage("plan-inbox")
        return {"answered": True}

    async def _cmd_run_script(self, args: dict) -> dict:
        script_id = args.get("script_id", "")
        context = args.get("context", "")
        script = next((s for s in self._scripts if s.id == script_id), None)
        if not script:
            raise ValueError(f"Script '{script_id}' not found")
        # Run script in background thread
        asyncio.get_running_loop().run_in_executor(
            None, self._run_script_sync, script_id, context
        )
        return {"started": True, "script_id": script_id}

    async def _cmd_set_agent_for_phase(self, args: dict) -> dict:
        phase = args.get("phase", "")
        agent = args.get("agent", "")
        model = args.get("model", "")
        cfg = Config()
        cfg.set_agent_for_phase(phase, agent, model or None)
        logger.info(f"[server] Set agent for {phase}: agent={agent}, model={model}")
        return {"phase": phase, "agent": agent}

    async def _cmd_restart_server(self, args: dict) -> dict:
        """Restart the server process."""
        logger.info("[server] Restart requested")
        asyncio.create_task(self._shutdown())
        return {"restarting": True}

    # ── Pipeline Execution (threaded) ───────────────────────────────────

    def _run_plan_sync(self, feature: FeatureFile) -> None:
        """Run planning pipeline synchronously (called in executor thread)."""
        if not self._pipeline_lock.acquire('plan'):
            logger.info("[server] Plan skipped — lock held")
            return

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
            max_retries = 3
            retry_count = 0
            while feature.current_stage in ("plan-inbox", "requested-input") and retry_count < max_retries:
                plan_complete = run_planning(feature, runner, status=status)
                retry_count += 1
                if not plan_complete:
                    self._log_line(f"[plan] Planning failed (attempt {retry_count})")
                    break
                if feature.current_stage != "reviewing-plan":
                    break

            if feature.current_stage == "reviewing-plan":
                verdict, feedback = run_plan_review(feature, runner, status=status)
                if verdict == "PASS":
                    feature.move_to_stage("approved")
                    feature.add_history("PROMOTED", "Plan auto-approved")
                    feature.save()
                    self._log_line("=== Plan approved ===")
                else:
                    feature.add_history("PLAN_REVIEW_FAILED", f"Feedback: {feedback}")
                    feature.save()
                    feature.move_to_stage("plan-inbox")
                    self._log_line(f"=== Plan rejected: {feedback[:100]}... ===")

            self._log_line("=== Planning completed ===")
            self._auto_plan_fail_count.pop(feature.slug, None)
            self._auto_plan_fail_cooldown.pop(feature.slug, None)
        except RateLimitError as e:
            self._log_line(f"=== Rate limited: {e} ===")
            self._rate_limit_until = time.time() + 300
            self._auto_plan_fail_count[feature.slug] = self._auto_plan_fail_count.get(feature.slug, 0) + 1
            self._auto_plan_fail_cooldown[feature.slug] = time.time() + AUTO_RETRY_COOLDOWN
        except Exception as e:
            tb = traceback.format_exc()
            self._log_line(f"=== Planning failed: {e} ===")
            logger.error(f"[server] Planning error:\n{tb}")
            self._auto_plan_fail_count[feature.slug] = self._auto_plan_fail_count.get(feature.slug, 0) + 1
            self._auto_plan_fail_cooldown[feature.slug] = time.time() + AUTO_RETRY_COOLDOWN
        finally:
            self._plan_running = False
            self._auto_plan_queued.discard(feature.slug)
            status.running = False
            self._pipeline_lock.release('plan')
            status.kill_requested = False
            status.restart_requested = False

    def _run_implement_sync(self, feature: FeatureFile) -> None:
        """Run implementation pipeline synchronously (called in executor thread)."""
        if not self._pipeline_lock.acquire('implement'):
            logger.info("[server] Implement skipped — lock held")
            return

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
            from phases import (run_spec_writing, run_implementing,
                                run_verify_tests, run_review_impl)

            stage = feature.current_stage
            self._log_line(f"[impl] Starting from stage: {stage} for {feature.title}")

            if stage in ("approved", "spec-writing"):
                self._log_line(f"[impl] Running spec_writing")
                run_spec_writing(feature, runner, status=status)

            if feature.current_stage not in ("testing", "review", "final-human-approval"):
                self._log_line(f"[impl] Running implementing (stage: {feature.current_stage})")
                run_implementing(feature, runner, status=status)

            if feature.current_stage not in ("review", "final-human-approval"):
                self._log_line(f"[impl] Running verify_tests")
                run_verify_tests(feature, runner, status=status)

            if feature.current_stage not in ("final-human-approval",):
                self._log_line(f"[impl] Running review_impl")
                verdict, _ = run_review_impl(feature, runner, status=status)
                self._log_line(f"[impl] Review verdict: {verdict}")
                if verdict == "PASS":
                    feature.move_to_stage("final-human-approval")
                    feature.add_history("PROMOTED", "Implementation approved")
                    feature.save()

            self._log_line("=== Implementation completed ===")
            self._auto_implement_fail_count.pop(feature.slug, None)
            self._auto_implement_fail_cooldown.pop(feature.slug, None)
        except RateLimitError as e:
            self._log_line(f"=== Rate limited: {e} ===")
            self._rate_limit_until = time.time() + 300
            self._auto_implement_fail_count[feature.slug] = self._auto_implement_fail_count.get(feature.slug, 0) + 1
            self._auto_implement_fail_cooldown[feature.slug] = time.time() + AUTO_RETRY_COOLDOWN
        except Exception as e:
            tb = traceback.format_exc()
            self._log_line(f"=== Implementation failed: {e} ===")
            logger.error(f"[server] Implement error:\n{tb}")
            self._auto_implement_fail_count[feature.slug] = self._auto_implement_fail_count.get(feature.slug, 0) + 1
            self._auto_implement_fail_cooldown[feature.slug] = time.time() + AUTO_RETRY_COOLDOWN
        finally:
            self._implement_running = False
            self._auto_implement_queued.discard(feature.slug)
            status.running = False
            self._pipeline_lock.release('implement')
            status.kill_requested = False
            status.restart_requested = False

    def _run_ideating_sync(self, feature: FeatureFile) -> None:
        """Run ideation (debate) phase synchronously (called in executor thread)."""
        if not self._pipeline_lock.acquire('plan'):
            logger.info("[server] Ideation skipped — lock held")
            self._auto_plan_queued.discard(feature.slug)
            return

        self._plan_running = True
        runner = AgentRunner(self._config)
        status = self._plan_agent_status
        status.lines = []
        status.phase = "ideating"
        status.started_at = 0.0
        status.agent = runner.agent.name
        status.feature_slug = feature.slug
        status.running = True

        try:
            from phases import run_ideating
            run_ideating(feature, runner, status=status)
            self._log_line("=== Ideation complete ===")
            self._auto_plan_fail_count.pop(feature.slug, None)
            self._auto_plan_fail_cooldown.pop(feature.slug, None)
        except RateLimitError as e:
            self._log_line(f"=== Rate limited: {e} ===")
            self._rate_limit_until = time.time() + 300
            self._auto_plan_fail_count[feature.slug] = self._auto_plan_fail_count.get(feature.slug, 0) + 1
            self._auto_plan_fail_cooldown[feature.slug] = time.time() + AUTO_RETRY_COOLDOWN
        except Exception as e:
            tb = traceback.format_exc()
            self._log_line(f"=== Ideation failed: {e} ===")
            logger.error(f"[server] Ideation error:\n{tb}")
            self._auto_plan_fail_count[feature.slug] = self._auto_plan_fail_count.get(feature.slug, 0) + 1
            self._auto_plan_fail_cooldown[feature.slug] = time.time() + AUTO_RETRY_COOLDOWN
        finally:
            self._plan_running = False
            self._auto_plan_queued.discard(feature.slug)
            status.running = False
            self._pipeline_lock.release('plan')
            status.kill_requested = False
            status.restart_requested = False

    def _run_script_sync(self, script_id: str, context: str) -> None:
        """Run a custom script synchronously."""
        script = next((s for s in self._scripts if s.id == script_id), None)
        if not script:
            return
        try:
            run_script(script, context, self._config)
        except Exception as e:
            logger.warning(f"[server] Script '{script_id}' failed: {e}")

    # ── State Broadcasting ──────────────────────────────────────────────

    async def _broadcast_state(self) -> None:
        """Send current state to all connected clients (TUI + Golang)."""
        state_msg = self._build_state_broadcast()

        # Send to local TUI clients
        dead_clients = set()
        for client in self._local_clients:
            try:
                await client.send(state_msg)
            except Exception:
                dead_clients.add(client)
        for c in dead_clients:
            c.close()
            self._local_clients.discard(c)

        # Push to Golang
        await self._push_state_to_golang()

    def _build_state_broadcast(self) -> dict:
        """Build the state broadcast message for TUI clients."""
        features = self._load_all_features()
        from state import resolve_dependencies
        dep_statuses = {}
        for f in features:
            dep_statuses[f.id] = resolve_dependencies(f.id, features)
        return {
            "type": "state",
            "data": {
                "features": [self._feature_to_dict(f, dep_statuses.get(f.id)) for f in features],
                "plan_agent": self._agent_status_dict(self._plan_agent_status),
                "impl_agent": self._agent_status_dict(self._implement_agent_status),
                "auto_plan": self._auto_plan_enabled,
                "auto_impl": self._auto_implement_enabled,
                "scripts": [
                    {"id": s.id, "label": s.label, "description": s.description, "confirm": s.confirm}
                    for s in self._scripts
                ],
                "script_status": self._script_status_dict(),
                "logs": self._log_buffer[-100:],
            },
        }

    def _build_golang_state_message(self) -> dict:
        """Build state message in the format the Golang server expects.

        Matches the old server_client.push_state() format exactly.
        """
        features = self._load_all_features()
        feature_summaries = []
        log_entries = []

        from state import resolve_dependencies
        dep_statuses = {}
        for f in features:
            dep_statuses[f.id] = resolve_dependencies(f.id, features)

        for f in features:
            available = []
            for action, allowed_stages in STAGE_ACTIONS.items():
                if f.current_stage in allowed_stages:
                    if action == "plan" and self._plan_running:
                        continue
                    if action == "implement" and self._implement_running:
                        continue
                    available.append(action)
            feature_summaries.append({
                "title": f.title,
                "stage": f.current_stage,
                "board": f.board,
                "created": f.created,
                "id": f.id,
                "slug": f.slug,
                "description": f.description or "",
                "history": f._data.get("history", []),
                "questions": f.questions,
                "available_actions": available,
                "plan": f.plan or "",
                "plan_exploration_summary": f.plan_exploration_summary or "",
                "impl_spec": f.impl_spec or "",
                "test_spec": f.test_spec or "",
                "impl_notes": f.impl_notes or "",
                "plan_reviews": f.plan_reviews,
                "impl_reviews": f.impl_reviews,
                "test_results": f.test_results,
                "checkpoint": _get_checkpoint_info(f),
                "done_script": f.done_script or "",
                "item_type": f.item_type,
                "ideation": f.Ideation or "",
                "ideation_prompt": f.ideation_prompt,
                "ideation_summaries": f.ideation_summaries,
                "requires_human_approval": f.requires_human_approval,
                "depends_on": f.depends_on,
                "blocked": dep_statuses[f.id]['blocked'],
                "unresolved_deps": dep_statuses[f.id]['unresolved_deps'],
                "dangling_deps": dep_statuses[f.id]['dangling_deps'],
            })
            raw_logs = f._data.get("pipeline_log", [])
            for entry in raw_logs[-10:]:
                log_entries.append({
                    "timestamp": entry.get("ts", ""),
                    "phase": entry.get("phase", ""),
                    "output": entry.get("output", "")[:500],
                    "client_id": self._golang_client_id,
                })

        msg = {
            "type": "state_update",
            "client_id": self._golang_client_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "features": feature_summaries,
            "logs": log_entries,
            "stage_actions": STAGE_ACTIONS,
            "plan_agent": self._agent_status_dict(self._plan_agent_status),
            "impl_agent": self._agent_status_dict(self._implement_agent_status),
            "auto_plan": self._auto_plan_enabled,
            "auto_impl": self._auto_implement_enabled,
        }

        try:
            _config = Config()
            _afp = _config.agent_for_phase
            msg["config"] = {
                "default_agent": _config.current_agent_name,
                "agent_for_phase": {
                    pk: {"agent": pc.agent, "model": pc.model or ""}
                    for pk, pc in _afp.items()
                },
                "available_agents": list(BUILTIN_AGENTS.keys()),
                "phases": [{"key": k, "label": l} for k, l in PIPELINE_PHASES],
            }
        except Exception as e:
            logger.warning(f"[server] Failed to build config for Golang push: {e}")

        if self._scripts:
            msg["scripts"] = [
                {"id": s.id, "label": s.label, "description": s.description, "confirm": s.confirm}
                for s in self._scripts
            ]
        if self._script_status and self._script_status.running:
            lines = self._script_status.lines
            if hasattr(self._script_status, '_agent_status') and self._script_status._agent_status:
                lines = self._script_status._agent_status.lines
            msg["script_status"] = {
                "script_id": self._script_status.script_id,
                "running": self._script_status.running,
                "lines": lines[-50:] if lines else [],
                "started_at": self._script_status.started_at or "",
                "finished_at": self._script_status.finished_at or "",
                "exit_code": self._script_status.exit_code,
            }

        return msg

    # ── Periodic Tasks ──────────────────────────────────────────────────

    async def _periodic_broadcast(self) -> None:
        """Broadcast state to ALL clients (TUI + Golang) every 2 seconds.

        This ensures TUI sees real-time agent progress (streaming lines,
        phase changes) even though those happen in executor threads that
        don't trigger explicit broadcasts.
        """
        while not self._shutting_down:
            await asyncio.sleep(2.0)
            if self._local_clients or self._golang_ws:
                await self._broadcast_state()

    async def _auto_run_loop(self) -> None:
        """Check for features that should be auto-run every 10 seconds."""
        while not self._shutting_down:
            await asyncio.sleep(10.0)
            try:
                self._auto_run_check()
            except Exception as e:
                logger.warning(f"[server] Auto-run check error: {e}")

    def _auto_run_check(self) -> None:
        """Check for features eligible for auto-plan or auto-implement.

        Auto-plan stages: ideating, plan-inbox, reviewing-plan
        Auto-implement stages: review, testing, implementing, spec-writing, approved
        (checked in reverse pipeline order to resume from where we left off)
        """
        now = time.time()
        if now < self._rate_limit_until:
            return

        features = self._load_all_features()

        if self._auto_plan_enabled and not self._plan_running:
            # Check ideating, plan-inbox, reviewing-plan
            plan_stages = ("ideating", "plan-inbox", "reviewing-plan")
            for f in features:
                if f.current_stage not in plan_stages:
                    continue
                if f.slug in self._auto_plan_queued:
                    continue
                if self._auto_plan_fail_count.get(f.slug, 0) >= MAX_AUTO_RETRIES:
                    continue
                cooldown = self._auto_plan_fail_cooldown.get(f.slug, 0)
                if now < cooldown:
                    continue
                self._auto_plan_queued.add(f.slug)
                # Route to ideation or planning based on stage
                if f.current_stage == "ideating":
                    asyncio.get_event_loop().run_in_executor(None, self._run_ideating_sync, f)
                else:
                    asyncio.get_event_loop().run_in_executor(None, self._run_plan_sync, f)
                break  # One at a time

        if self._auto_implement_enabled and not self._implement_running:
            # Check in reverse pipeline order to pick up where we left off
            impl_stages = ("review", "testing", "implementing", "spec-writing", "approved")
            for f in features:
                if f.current_stage not in impl_stages:
                    continue
                if f.slug in self._auto_implement_queued:
                    continue
                if self._auto_implement_fail_count.get(f.slug, 0) >= MAX_AUTO_RETRIES:
                    continue
                cooldown = self._auto_implement_fail_cooldown.get(f.slug, 0)
                if now < cooldown:
                    continue
                from state import resolve_dependencies
                dep_status = resolve_dependencies(f.id, features)
                if dep_status['blocked']:
                    if dep_status['dangling_deps']:
                        logger.info(f"[server] Auto-impl skip {f.slug}: dangling deps {dep_status['dangling_deps']}")
                    else:
                        unresolved_titles = [d['title'] for d in dep_status['unresolved_deps']]
                        logger.info(f"[server] Auto-impl skip {f.slug}: blocked by {unresolved_titles}")
                    continue
                self._auto_implement_queued.add(f.slug)
                asyncio.get_event_loop().run_in_executor(None, self._run_implement_sync, f)
                break

    # ── Helpers ──────────────────────────────────────────────────────────

    def _load_all_features(self) -> list[FeatureFile]:
        """Load features across all boards."""
        all_features = []
        for board in self._config.boards:
            all_features.extend(FeatureFile.list_all(board=board))
        return all_features

    def _feature_to_dict(self, f: FeatureFile, dep_status: dict = None) -> dict:
        """Convert a FeatureFile to a serializable dict for TUI clients."""
        available = []
        for action, allowed_stages in STAGE_ACTIONS.items():
            if f.current_stage in allowed_stages:
                available.append(action)
        if dep_status is None:
            dep_status = {'blocked': False, 'unresolved_deps': [], 'dangling_deps': []}
        return {
            "title": f.title,
            "stage": f.current_stage,
            "board": f.board,
            "created": f.created,
            "id": f.id,
            "slug": f.slug,
            "description": f.description or "",
            "history": f._data.get("history", []),
            "questions": f.questions,
            "available_actions": available,
            "plan": f.plan or "",
            "plan_exploration_summary": f.plan_exploration_summary or "",
            "impl_spec": f.impl_spec or "",
            "test_spec": f.test_spec or "",
            "impl_notes": f.impl_notes or "",
            "plan_reviews": f.plan_reviews,
            "impl_reviews": f.impl_reviews,
            "test_results": f.test_results,
            "checkpoint": _get_checkpoint_info(f),
            "done_script": f.done_script or "",
            "item_type": f.item_type,
            "ideation": f.Ideation or "",
            "ideation_prompt": f.ideation_prompt,
            "ideation_summaries": f.ideation_summaries,
            "requires_human_approval": f.requires_human_approval,
            "depends_on": f.depends_on,
            "blocked": dep_status['blocked'],
            "unresolved_deps": dep_status['unresolved_deps'],
            "dangling_deps": dep_status['dangling_deps'],
        }

    def _agent_status_dict(self, status: AgentStatus) -> dict:
        if not status:
            return {"running": False, "phase": "", "feature": "", "agent": ""}
        return {
            "running": status.running,
            "phase": status.phase,
            "feature": status.feature_slug,
            "agent": status.agent,
        }

    def _script_status_dict(self) -> Optional[dict]:
        if not self._script_status or not self._script_status.running:
            return None
        lines = self._script_status.lines
        if hasattr(self._script_status, '_agent_status') and self._script_status._agent_status:
            lines = self._script_status._agent_status.lines
        return {
            "script_id": self._script_status.script_id,
            "running": self._script_status.running,
            "lines": lines[-50:] if lines else [],
            "started_at": self._script_status.started_at or "",
            "finished_at": self._script_status.finished_at or "",
            "exit_code": self._script_status.exit_code,
        }

    def _log_line(self, line: str) -> None:
        """Add a line to the log buffer."""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {line}"
        self._log_buffer.append(entry)
        # Cap buffer
        if len(self._log_buffer) > 1000:
            self._log_buffer = self._log_buffer[-500:]
        logger.info(line)
