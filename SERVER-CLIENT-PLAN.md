# MAD Pipeline Server/Client Architecture Plan

## Overview

Build a centralized MAD Server that multiple standalone MAD Pipeline instances can connect to, allowing remote monitoring and control via a web interface.

## IMPORTANT: Server is Optional

The server/client architecture must be **completely optional**:

- **TUI runs standalone** by default - no server connection required
- **Config flag** to enable server connection: `server.enabled = true`
- **Graceful degradation** - if server is unreachable, client continues working
- **No impact on pipeline** - server is purely for monitoring/control, not required for execution

## Updated Configuration

```json
{
  "server": {
    "enabled": false,
    "url": "https://mad.yourdomain.com",
    "api_key": "your-secret-key",
    "client_id": "chickencity-desktop",
    "push_interval_seconds": 10
  }
}
```

When `enabled: false` (default), MAD runs completely standalone.

---

## Architecture

```
                    ┌─────────────────────────┐
                    │    MAD Server (Go)     │
                    │    (cloud/DO host)     │
                    │                       │
                    │ - HTTP REST API       │
                    │ - WebSocket Server    │
                    │ - Web UI (HTMX)       │
                    │ - Client Registry     │
                    │ - State Store        │
                    └──────────┬────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
   │ MAD Client  │     │ MAD Client  │     │   Browser   │
   │ (TUI/local) │     │ (headless)  │     │   (phone)   │
   │             │     │             │     │             │
   │ - WS push  │     │ - WS push   │     │ - View only │
   │ - API key  │     │ - API key   │     │ - API key   │
   └─────────────┘     └─────────────┘     └─────────────┘
```

## Tech Stack

- **Server**: Go (user preference)
- **Web Framework**: Standard library + gorilla/websocket
- **Web UI**: HTMX (server-side rendered, no JS build)
- **Client**: Python (existing MAD pipeline)

---

## Protocol Specification

### WebSocket Messages (JSON)

#### Client → Server

```json
// Registration
{"type": "register", "client_id": "chickencity-desktop", "board": "chickencity", "api_key": "secret-key"}

// Push state update
{"type": "state_update", "client_id": "chickencity-desktop", "features": [...], "pipeline_log": [...]}

// Disconnect
{"type": "disconnect", "client_id": "chickencity-desktop"}
```

#### Server → Client

```json
// Ack registration
{"type": "ack", "client_id": "chickencity-desktop", "server_time": "..."}

// Request full sync (on reconnect)
{"type": "request_sync", "client_id": "chickencity-desktop"}
```

---

## Server Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | / | Web UI (HTMX) |
| GET | /api/clients | List connected clients |
| GET | /api/clients/:id | Client details + features |
| GET | /api/clients/:id/logs | Pipeline logs |
| WS | /ws | Client WebSocket |

---

## Implementation Phases

### Phase 1: Server Core (Go)

1. Project structure with config, server, websocket, client registry
2. In-memory state store
3. REST API handlers
4. HTMX templates for dashboard + client detail

### Phase 2: Python Client

1. WebSocket client module
2. Auto-connect on TUI startup (when enabled in config)
3. Push state on feature changes
4. Handle server commands (future)

### Phase 3: Web UI Polish

1. Better styling
2. Real-time log streaming
3. Feature detail modal

---

## Questions

1. **Port**: Default 8080? Or should it bind to 443 (behind your nginx)?
2. **Client ID**: Auto-generated (UUID) or configured in config.json?
3. **Log lines**: How many to keep per client? (1000?)
4. **Auto-reconnect**: Client should auto-reconnect on disconnect?
