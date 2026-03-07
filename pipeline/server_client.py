"""WebSocket client for pushing pipeline state to the MAD monitoring server."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from state import STAGE_ACTIONS

logger = logging.getLogger(__name__)


def _get_checkpoint_info(feature) -> Optional[dict]:
    """Read checkpoint file for a feature and return info, or None if not found."""
    try:
        config_path = Path(__file__).parent.parent / ".mad" / "config.json"
        if not config_path.exists():
            return None
        with open(config_path) as f:
            config = json.load(f)
        mad_dir = Path(config.get("mad_dir", ".mad"))
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
                 on_start_agent: Optional[Callable[[str, str], None]] = None,
                 on_idea_created: Optional[Callable[[str, str, str], None]] = None,
                 on_move_requested: Optional[Callable[[str, str], None]] = None):
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
                except Exception:
                    pass
                self._ws = None
            return False

    async def push_state(self, features: list, plan_status=None, impl_status=None,
                         auto_plan_enabled: bool = False, auto_impl_enabled: bool = False) -> None:
        """Push current feature state to the server.

        Args:
            features: List of FeatureFile objects
            plan_status: Optional AgentStatus for the plan agent
            impl_status: Optional AgentStatus for the implement agent
            auto_plan_enabled: Whether auto-plan mode is enabled
            auto_impl_enabled: Whether auto-impl mode is enabled
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
                    "description": f.description[:1000] if f.description else "",
                    "history": f._data.get("history", []),
                    "questions": f.questions,
                    "available_actions": available,
                    "plan": f.plan[:1000] if f.plan else "",
                    "impl_spec": f.impl_spec[:1000] if f.impl_spec else "",
                    "test_spec": f.test_spec[:1000] if f.test_spec else "",
                    "impl_notes": f.impl_notes[:1000] if f.impl_notes else "",
                    "checkpoint": _get_checkpoint_info(f),
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
            except Exception:
                pass
            finally:
                self._ws = None
                self._connected = False

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
                    self._on_start_agent(feature_id, action)
                except Exception as e:
                    logger.warning(f"Error handling start_agent: {e}")
        elif msg_type == "create_idea":
            title = data.get("title", "")
            board = data.get("board", "")
            description = data.get("description", "")
            if self._on_idea_created and title and board:
                try:
                    self._on_idea_created(title, board, description)
                except Exception as e:
                    logger.warning(f"Error handling create_idea: {e}")
        elif msg_type == "move_feature":
            feature_id = data.get("feature_id", "")
            target_stage = data.get("target_stage", "")
            if self._on_move_requested and feature_id and target_stage:
                try:
                    self._on_move_requested(feature_id, target_stage)
                except Exception as e:
                    logger.warning(f"Error handling move_feature: {e}")
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
