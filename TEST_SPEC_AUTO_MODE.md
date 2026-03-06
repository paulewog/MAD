# Test Specification: Auto-Plan / Auto-Impl Web UI Controls

## Feature Summary

Add toggle buttons to the web UI that allow users to enable/disable auto-plan and auto-impl modes on pipeline clients. The flow is: UI button click -> POST to server API -> WebSocket message to pipeline client -> client toggles mode -> next state_update reflects new state -> UI polls and updates button.

---

## 1. Behaviors That MUST Be Verified

### 1.1 Pipeline Client: `push_state()` includes auto-mode booleans

**Behavior:** When `push_state()` is called with `auto_plan_enabled` and `auto_impl_enabled` parameters, the `state_update` JSON message sent over WebSocket includes `"auto_plan"` and `"auto_impl"` boolean fields.

- When `auto_plan_enabled=True`, the message contains `"auto_plan": true`
- When `auto_plan_enabled=False`, the message contains `"auto_plan": false`
- Same for `auto_impl_enabled` -> `"auto_impl"`
- When both are True, both fields are True in the same message
- When both are False, both fields are False
- Default behavior: if parameters are not provided, both default to False (backward compatibility)

**Success criteria:** The JSON payload sent via WebSocket contains correct `auto_plan` and `auto_impl` boolean values matching the parameters passed to `push_state()`.

### 1.2 Pipeline Client: `_handle_incoming()` dispatches `set_auto_mode` messages

**Behavior:** When a `set_auto_mode` message arrives via WebSocket, the client parses it and invokes the `on_set_auto_mode` callback with the correct mode and enabled state.

- Message `{"type": "set_auto_mode", "mode": "plan", "enabled": true}` calls `on_set_auto_mode("plan", True)`
- Message `{"type": "set_auto_mode", "mode": "impl", "enabled": false}` calls `on_set_auto_mode("impl", False)`
- Message `{"type": "set_auto_mode", "mode": "plan", "enabled": false}` calls `on_set_auto_mode("plan", False)`
- Message `{"type": "set_auto_mode", "mode": "impl", "enabled": true}` calls `on_set_auto_mode("impl", True)`

**Success criteria:** The callback is invoked exactly once per message with the correct `(mode, enabled)` arguments.

### 1.3 Pipeline Client: `on_set_auto_mode` callback wiring

**Behavior:** `ServerClient.__init__` accepts an `on_set_auto_mode` callback parameter and stores it. When not provided, it defaults to None.

**Success criteria:** `sc._on_set_auto_mode` matches the callback passed to `__init__`, or is None when omitted.

### 1.4 TUI: passes auto-mode state to all `push_state()` calls

**Behavior:** Every call to `push_state()` in `tui.py` includes the current values of `self.auto_plan_enabled` and `self.auto_implement_enabled`.

**Success criteria:** Inspecting the arguments of every `push_state()` call site confirms `auto_plan_enabled` and `auto_impl_enabled` are passed from the TUI's reactive properties.

### 1.5 TUI: wires `on_set_auto_mode` callback to toggle methods

**Behavior:** The TUI provides an `on_set_auto_mode` callback to `ServerClient.__init__`. When invoked with `("plan", True)`, it sets `self.auto_plan_enabled = True`. When invoked with `("impl", False)`, it sets `self.auto_implement_enabled = False`. And vice versa.

**Success criteria:** After the callback fires, the TUI's `auto_plan_enabled` or `auto_implement_enabled` property reflects the requested state.

### 1.6 Server: `ClientState` stores `AutoPlan` and `AutoImpl`

**Behavior:** The `ClientState` struct has `AutoPlan bool` and `AutoImpl bool` fields, serialized as `"auto_plan"` and `"auto_impl"` in JSON.

**Success criteria:** A `ClientState` marshaled to JSON includes `"auto_plan"` and `"auto_impl"` boolean fields. These default to `false` for newly connected clients.

### 1.7 Server: `state_update` handler parses auto-mode fields

**Behavior:** When a `state_update` WebSocket message includes `"auto_plan": true` and/or `"auto_impl": true`, `handleMessage` stores these values in the client's `ClientState`.

- A state_update with `"auto_plan": true` sets `ClientState.AutoPlan = true`
- A state_update without `"auto_plan"` leaves the existing value unchanged
- A state_update with `"auto_plan": false` sets `ClientState.AutoPlan = false`
- Same for `"auto_impl"`

**Success criteria:** After processing a state_update, `hub.GetClient(id)` returns a `ClientState` with the correct `AutoPlan`/`AutoImpl` values.

### 1.8 Server: `POST /api/clients/{id}/auto-mode` endpoint

**Behavior:** The server exposes a POST endpoint that accepts `{"mode": "plan"|"impl", "enabled": true|false}` and forwards a `set_auto_mode` WebSocket message to the specified pipeline client.

- Valid request with connected client: returns 200 OK, sends WebSocket message `{"type": "set_auto_mode", "mode": "<mode>", "enabled": <bool>}` to the client
- Client not found: returns 404 with `{"error": "client not found"}`
- Client not connected: returns 422 with `{"error": "client not connected"}`
- Non-POST method: returns 405

**Success criteria:** Correct HTTP status codes returned, and the WebSocket message received by the pipeline client matches the request payload.

### 1.9 Server: API authentication on auto-mode endpoint

**Behavior:** The auto-mode endpoint requires dashboard auth or API auth (same pattern as `serveSubmitAnswers`).

- Request with valid `?key=` parameter: authorized
- Request with valid `Authorization: Bearer` header: authorized
- Request with valid `X-API-Key` header: authorized
- Request with no credentials when auth is required: returns 401

**Success criteria:** Unauthorized requests are rejected with 401. Authorized requests proceed normally.

### 1.10 Web UI: toggle buttons render with correct initial state

**Behavior:** The client detail page renders auto-plan and auto-impl toggle buttons. Their initial ON/OFF state reflects the `AutoPlan`/`AutoImpl` fields from the server-side template data.

- If `AutoPlan` is true, the Plan toggle shows ON (green/active style)
- If `AutoPlan` is false, the Plan toggle shows OFF (muted style)
- Same for `AutoImpl` and the Impl toggle

**Success criteria:** The HTML rendered by the template contains buttons whose visual state matches the `AutoPlan`/`AutoImpl` booleans.

### 1.11 Web UI: toggle button click sends correct POST

**Behavior:** Clicking a toggle button sends `POST /api/clients/{id}/auto-mode` with `{"mode": "plan"|"impl", "enabled": <opposite of current>}`.

**Success criteria:** The fetch call is made with the correct URL, method, content-type, and body.

### 1.12 Web UI: polling updates button state

**Behavior:** The existing agent status polling (which fetches client JSON from `/api/clients/{id}`) reads `auto_plan` and `auto_impl` from the response and updates the toggle buttons' ON/OFF visual state.

**Success criteria:** After the poll response arrives with changed auto-mode values, the buttons reflect the new state without a page reload.

### 1.13 End-to-end data flow

**Behavior:** The full round-trip works:
1. UI sends POST to toggle auto-plan ON
2. Server sends `set_auto_mode` to pipeline client
3. Pipeline client sets `auto_plan_enabled = True`
4. Next `push_state()` includes `"auto_plan": true`
5. Server stores `AutoPlan = true`
6. Next UI poll shows the button as ON

**Success criteria:** Starting from auto-plan OFF, after triggering the toggle, all intermediate states are correct and the UI eventually reflects ON.

---

## 2. Edge Cases

### 2.1 Invalid mode value in API request

- POST with `{"mode": "invalid", "enabled": true}` should return 400/422 with an error message
- POST with `{"mode": "", "enabled": true}` should return 400/422
- POST with `{"mode": 123, "enabled": true}` (wrong type) should return 400

### 2.2 Missing or null fields in API request

- POST with `{}` (empty body) should return 400
- POST with `{"mode": "plan"}` (missing `enabled`) should return 400
- POST with `{"enabled": true}` (missing `mode`) should return 400
- POST with `{"mode": null, "enabled": null}` should return 400
- POST with empty request body should return 400
- POST with non-JSON body should return 400

### 2.3 Invalid `enabled` type

- POST with `{"mode": "plan", "enabled": "yes"}` (string instead of bool) should return 400
- POST with `{"mode": "plan", "enabled": 1}` (number instead of bool) — decide whether to accept or reject

### 2.4 `_handle_incoming` receives malformed `set_auto_mode`

- Message with `{"type": "set_auto_mode"}` (missing mode and enabled) — callback should NOT be invoked; no crash
- Message with `{"type": "set_auto_mode", "mode": "plan"}` (missing enabled) — callback should NOT be invoked or should use a safe default
- Message with `{"type": "set_auto_mode", "mode": "unknown", "enabled": true}` (unknown mode) — callback should either ignore or pass through for the callback to handle

### 2.5 No `on_set_auto_mode` callback provided

- If `ServerClient` was created without `on_set_auto_mode`, receiving a `set_auto_mode` message should not crash. It should be silently ignored (same pattern as `on_answers_received` with missing callback).

### 2.6 Callback raises an exception

- If `on_set_auto_mode` raises an exception, `_handle_incoming` should catch it and log a warning, not crash the connection loop.

### 2.7 `push_state()` backward compatibility

- Existing callers that don't pass `auto_plan_enabled`/`auto_impl_enabled` should still work. The fields should default to False in the message. No existing tests should break.

### 2.8 Rapid toggling

- Multiple rapid POST requests (e.g., ON then OFF then ON in quick succession) should each be forwarded to the client. The final state should match the last request.

### 2.9 Client disconnects between API call and WebSocket delivery

- If the client disconnects after the API validates it's connected but before the WebSocket message is sent, the API should return an error (from `SendToClient` failing), not silently succeed.

### 2.10 `state_update` with auto-mode fields missing

- A `state_update` message that does NOT include `auto_plan` or `auto_impl` fields should leave the existing `ClientState.AutoPlan`/`AutoImpl` values unchanged (not reset them to false).

### 2.11 `state_update` with non-boolean auto-mode values

- A `state_update` with `"auto_plan": "yes"` (string) should be ignored or treated as a parse error for that field, without affecting other fields in the state_update.

### 2.12 Template rendering with missing AutoPlan/AutoImpl fields

- If `AutoPlan`/`AutoImpl` are zero-value (false) in the template data, the buttons should render in OFF state. No template error should occur.

### 2.13 JSON API response includes auto-mode fields

- `GET /api/clients/{id}` should include `"auto_plan"` and `"auto_impl"` in the JSON response so the UI polling can read them.

---

## 3. What Constitutes Failure

### 3.1 Python (pipeline) test failures

- `push_state()` sends a message missing `auto_plan` or `auto_impl` fields
- `push_state()` sends wrong boolean values (e.g., True when False was passed)
- `_handle_incoming()` does not invoke `on_set_auto_mode` when a valid `set_auto_mode` message arrives
- `_handle_incoming()` invokes `on_set_auto_mode` with wrong arguments (swapped mode/enabled, wrong types)
- `_handle_incoming()` crashes (raises unhandled exception) on any input
- `ServerClient.__init__` does not accept `on_set_auto_mode` parameter
- Existing tests in `test_server_client.py` break due to signature changes (backward compatibility failure)

### 3.2 Go (server) test failures

- `ClientState` JSON serialization does not include `auto_plan`/`auto_impl` fields
- `handleMessage` for `state_update` does not parse or store `auto_plan`/`auto_impl`
- `handleMessage` resets `AutoPlan`/`AutoImpl` to false when the fields are absent from a state_update
- `POST /api/clients/{id}/auto-mode` returns wrong status codes
- `POST /api/clients/{id}/auto-mode` does not send a WebSocket message to the client
- The WebSocket message sent has wrong type, mode, or enabled values
- Authentication is not enforced on the new endpoint
- Any panic or unhandled error in request handling

### 3.3 Integration / E2E failures

- The round-trip described in 1.13 does not complete — state gets stuck or lost at any stage
- The UI poll does not pick up changed `auto_plan`/`auto_impl` values
- Toggling auto-mode from the web UI has no effect on the pipeline client's actual behavior

### 3.4 Expected error messages

| Scenario | Expected HTTP Status | Expected Error |
|---|---|---|
| Client not found | 404 | `"client not found"` |
| Client not connected | 422 | `"client not connected"` |
| Invalid JSON body | 400 | `"invalid JSON"` |
| Missing/invalid mode | 400 | `"invalid mode"` or similar |
| Wrong HTTP method | 405 | `"method not allowed"` |
| Unauthorized | 401 | `"unauthorized"` |

---

## 4. Out of Scope

- **UI visual/styling assertions** — Do not test CSS colors, pixel positions, animations, or visual appearance of toggle buttons. Only verify that the correct HTML elements and data attributes are rendered.
- **UI timing/animation** — Do not test how quickly the button updates after a poll, transition effects, or debounce timing.
- **Performance benchmarks** — Do not measure response times, WebSocket throughput, or polling overhead.
- **Browser compatibility** — Do not test across different browsers or browser versions.
- **TUI rendering** — Do not test Textual widget rendering, status bar display, or notification messages from the TUI. Only test the data flow (callback invocation, property changes).
- **Existing auto-plan/auto-impl logic** — Do not re-test that `action_toggle_auto_plan` / `action_toggle_auto_implement` / `_auto_plan_check` / `_auto_implement_check` work correctly. These are existing behaviors. Only test that the web UI callback triggers the same toggles.
- **WebSocket reconnection behavior** — Already tested. Only verify that `set_auto_mode` messages are dispatched during an active connection (same as `answer_questions`).
- **Server startup, shutdown, or configuration** — Do not test server boot, graceful shutdown, or config file parsing beyond what's needed for the new fields.
- **Load testing / concurrent user sessions** — Do not test multiple browser tabs or users toggling simultaneously.
- **Mobile/PWA behavior** — Do not test service worker interaction, offline mode, or mobile-specific rendering.
