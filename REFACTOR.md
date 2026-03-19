# Server/Client Refactor Plan for MAD Pipeline

## Executive Summary

The current architecture couples the TUI (Textual UI) directly to the Python process, causing problems when users detach/reattach from tmux. This refactor separates the Python process into a headless server, allowing multiple clients (TUI, WebUI) to connect as peers.

## Current Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  TUI (Python/Textual)                                                  │
│  - Manages state (features, agents, boards)                           │
│  - Runs in tmux (started by systemctl)                                │
│  - Connects to golang server via WebSocket                           │
│  - BROADCASTs state to golang server                                │
└─────────────────────────────────────────────────────────────────────────┘
         │                                                      ▲
         │ WebSocket                                           │ WebSocket
         ▼                                                      │
┌─────────────────────────────────────────────────────────────────────────┐
│  Golang Server (hub.go)                                               │
│  - Maintains client connections                                       │
│  - Forwards messages between clients                                 │
│  - WebUI connects here too                                           │
└─────────────────────────────────────────────────────────────────────────┘
         │
         │ HTTP/WebSocket
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Web Browser (WebUI)                                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

**Problem**: The python process IS the TUI. When TUI exits/detaches, the python process dies, and webui loses connection to the pipeline.

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Python Server (headless)                                              │
│  - Manages ALL state (features, boards, agents, scripts)              │
│  - Runs in tmux (started by systemctl) - NEVER exits                  │
│  - Listens on Unix socket for local clients (TUI)                    │
│  - Maintains outbound WebSocket to Golang server (remote)            │
│  - Pushes state to Golang; receives WebUI commands back              │
└─────────────────────────────────────────────────────────────────────────┘
         │                                    │
         │ Unix Socket                       │ WebSocket (bidirectional)
         │ (local)                           │ (remote)
         ▼                                    ▼
┌──────────────────────┐          ┌──────────────────────────┐
│  TUI Client         │          │  Golang Server (remote)  │
│  - Thin UI renderer │          │  - Hosted separately     │
│  - Connects to socket│          │  - Serves WebUI to       │
│  - Sends commands    │          │    browsers              │
│  - Receives updates │          │  - Forwards browser cmds │
└──────────────────────┘          │    back to Python via WS │
                                  └──────────────────────────┘
                                             │
                                             │ HTTP/WebSocket
                                             ▼
                                    ┌──────────────────┐
                                    │  Web Browser     │
                                    │  (WebUI)         │
                                    └──────────────────┘
```

### Data Flow

```
State updates:  Python Server ──WS──► Golang Server ──WS──► Browser
                Python Server ──Unix──► TUI Client

Commands:       Browser ──WS──► Golang Server ──WS──► Python Server
                TUI Client ──Unix──► Python Server
```

The Python server is the single source of truth. It pushes state outbound
on two channels (Unix socket for TUI, WebSocket for Golang) and receives
commands inbound on those same channels. The Golang server is a relay —
it does not own state.

## Implementation Plan

### Phase 1: Extract Python Server (Core)

#### 1.1 Create `server.py` - Headless Server

New file: `/home/paulewog/MAD/pipeline/server.py`

**Responsibilities:**
- Manage ALL state (features, boards, agents, scripts)
- Listen for client connections (Unix socket)
- Handle commands from clients
- Broadcast state changes to all connected clients

**Key Classes/Functions:**

```python
class PipelineServer:
    """Headless server that manages pipeline state."""

    def __init__(self, socket_path: str = "/tmp/pipeline.sock",
                 golang_ws_url: str = "ws://..."):
        """Initialize server with socket path and remote Golang WS URL."""
        self._socket_path = socket_path
        self._golang_ws_url = golang_ws_url
        self._local_clients: set = set()   # TUI clients via Unix socket
        self._golang_ws = None             # WebSocket to remote Golang server

        # STATE - moved from tui.py
        self._features: list[FeatureFile] = []
        self._boards: dict[str, list] = {}
        self._plan_agent_status = AgentStatus()
        self._implement_agent_status = AgentStatus()
        self._auto_plan_enabled = False
        self._auto_implement_enabled = False
        self._scripts: list[ScriptConfig] = []
        self._script_status: Optional[ScriptStatus] = None

    async def start(self):
        """Start all connections and begin serving."""
        # 1. Connect to remote Golang server via WebSocket
        await self._connect_golang()
        # 2. Start Unix socket server for local TUI clients
        await self._start_local_server()
        # 3. Listen for commands from both channels

    async def _connect_golang(self):
        """Maintain persistent bidirectional WebSocket to Golang server."""
        # Connect to remote Golang server
        # Start listener task for incoming WebUI commands
        # Auto-reconnect on disconnect

    async def _on_golang_message(self, message: dict):
        """Handle command received from Golang (originated from WebUI)."""
        # Dispatch to appropriate command handler
        # Same handlers used for TUI and WebUI commands

    async def handle_local_client(self, reader, writer):
        """Handle individual TUI client connection via Unix socket."""
        # Register client
        # Handle commands loop
        # Broadcast state on changes

    def broadcast(self, message: dict):
        """Send state update to ALL connected clients (TUI + Golang)."""
        # Send to local TUI clients via Unix socket
        for client in self._local_clients:
            client.send(message)
        # Send to Golang server via WebSocket (relayed to browsers)
        if self._golang_ws:
            self._golang_ws.send(message)
            
    # COMMAND HANDLERS
    async def handle_get_state(self) -> dict:
        """Return full state to client."""
        
    async def handle_move_feature(self, feature_slug: str, from_stage: str, 
                                   to_stage: str, board: str) -> dict:
        """Move feature to different stage."""
        
    async def handle_create_feature(self, title: str, board: str, 
                                     description: str = "") -> dict:
        """Create new feature."""
        
    async def handle_start_agent(self, phase: str, feature_slug: str) -> dict:
        """Start agent for feature."""
        
    async def handle_stop_agent(self, phase: str) -> dict:
        """Stop running agent."""
        
    async def handle_set_auto_mode(self, mode: str, enabled: bool) -> dict:
        """Toggle auto-plan or auto-impl mode."""
        
    async def handle_edit_feature(self, feature_slug: str, field: str, 
                                   value: str) -> dict:
        """Edit feature field."""
        
    async def handle_run_script(self, script_id: str, feature_slug: str) -> dict:
        """Run script on feature."""
        
    async def handle_answer_questions(self, feature_slug: str, 
                                       answers: dict) -> dict:
        """Submit answers to feature questions."""
```

**Protocol (JSON over Unix socket):**

Client → Server:
```json
{"cmd": "move_feature", "args": {"feature_slug": "...", "from_stage": "...", "to_stage": "...", "board": "..."}}
```

Server → Client:
```json
{"type": "state", "data": {...}}
{"type": "error", "message": "..."}
{"type": "log", "entries": [...]}
```

#### 1.2 Create Client Library

New file: `/home/paulewog/MAD/pipeline/client.py`

**Responsibilities:**
- Connect to python server via Unix socket
- Send commands
- Receive state updates
- Expose async API for TUI and golang

```python
class PipelineClient:
    """Async client for connecting to PipelineServer."""
    
    def __init__(self, socket_path: str = "/tmp/pipeline.sock"):
        self._socket_path = socket_path
        self._reader = None
        self._writer = None
        self._state_callbacks: list[Callable] = []
        self._log_callbacks: list[Callable] = []
        
    async def connect(self) -> bool:
        """Connect to server."""
        
    async def disconnect(self):
        """Close connection."""
        
    async def send_command(self, cmd: str, **kwargs) -> dict:
        """Send command to server, wait for response."""
        
    def on_state_update(self, callback: Callable[[dict], None]):
        """Register callback for state updates."""
        
    def on_log_update(self, callback: Callable[[list], None]):
        """Register callback for log entries."""
        
    async def get_state(self) -> dict:
        """Get current state (blocking)."""
        return await self.send_command("get_state")
        
    async def move_feature(self, feature_slug: str, from_stage: str, 
                           to_stage: str, board: str) -> dict:
        return await self.send_command("move_feature", feature_slug=feature_slug,
                                       from_stage=from_stage, to_stage=to_stage, board=board)
        
    # ... similar for all commands
```

### Phase 2: Update TUI to Client Mode

#### 2.1 Rewrite `tui.py` as Thin Client

**Key Changes:**

```python
class PipelineApp(App):
    def __init__(self):
        super().__init__()
        self._config = Config()
        self._config.setup_boards()
        
        # CONNECT TO SERVER INSTEAD OF MANAGING STATE
        self._client = PipelineClient()
        self._client.on_state_update(self._on_state_update)
        self._client.on_log_update(self._on_log_update)
        
        # UI STATE - just for rendering, not management
        self._selected_feature_slug: Optional[str] = None
        self._ui_features: list[dict] = []  # From server state
        
    async def on_mount(self):
        # Connect to server
        await self._client.connect()
        # Get initial state
        state = await self._client.get_state()
        self._apply_state(state)
        
    def _on_state_update(self, state: dict):
        """Handle state broadcast from server."""
        self._apply_state(state)
        self.refresh()
        
    def _apply_state(self, state: dict):
        """Update UI from server state."""
        self._ui_features = state.get('features', [])
        # Update other UI state...
        
    # ALL ACTIONS NOW SEND COMMANDS TO SERVER
    
    async def action_promote(self):
        """Promote selected feature."""
        if self._selected_feature_slug:
            await self._client.move_feature(
                self._selected_feature_slug,
                from_stage=self._current_stage,
                to_stage=next_stage,
                board=self._active_board
            )
            
    async def action_start(self):
        """Start agent."""
        await self._client.start_agent(
            phase=self._current_phase,
            feature_slug=self._selected_feature_slug
        )
        
    # REMOVED:
    # - All state management (_features, _boards, etc.)
    # - Agent execution logic (now in server)
    # - runner.py calls moved to server
```

### Phase 3: Absorb `server_client.py` into Server

#### 3.1 Move Golang WebSocket Connection into `server.py`

**Current:** `server_client.py` lives in the TUI process and maintains a
WebSocket to the Golang server. When the TUI dies, this connection dies.

**New:** The Python server owns the Golang WebSocket directly. `server_client.py`
is no longer needed as a separate module — its WebSocket logic moves into
`PipelineServer._connect_golang()`.

**Key changes:**

```python
# Inside PipelineServer (server.py)

async def _connect_golang(self):
    """Maintain persistent bidirectional WebSocket to Golang server.

    Absorbs the role of server_client.py. The connection lives in the
    server process, so it survives TUI disconnect/reconnect.
    """
    while True:  # auto-reconnect loop
        try:
            async with websockets.connect(self._golang_ws_url) as ws:
                self._golang_ws = ws
                # Push full state on connect
                await self._push_state_to_golang()
                # Listen for incoming commands from WebUI
                async for message in ws:
                    cmd = json.loads(message)
                    await self._dispatch_command(cmd, source="webui")
        except ConnectionClosed:
            self._golang_ws = None
            await asyncio.sleep(2)  # reconnect backoff

async def _push_state_to_golang(self):
    """Push full state to Golang server (same format as today)."""
    state = self._build_state_message()
    if self._golang_ws:
        await self._golang_ws.send(json.dumps(state))
```

**What happens to `server_client.py`:**
- Delete it. Its responsibilities are absorbed by `PipelineServer`.
- The Golang WebSocket URL moves to server config.
- The message format to/from Golang stays the same — no Go-side changes needed
  for the transport layer.

#### 3.2 Golang Server Changes

The Golang server (`hub.go`) needs minimal changes:

- **No change** to how it handles browser WebSocket connections
- **No change** to the message format it relays
- The only difference is that the upstream Python connection is now persistent
  and survives TUI restarts — the Go server should handle reconnects gracefully
  (it likely already does)

### Phase 4: Service Configuration

#### 4.1 Update systemd service

Current:
```
[Service]
ExecStart=/usr/bin/tmux new-session -d -s mad-mad-c5a2 -x 200 -y 50 /home/paulewog/MAD/pipeline/bin/python3 /home/paulewog/MAD/pipeline/pipeline.py tui
```

New:
```
[Service]
ExecStart=/usr/bin/tmux new-session -d -s mad-mad-c5a2 -x 200 -y 50 /home/paulewog/MAD/pipeline/bin/python3 -m pipeline server
```

#### 4.2 Add `pipeline server` CLI command

In `pipeline.py`:

```python
@cli.command()
def server():
    """Start the headless pipeline server."""
    from server import PipelineServer
    server = PipelineServer()
    server.run()
```

## Edge Cases & Considerations

### 1. Concurrency

**Issue:** Multiple TUI clients could connect simultaneously.

**Solution:** 
- Server handles each client in separate async task
- Use asyncio.Lock for commands that modify state
- Server broadcasts state after every change

### 2. Client Disconnection

**Issue:** 
- TUI client exits → server continues running
- WebUI disconnects → server continues running

**Solution:** 
- Server maintains set of connected clients
- Broadcasts to all on state changes
- Clients auto-reconnect on disconnect

### 3. State Synchronization

**Issue:** When new client connects, they need current state.

**Solution:** 
- Server sends full state immediately on client connect
- Clients update UI from state, not local cache

### 4. WebUI Command Forwarding

**Issue:**
- Golang server receives commands from browsers
- Commands must reach the Python server

**Solution:**
- Python server maintains a persistent bidirectional WebSocket to Golang
- State flows: Python → Golang → Browser
- Commands flow: Browser → Golang → Python (same WebSocket, reverse direction)
- Golang relays commands without interpreting them
- Python server dispatches WebUI commands through the same handler as TUI
  commands — the source is tagged ("webui" vs "tui") but logic is identical

### 5. Agent Execution

**Issue:** 
- Agents run in separate processes
- Output needs to go to all clients

**Solution:** 
- Keep runner.py - server calls runner methods
- Server captures agent output via callbacks
- Server broadcasts log entries to all clients

### 6. Lock File

**Issue:** Current lock prevents multiple TUI instances.

**Solution:** 
- Server handles concurrency internally
- Can allow multiple TUI clients (different users viewing same state)
- Remove lock file logic from TUI

### 7. Restart Behavior

**Issue:** 
- Current "Restart TUI" in webui restarts the service

**Solution:** 
- Server continues running when clients disconnect
- Clients automatically reconnect
- "Restart Server" could fully restart the python process if needed

### 8. Backward Compatibility

**Issue:** Need to maintain API compatibility with webui.

**Solution:** 
- Keep same message formats
- Golang server acts as compatibility layer
- Or: update webui to connect directly to python

### 9. Error Handling

**Issues:**
- Client sends invalid command
- Client disconnects mid-command
- Network/socket errors

**Solution:**
- Server validates commands, returns error messages
- Commands are atomic (complete or rollback)
- Timeouts on socket operations

### 10. Security

**Issues:**
- Unix socket has no auth
- Anyone with file access can connect

**Solution:**
- Use filesystem permissions (socket mode 0600)
- Or use TCP with token auth
- For local use, filesystem permissions are sufficient

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `server.py` | CREATE | Headless server with all state management + Golang WS |
| `client.py` | CREATE | Client library for TUI to connect to server via Unix socket |
| `tui.py` | MODIFY | Rewrite as thin client (UI only, uses client.py) |
| `server_client.py` | DELETE | Absorbed into server.py |
| `pipeline.py` | MODIFY | Add `server` CLI command |
| Service files | MODIFY | Run server instead of tui |

## Testing Strategy

### Unit Tests
- Test server command handlers
- Test client serialization/deserialization
- Test state management

### Integration Tests
- Server + TUI client
- Server + golang proxy
- Full stack: browser → golang → python

### Manual Testing
1. Start service (`pipeline service start`)
2. Connect TUI client (`pipeline tui`)
3. Connect webui (browser)
4. Disconnect/reconnect clients
5. Verify state synchronization
6. Verify commands work from both clients

## Implementation Steps (Detailed)

### Step 1: Create server.py
1. Define state classes (move from tui.py)
2. Implement Unix socket server for local TUI clients
3. Implement Golang WebSocket connection (absorb server_client.py logic)
4. Implement command handlers (shared by TUI and WebUI commands)
5. Implement broadcast mechanism (Unix socket + Golang WS)
6. Add auto-reconnect for Golang WebSocket

### Step 2: Create client.py
1. Implement async Unix socket client
2. Implement command sending
3. Implement state callback handling

### Step 3: Update tui.py
1. Replace state with client calls
2. Update all actions to use client
3. Update state callbacks to refresh UI

### Step 4: Delete server_client.py
1. Verify all Golang WS logic is in server.py
2. Remove server_client.py
3. Remove imports/references from tui.py

### Step 5: Update service
1. Add `pipeline server` command
2. Update service file
3. Test start/stop

## Timeline Estimate

| Phase | Effort |
|-------|--------|
| Phase 1 (server + client lib) | 2-3 hours |
| Phase 2 (TUI client) | 2-3 hours |
| Phase 3 (golang connection) | 1-2 hours |
| Phase 4 (service config) | 30 min |
| Testing | 1-2 hours |
| **Total** | **~8-12 hours** |

## Alternative Approaches Considered

### 1. Keep tmux, fix reattach
- Tried: stty sane, termios reset, Textual events
- Result: Doesn't work reliably
- Conclusion: Not worth more effort

### 2. Use Textual's native remote mode
- Textual doesn't have a built-in client/server mode
- Would require significant hacking

### 3. Use existing tools (tmux-zoom, etc.)
- Workarounds for tmux issues
- Don't solve the fundamental architecture problem

### 4. Run server in docker
- Could isolate the python process
- Adds deployment complexity
- Doesn't change the fundamental architecture

The proposed server/client refactor is the cleanest solution that addresses all the issues.
