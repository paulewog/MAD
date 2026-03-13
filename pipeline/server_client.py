"""WebSocket client for pushing pipeline state to the MAD monitoring server."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from config import Config, BUILTIN_AGENTS, PIPELINE_PHASES
from state import STAGE_ACTIONS

logger = logging.getLogger(__name__)


def _get_checkpoint_info(feature) -> Optional[dict]:
    """Read checkpoint file for a feature and return info, or None if not found."""
    try:
        config = Config()
        mad_dir = config.mad_dir
        checkpoint_path = mad_dir / "checkpoints" / f"{feature.slug}.checkpoint.json"
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
        logger.warning(f"[server_client] Failed to read checkpoint for {feature.slug}: {e}")
        return None


class ServerClient:
    """Async WebSocket client that pushes feature state to the MAD server."""

    def __init__(self, url: str, api_key: str, client_id: str, on_connect=None,
                 on_answers_received=None,
                 on_set_auto_mode: Optional[Callable[[str, bool], None]] = None,
                 on_start_agent: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 on_idea_created: Optional[Callable[[str, str, str, str, str], Awaitable[None]]] = None,
                 on_move_requested: Optional[Callable[[str, str, str, str], Awaitable[None]]] = None,
                 on_edit_description: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 on_edit_done_script: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 on_edit_title: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 on_edit_item_type: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 on_run_script: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 on_set_agent_for_phase: Optional[Callable[[str, str, str], Awaitable[None]]] = None):
        self._url = url
        self._api_key = api_key
        self._client_id = client_id
        self._ws = None
        self._connected = False
        self._backoff = 1.0
        self._max_backoff = 30.0
        self._on_connect = on_connect
        self._on_answers_received = on_answers_received
        self._on_set_auto_mode = on_set_auto_mode
        self._on_start_agent = on_start_agent
        self._on_idea_created = on_idea_created
        self._on_move_requested = on_move_requested
        self._on_edit_description = on_edit_description
        self._on_edit_done_script = on_edit_done_script
        self._on_edit_title = on_edit_title
        self._on_edit_item_type = on_edit_item_type
        self._on_run_script = on_run_script
        self._on_set_agent_for_phase = on_set_agent_for_phase

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """Connect to the server and register."""
        if not HAS_WEBSOCKETS:
            return False
        try:
            # Build WebSocket URL with ws endpoint
            ws_url = self._url.rstrip("/") + "/ws"
            self._ws = await websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                max_size=10 * 1024 * 1024,  # 10MB
            )
            # Send register message
            await self._ws.send(json.dumps({
                "type": "register",
                "client_id": self._client_id,
                "api_key": self._api_key,
            }))
            # Wait for ack
            response = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            data = json.loads(response)
            if data.get("type") == "ack":
                self._connected = True
                self._backoff = 1.0  # Reset backoff on success
                logger.info(f"Connected to server as '{self._client_id}'")
                return True
            else:
                logger.warning(f"Server rejected registration: {data}")
                await self._ws.close()
                self._ws = None
                return False
        except Exception as e:
            logger.warning(f"Failed to connect to server: {e}")
            self._connected = False
            if self._ws:
                try:
                    await self._ws.close()
                except Exception as e:
                    logger.debug(f"Failed to close WebSocket during connection error cleanup: {e}")
                self._ws = None
            return False

    async def push_state(self, features: list, plan_status=None, impl_status=None,
                         auto_plan_enabled: bool = False, auto_impl_enabled: bool = False,
                         scripts=None, script_status=None) -> None:
        """Push current feature state to the server.

        Args:
            features: List of FeatureFile objects
            plan_status: Optional AgentStatus for the plan agent
            impl_status: Optional AgentStatus for the implement agent
            auto_plan_enabled: Whether auto-plan mode is enabled
            auto_impl_enabled: Whether auto-impl mode is enabled
            scripts: Optional list of ScriptConfig objects
            script_status: Optional ScriptStatus object for running script
        """
        if not self._connected or not self._ws:
            return
        try:
            feature_summaries = []
            log_entries = []
            for f in features:
                available = []
                for action, allowed_stages in STAGE_ACTIONS.items():
                    if f.current_stage in allowed_stages:
                        if action == "plan" and plan_status and plan_status.running:
                            continue
                        if action == "implement" and impl_status and impl_status.running:
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
                    "ideation_summaries": f.ideation_summaries,
                })
                # Collect log entries from the feature
                raw_logs = f._data.get("pipeline_log", [])
                for entry in raw_logs[-10:]:  # Only send last 10 per feature
                    log_entries.append({
                        "timestamp": entry.get("ts", ""),
                        "phase": entry.get("phase", ""),
                        "output": entry.get("output", "")[:500],  # Truncate long outputs
                        "client_id": self._client_id,
                    })

            msg = {
                "type": "state_update",
                "client_id": self._client_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "features": feature_summaries,
                "logs": log_entries,
            }

            def _agent_dict(status):
                if status is None:
                    return {"running": False, "phase": "", "feature": "", "agent": ""}
                return {
                    "running": status.running,
                    "phase": status.phase,
                    "feature": status.feature_slug,
                    "agent": status.agent,
                }

            msg["stage_actions"] = STAGE_ACTIONS
            msg["plan_agent"] = _agent_dict(plan_status)
            msg["impl_agent"] = _agent_dict(impl_status)
            msg["auto_plan"] = auto_plan_enabled
            msg["auto_impl"] = auto_impl_enabled

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
                    "phases": [{"key": k, "label": l} for k, l in PIPELINE_PHASES]
                }
            except Exception as e:
                logger.warning(f"Failed to prepare config data for server push: {e}")

            if scripts:
                msg["scripts"] = [
                    {"id": s.id, "label": s.label, "description": s.description, "confirm": s.confirm}
                    for s in scripts
                ]
            if script_status and script_status.running:
                lines = script_status.lines
                if hasattr(script_status, '_agent_status') and script_status._agent_status:
                    lines = script_status._agent_status.lines
                msg["script_status"] = {
                    "script_id": script_status.script_id,
                    "running": script_status.running,
                    "lines": lines[-50:] if lines else [],
                    "started_at": script_status.started_at or "",
                    "finished_at": script_status.finished_at or "",
                    "exit_code": script_status.exit_code,
                }

            message = json.dumps(msg)
            await self._ws.send(message)
        except Exception as e:
            logger.warning(f"Failed to push state: {e}")
            self._connected = False

    async def disconnect(self) -> None:
        """Send disconnect message and close the WebSocket."""
        if self._ws:
            try:
                if self._connected:
                    await self._ws.send(json.dumps({
                        "type": "disconnect",
                        "client_id": self._client_id,
                    }))
                await self._ws.close()
            except Exception as e:
                logger.debug(f"Error during WebSocket disconnect: {e}")
            finally:
                self._ws = None
                self._connected = False

    async def _send_move_result(self, request_id: str, success: bool, error: str = "") -> None:
        """Send move result back to server."""
        if not self._connected or not self._ws:
            return
        msg = {"type": "move_result", "request_id": request_id, "success": success}
        if error:
            msg["error"] = error
        try:
            await self._ws.send(json.dumps(msg))
        except Exception as e:
            logger.warning(f"Failed to send move_result: {e}")

    async def send_move_result(self, request_id: str, success: bool, error: str = "") -> None:
        """Send move result back to server (public API)."""
        await self._send_move_result(request_id, success, error)

    async def _handle_incoming(self, raw: str) -> None:
        """Dispatch an incoming server message by type."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Received non-JSON message from server")
            return
        msg_type = data.get("type")
        if msg_type == "answer_questions":
            feature_id = data.get("feature_id", "")
            answers = data.get("answers", [])
            if self._on_answers_received and feature_id:
                try:
                    await self._on_answers_received(feature_id, answers)
                except Exception as e:
                    logger.warning(f"Error handling answer_questions: {e}")
        elif msg_type == "set_auto_mode":
            mode = data.get("mode")
            enabled = data.get("enabled")
            if mode in ("plan", "impl") and isinstance(enabled, bool):
                if self._on_set_auto_mode:
                    try:
                        self._on_set_auto_mode(mode, enabled)
                    except Exception as e:
                        logger.warning(f"Error handling set_auto_mode: {e}")
        elif msg_type == "start_agent":
            feature_id = data.get("feature_id", "")
            action = data.get("action", "")
            if self._on_start_agent and feature_id and action in ("plan", "implement"):
                try:
                    await self._on_start_agent(feature_id, action)
                except Exception as e:
                    logger.warning(f"Error handling start_agent: {e}")
        elif msg_type == "create_idea":
            title = data.get("title", "")
            board = data.get("board", "")
            description = data.get("description", "")
            item_type = data.get("item_type", "feature")
            done_script = data.get("done_script", "")
            if self._on_idea_created and title and board:
                try:
                    await self._on_idea_created(title, board, description, item_type, done_script)
                except Exception as e:
                    logger.warning(f"Error handling create_idea: {e}")
        elif msg_type == "move_feature":
            feature_id = data.get("feature_id", "")
            target_stage = data.get("target_stage", "")
            request_id = data.get("request_id", "")
            reason = data.get("reason", "")
            if self._on_move_requested and feature_id and target_stage:
                try:
                    await self._on_move_requested(feature_id, target_stage, request_id, reason)
                except Exception as e:
                    logger.warning(f"Error handling move_feature: {e}")
                    if request_id:
                        await self._send_move_result(request_id, False, str(e))
        elif msg_type == "edit_description":
            feature_id = data.get("feature_id", "")
            description = data.get("description", "")
            if self._on_edit_description and feature_id:
                try:
                    await self._on_edit_description(feature_id, description)
                except Exception as e:
                    logger.warning(f"Error handling edit_description: {e}")
        elif msg_type == "edit_done_script":
            feature_id = data.get("feature_id", "")
            done_script = data.get("done_script", "")
            if self._on_edit_done_script and feature_id:
                try:
                    await self._on_edit_done_script(feature_id, done_script)
                except Exception as e:
                    logger.warning(f"Error handling edit_done_script: {e}")
        elif msg_type == "edit_title":
            feature_id = data.get("feature_id", "")
            title = data.get("title", "")
            if self._on_edit_title and feature_id:
                try:
                    await self._on_edit_title(feature_id, title)
                except Exception as e:
                    logger.warning(f"Error handling edit_title: {e}")
        elif msg_type == "edit_item_type":
            feature_id = data.get("feature_id", "")
            item_type = data.get("item_type", "")
            if self._on_edit_item_type and feature_id:
                try:
                    await self._on_edit_item_type(feature_id, item_type)
                except Exception as e:
                    logger.warning(f"Error handling edit_item_type: {e}")
        elif msg_type == "run_script":
            script_id = data.get("script_id", "")
            context = data.get("context", "")
            if self._on_run_script and script_id:
                try:
                    await self._on_run_script(script_id, context)
                except Exception as e:
                    logger.warning(f"Error handling run_script: {e}")
        elif msg_type == "set_agent_for_phase":
            phase = data.get("phase", "")
            agent = data.get("agent", "")
            model = data.get("model", "")
            if self._on_set_agent_for_phase and phase and agent:
                try:
                    await self._on_set_agent_for_phase(phase, agent, model)
                except Exception as e:
                    logger.warning(f"Error handling set_agent_for_phase: {e}")
        # Other message types silently ignored

    async def reconnect_loop(self) -> None:
        """Continuously attempt to connect and stay connected.

        This runs forever as a background task. On disconnect,
        retries with exponential backoff (1s -> 2s -> 4s -> ... -> 30s cap).
        """
        if not HAS_WEBSOCKETS:
            logger.warning("websockets not installed, server push disabled")
            return

        while True:
            try:
                connected = await self.connect()
                if connected:
                    if self._on_connect:
                        await self._on_connect()
                    # Stay connected, dispatch incoming server messages
                    try:
                        async for message in self._ws:
                            await self._handle_incoming(message)
                    except Exception:
                        pass
                    # Connection dropped
                    self._connected = False
                    self._ws = None
                    logger.info("Server connection lost, will reconnect...")
                    self._backoff = 1.0  # Reset on clean disconnect

                # Wait before reconnecting
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, self._max_backoff)
            except asyncio.CancelledError:
                # Clean shutdown
                await self.disconnect()
                return
            except Exception as e:
                logger.warning(f"Reconnect error: {e}")
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, self._max_backoff)
