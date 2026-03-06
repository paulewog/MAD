# Test Spec: Start Planning/Implementing from Web UI

## 1. Behaviors That MUST Be Verified

### 1.1 STAGE_ACTIONS config in state.py

- **Behavior:** `STAGE_ACTIONS` dict exists in `pipeline/state.py` with keys `"plan"` and `"implement"`, each mapping to a list of stage strings.
- **Success:** `STAGE_ACTIONS["plan"]` contains `["plan-inbox", "reviewing-plan", "requested-input", "approved"]`. `STAGE_ACTIONS["implement"]` contains `["approved", "spec-writing"]`.
- **Expected output:** Importing `STAGE_ACTIONS` from `state` yields the correct dict without error.

### 1.2 TUI uses STAGE_ACTIONS instead of hardcoded stages

- **Behavior:** `action_plan_only`, `action_implement_only`, `_auto_plan_check`, and `_auto_implement_check` in `tui.py` reference `STAGE_ACTIONS` from `state.py` to determine allowed stages, rather than inline string lists.
- **Success:** Changing a value in `STAGE_ACTIONS` (e.g., removing `"approved"` from the plan list) changes TUI behavior accordingly — the TUI no longer offers planning for features in the `approved` stage.
- **Expected output:** No hardcoded stage lists remain in these four methods for determining action eligibility.

### 1.3 State push includes stage_actions and per-feature available_actions

- **Behavior:** `push_state()` in `server_client.py` includes a `stage_actions` field (the `STAGE_ACTIONS` dict) and each feature in the `features` list includes an `available_actions` array.
- **Success criteria:**
  - The `state_update` WebSocket message JSON contains a top-level `stage_actions` key matching the `STAGE_ACTIONS` dict.
  - Each feature object in the `features` array has an `available_actions` list.
  - A feature in stage `"plan-inbox"` with no plan agent running has `available_actions` containing `"plan"`.
  - A feature in stage `"approved"` with no agents running has `available_actions` containing both `"plan"` and `"implement"`.
  - A feature in stage `"done"` has an empty `available_actions`.
  - When the plan agent is running, `"plan"` is excluded from all features' `available_actions` even if their stage would normally allow it.
  - When the impl agent is running, `"implement"` is excluded similarly.

### 1.4 Go server parses stage_actions and available_actions from state_update

- **Behavior:** `hub.go` `handleMessage` for `state_update` parses `stage_actions` into `ClientState.StageActions` and per-feature `available_actions` into `FeatureSummary.AvailableActions`.
- **Success:** After a state_update message is processed, `hub.GetClient(id)` returns a `ClientState` with correct `StageActions` map and each feature has the correct `AvailableActions` slice.
- **Expected output:** The JSON API response at `/api/clients/{id}` includes both `stage_actions` and per-feature `available_actions`.

### 1.5 POST /api/clients/{id}/features/{fid}/start-agent endpoint — happy path

- **Behavior:** Sending `POST /api/clients/{id}/features/{fid}/start-agent` with body `{"action": "plan"}` for a feature in `"plan-inbox"` stage with no plan agent running results in a `start_agent` WebSocket message sent to the pipeline client.
- **Success criteria:**
  - Server returns HTTP 200 with `{"status": "ok"}`.
  - The WebSocket message received by the pipeline client is `{"type": "start_agent", "feature_id": "<fid>", "action": "plan"}`.
  - Same behavior works for `{"action": "implement"}` on a feature in `"approved"` stage.

### 1.6 POST start-agent — server-side validation rejects invalid requests

- **Behavior:** The endpoint validates before sending the WebSocket command.
- **Success criteria (each is a separate check):**
  - **Unknown client:** Returns 404 `{"error": "client not found"}`.
  - **Disconnected client:** Returns 422 `{"error": "client not connected"}`.
  - **Unknown feature ID:** Returns 404 `{"error": "feature not found"}`.
  - **Wrong stage for action:** A feature in `"done"` stage with action `"plan"` returns 400 or 409 with an error indicating the stage is not valid for that action.
  - **Agent already busy:** A feature in `"plan-inbox"` with action `"plan"` but plan agent is already running returns 409 `{"error": ...}` indicating the agent is busy.
  - **Invalid action value:** `{"action": "unknown"}` returns 400 with an error about invalid action.
  - **Missing action field:** `{}` returns 400.
  - **Wrong HTTP method (GET):** Returns 405.

### 1.7 Python client handles incoming start_agent message

- **Behavior:** When the server sends `{"type": "start_agent", "feature_id": "abc123", "action": "plan"}` over WebSocket, `_handle_incoming` in `server_client.py` invokes the `on_start_agent` callback with `("abc123", "plan")`.
- **Success:** The callback is called exactly once with the correct arguments.
- **Expected output:** If no callback is registered, the message is silently ignored (no crash).

### 1.8 TUI on_start_agent callback triggers the correct pipeline action

- **Behavior:** When `on_start_agent("abc123", "plan")` fires in the TUI:
  1. The TUI finds the feature with id `"abc123"`.
  2. Validates the feature's stage is in `STAGE_ACTIONS["plan"]`.
  3. Checks the plan agent is not already running.
  4. Calls `_run_plan_async()` on that feature.
- **Success:** The plan agent starts running for the correct feature. The TUI state updates to show the plan agent as busy.
- **Same behavior for `"implement"`:** calls `_run_implement_async()`.

### 1.9 TUI on_start_agent callback rejects invalid requests gracefully

- **Behavior:** When `on_start_agent` is called with a feature ID that doesn't exist, or a feature in a disallowed stage, or while the agent is already busy:
  - The action is not started.
  - No crash or unhandled exception occurs.
  - A warning is logged or a notification is shown.

### 1.10 Web UI renders action buttons based on available_actions

- **Behavior:** For each feature card in the web UI:
  - If `available_actions` contains `"plan"`, a "Start Planning" button is shown and enabled.
  - If `available_actions` contains `"implement"`, a "Start Implementing" button is shown and enabled.
  - If `available_actions` is empty, no action buttons are shown (or they are disabled/hidden).
- **Success:** Buttons reflect the server-provided `available_actions` — no client-side stage logic duplication.

### 1.11 Web UI button click sends correct API request

- **Behavior:** Clicking "Start Planning" on a feature sends `POST /api/clients/{id}/features/{fid}/start-agent` with `{"action": "plan"}`.
- **Success:** The request is sent with correct client ID, feature ID, and action. On 200 response, the button enters a loading/disabled state until the next state update.
- **Same for "Start Implementing"** with `{"action": "implement"}`.

### 1.12 Web UI handles error responses from start-agent

- **Behavior:** If the API returns 409 (agent busy), 400 (invalid stage), or 404 (not found), the web UI shows an appropriate error message to the user and does not leave buttons in a stuck loading state.

### 1.13 Authentication on start-agent endpoint

- **Behavior:** The endpoint requires the same auth as other API/dashboard endpoints (dashboard key or API key).
- **Success:** Unauthenticated requests return 401. Requests with valid dashboard key or API key succeed.

---

## 2. Edge Cases

### 2.1 Race condition: agent becomes busy between button render and click

- A user sees "Start Planning" enabled, but between rendering and clicking, the plan agent starts (e.g., via auto-plan or another UI session).
- The server-side validation must catch this and return 409. The web UI must handle the 409 gracefully.

### 2.2 Race condition: feature moves stages between button render and click

- A feature is in `"plan-inbox"` when the page renders, but by the time the user clicks, it has moved to `"reviewing-plan"` (still valid) or `"implementing"` (not valid for plan).
- The server must validate the current stage at request time, not rely on stale state.

### 2.3 Feature ID that exists on a different client

- Posting to `/api/clients/clientA/features/{fid}/start-agent` where `fid` belongs to `clientB` must return 404 (feature not found on this client).

### 2.4 Empty feature list

- A client with zero features: the start-agent endpoint returns 404 for any feature ID. The web UI renders no action buttons.

### 2.5 Simultaneous start-agent requests for same feature

- Two requests arrive nearly simultaneously for the same feature and same action. Only one should succeed; the second should get 409 (agent busy) once the first has been dispatched.

### 2.6 start_agent callback with unknown action string

- Python client receives `{"type": "start_agent", "feature_id": "x", "action": "migrate"}` — an action not in `STAGE_ACTIONS`. The callback should reject it gracefully (log warning, no crash).

### 2.7 start_agent with missing or null fields

- `{"type": "start_agent"}` — missing both feature_id and action. No crash; silently ignored or logged.
- `{"type": "start_agent", "feature_id": null, "action": null}` — same expectation.
- `{"type": "start_agent", "feature_id": "", "action": "plan"}` — empty feature ID, should be rejected.

### 2.8 Client disconnects right after start-agent is sent

- The server sends the WebSocket message, but the client disconnects before receiving it. No server crash. The HTTP response may already have been sent as 200 (fire-and-forget). This is acceptable — the next state push will reflect no agent started.

### 2.9 Stage that appears in both plan and implement allowed lists

- `"approved"` is in both `STAGE_ACTIONS["plan"]` and `STAGE_ACTIONS["implement"]`. A feature in `"approved"` should have both actions available (assuming neither agent is busy). Both buttons should render.

### 2.10 Auto-mode interaction

- If auto-plan is enabled and a user also clicks "Start Planning" via the web UI, the system should not start two plan agents. The busy check prevents this — the auto-plan check sees the agent is running and skips.

---

## 3. What Constitutes Failure

### 3.1 Functional failures

- The start-agent endpoint allows starting an agent on a feature in a disallowed stage.
- The start-agent endpoint allows starting an agent when that agent is already running.
- The Python client crashes or raises an unhandled exception when receiving a `start_agent` message with malformed data.
- The TUI starts the wrong agent type (e.g., implement when plan was requested).
- The TUI starts an agent on the wrong feature (feature ID mismatch).
- `available_actions` in the state push does not reflect agent busy status (shows "plan" as available while plan agent is running).
- `available_actions` includes actions not valid for the feature's current stage.
- Hardcoded stage lists remain in TUI methods after the refactor (divergence from `STAGE_ACTIONS`).

### 3.2 Integration failures

- The Go server does not parse `stage_actions` or `available_actions` from the state_update, causing the API response to omit them.
- The Go server sends a malformed WebSocket message to the Python client (wrong field names, missing type).
- The web UI sends the request to the wrong URL pattern or with the wrong JSON shape.
- Auth is not enforced on the new endpoint.

### 3.3 Error handling failures

- Server returns 500 instead of a structured error JSON for any validation failure.
- Web UI shows no feedback when the API returns an error — button stays in loading state forever.
- A 409 response crashes the web UI JavaScript.

---

## 4. Out of Scope

- **Visual/CSS assertions:** Button styling, color, positioning, animations, loading spinner appearance.
- **Performance/load testing:** Response time of the start-agent endpoint, WebSocket message latency.
- **UI timing:** Exact millisecond timing of button disable/enable transitions, debounce behavior.
- **End-to-end agent execution:** Whether the plan or implement agent actually produces correct output — only that it is invoked.
- **WebSocket reconnection:** The existing reconnect_loop behavior is already tested elsewhere; only the new message type handling matters here.
- **Browser compatibility:** Testing across different browsers or devices.
- **TUI rendering:** Textual framework rendering of the TUI — only the callback logic matters.
- **Existing auto-mode tests:** Auto-plan and auto-implement behavior is already covered; only the interaction with the new busy-check matters.
- **File system operations:** The feature file read/write/move behavior is out of scope; only the stage and ID lookups matter.
- **Server startup/shutdown:** Only the new endpoint and message handling, not server lifecycle.
