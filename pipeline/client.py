"""Async client for connecting to PipelineServer via Unix socket.

Uses JsonFramer (NDJSON) for all socket I/O — same framing as the server.
Supports request/response correlation via message IDs.
"""

import asyncio
import json
import logging
from typing import Callable, Optional

from config import socket_path_for_project
from framing import JsonFramer

logger = logging.getLogger("pipeline")


class PipelineClient:
    """Async client for connecting to PipelineServer via Unix socket."""

    def __init__(self, socket_path: str = None):
        self._socket_path = socket_path or socket_path_for_project()
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._state_callbacks: list[Callable] = []
        self._log_callbacks: list[Callable] = []
        self._pending: dict[str, asyncio.Future] = {}  # id -> Future
        self._next_id = 0
        self._recv_task: Optional[asyncio.Task] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """Connect to server via Unix socket."""
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                self._socket_path
            )
            self._connected = True
            self._recv_task = asyncio.create_task(self._recv_loop())
            logger.info(f"[client] Connected to {self._socket_path}")
            return True
        except Exception as e:
            logger.warning(f"[client] Failed to connect: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Close connection."""
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        # Fail all pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("Disconnected"))
        self._pending.clear()

    async def reconnect_loop(self, backoff_start: float = 1.0,
                              backoff_max: float = 30.0) -> None:
        """Continuously attempt to connect, with exponential backoff."""
        backoff = backoff_start
        while True:
            if not self._connected:
                ok = await self.connect()
                if ok:
                    backoff = backoff_start
                    # Wait for recv_loop to end (connection dropped)
                    if self._recv_task:
                        try:
                            await self._recv_task
                        except asyncio.CancelledError:
                            return
                        except Exception:
                            pass
                    self._connected = False
                    logger.info("[client] Connection lost, reconnecting...")
                    backoff = backoff_start
                else:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, backoff_max)
            else:
                await asyncio.sleep(1.0)

    async def _recv_loop(self) -> None:
        """Read messages from server, dispatch responses vs broadcasts."""
        try:
            while self._connected and self._reader:
                msg = await JsonFramer.read_message(self._reader)
                if "id" in msg:
                    # Response to a command — resolve the matching Future
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif msg.get("type") == "state":
                    for cb in self._state_callbacks:
                        try:
                            cb(msg.get("data", {}))
                        except Exception as e:
                            logger.warning(f"[client] State callback error: {e}")
                elif msg.get("type") == "log":
                    for cb in self._log_callbacks:
                        try:
                            cb(msg.get("entries", []))
                        except Exception as e:
                            logger.warning(f"[client] Log callback error: {e}")
        except ConnectionError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[client] Recv loop error: {e}")
        finally:
            self._connected = False

    async def send_command(self, cmd: str, **kwargs) -> dict:
        """Send a command with a unique request ID, wait for matching response.

        Raises ConnectionError if not connected.
        Raises TimeoutError if no response within 60 seconds.
        """
        if not self._connected or not self._writer:
            raise ConnectionError("Not connected to server")

        req_id = str(self._next_id)
        self._next_id += 1

        msg = {"id": req_id, "cmd": cmd, "args": kwargs}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        try:
            await JsonFramer.write_message(self._writer, msg)
            return await asyncio.wait_for(fut, timeout=60.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"Command '{cmd}' timed out")

    def on_state_update(self, callback: Callable[[dict], None]) -> None:
        """Register callback for state broadcasts."""
        self._state_callbacks.append(callback)

    def on_log_update(self, callback: Callable[[list], None]) -> None:
        """Register callback for log broadcasts."""
        self._log_callbacks.append(callback)

    # ── Convenience methods ─────────────────────────────────────────────

    async def get_state(self) -> dict:
        return await self.send_command("get_state")

    async def move_feature(self, feature_id: str, target_stage: str,
                           reason: str = "") -> dict:
        return await self.send_command("move_feature", feature_id=feature_id,
                                       target_stage=target_stage, reason=reason)

    async def create_idea(self, title: str, board: str, description: str = "",
                          item_type: str = "feature", done_script: str = "",
                          requires_human_approval: bool = False) -> dict:
        return await self.send_command("create_idea", title=title, board=board,
                                       description=description, item_type=item_type,
                                       done_script=done_script,
                                       requires_human_approval=requires_human_approval)

    async def start_agent(self, feature_id: str, action: str) -> dict:
        return await self.send_command("start_agent", feature_id=feature_id,
                                       action=action)

    async def stop_agent(self, phase: str) -> dict:
        return await self.send_command("stop_agent", phase=phase)

    async def set_auto_mode(self, mode: str, enabled: bool) -> dict:
        return await self.send_command("set_auto_mode", mode=mode, enabled=enabled)

    async def edit_description(self, feature_id: str, description: str) -> dict:
        return await self.send_command("edit_description", feature_id=feature_id,
                                       description=description)

    async def edit_title(self, feature_id: str, title: str) -> dict:
        return await self.send_command("edit_title", feature_id=feature_id,
                                       title=title)

    async def edit_done_script(self, feature_id: str, done_script: str) -> dict:
        return await self.send_command("edit_done_script", feature_id=feature_id,
                                       done_script=done_script)

    async def edit_item_type(self, feature_id: str, item_type: str) -> dict:
        return await self.send_command("edit_item_type", feature_id=feature_id,
                                       item_type=item_type)

    async def edit_ideation_prompt(self, feature_id: str, ideation_prompt: str) -> dict:
        return await self.send_command("edit_ideation_prompt", feature_id=feature_id,
                                       ideation_prompt=ideation_prompt)

    async def answer_questions(self, feature_id: str, answers: list) -> dict:
        return await self.send_command("answer_questions", feature_id=feature_id,
                                       answers=answers)

    async def run_script(self, script_id: str, context: str = "") -> dict:
        return await self.send_command("run_script", script_id=script_id,
                                       context=context)

    async def set_agent_for_phase(self, phase: str, agent: str,
                                   model: str = "") -> dict:
        return await self.send_command("set_agent_for_phase", phase=phase,
                                       agent=agent, model=model)
