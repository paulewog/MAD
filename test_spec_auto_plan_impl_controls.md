# Test Specification: Add Auto-Plan/Auto-Impl Controls to Web UI

## Overview

This document specifies the test requirements for the auto-plan and auto-impl feature, which allows users to toggle the auto-plan and auto-implement modes from the web interface. The feature follows the same pattern used for the existing `answer_questions` functionality: web UI button → server API endpoint → WebSocket message to pipeline client → client toggles the mode. The current auto-mode state is also pushed from the pipeline client to the server so the UI reflects the actual state.

---

## 1. Behaviors That MUST Be Verified

### 1.1 Pipeline Client: Auto-Mode State Included in State Update Messages

**Behavior:** The `push_state()` method in `pipeline/server_client.py` includes `auto_plan_enabled` and `auto_impl_enabled` boolean values in the `state_update` WebSocket message.

**Success Criteria:**
- `push_state()` method accepts `auto_plan_enabled` and `auto_impl_enabled` parameters
- The JSON payload sent via WebSocket contains `"auto_plan": true/false` field
- The JSON payload sent via WebSocket contains `"auto_impl": true/false` field
- Values reflect the current state of the pipeline client

**Expected Outputs:**
- WebSocket message payload includes both fields with correct boolean values
- Format: `{"type": "state_update", "auto_plan": true/false, "auto_impl": true/false, ...}`

### 1.2 Pipeline TUI: Passes Auto-Mode State to push_state()

**Behavior:** The TUI component passes its internal auto-mode state to all `push_state()` calls.

**Success Criteria:**
- Every call to `push_state()` includes `self.auto_plan_enabled` value
- Every call to `push_state()` includes `self.auto_implement_enabled` value
- Values are current at the time of each call

**Expected Outputs:**
- No hardcoded values; dynamic state passed on each invocation

### 1.3 Pipeline Client: Handle Incoming set_auto_mode Messages

**Behavior:** The `ServerClient` class in `pipeline/server_client.py` handles incoming WebSocket messages with type `"set_auto_mode"`.

**Success Criteria:**
- `_handle_incoming()` method detects message type `"set_auto_mode"`
- Message payload contains `mode` field with value `"plan"` or `"impl"`
- Message payload contains `enabled` field with boolean value
- The `on_set_auto_mode` callback is invoked with correct parameters

**Expected Outputs:**
- Callback receives `(mode, enabled)` where mode is "plan" or "impl" and enabled is boolean

### 1.4 Pipeline TUI: Callback Toggles Auto-Mode State

**Behavior:** The TUI provides an `on_set_auto_mode` callback that toggles the appropriate internal state.

**Success Criteria:**
- Callback receives mode ("plan" or "impl") and enabled (boolean)
- When mode is "plan", `self.auto_plan_enabled` is set to the enabled value
- When mode is "impl", `self.auto_implement_enabled` is set to the enabled value
- Reuses existing action logic (similar to `action_toggle_auto_plan` / `action_toggle_auto_implement`)

**Expected Outputs:**
- Internal state variables updated correctly
- Subsequent state pushes reflect the new values

### 1.5 Server: Store Auto-Mode State in ClientState

**Behavior:** The server's `ClientState` struct in `server/hub.go` includes `AutoPlan` and `AutoImpl` boolean fields.

**Success Criteria:**
- `ClientState` struct has `AutoPlan bool` field
- `ClientState` struct has `AutoImpl bool` field
- Fields are persisted as part of client state

**Expected Outputs:**
- Server can access `client.AutoPlan` and `client.AutoImpl`

### 1.6 Server: Parse Auto-Mode in State Update Messages

**Behavior:** The server parses incoming `state_update` messages and stores the auto-mode values.

**Success Criteria:**
- In `handleMessage` for `state_update` message type
- Parses `auto_plan` boolean from JSON payload
- Parses `auto_impl` boolean from JSON payload
- Stores values in corresponding `ClientState` fields

**Expected Outputs:**
- Client state updated with received auto-mode values

### 1.7 Server: API Endpoint for Auto-Mode Toggle

**Behavior:** The server provides a REST endpoint `POST /api/clients/{id}/auto-mode` to toggle auto-modes.

**Success Criteria:**
- Endpoint accepts POST requests at `/api/clients/{clientID}/auto-mode`
- Request body contains JSON with `mode` field ("plan" or "impl")
- Request body contains JSON with `enabled` field (boolean)
- Validates client exists and is connected
- Forwards `set_auto_mode` WebSocket message to the pipeline client
- Returns 200 OK on success

**Expected Outputs:**
- HTTP 200 status code on successful send
- Response body indicates success
- WebSocket message sent: `{"type": "set_auto_mode", "mode": "...", "enabled": true/false}`

### 1.8 Server: API Endpoint Validation

**Behavior:** The API endpoint properly validates requests.

**Success Criteria:**
- Returns 404 when client ID does not exist
- Returns appropriate error when client is not connected
- Returns 400 for missing or invalid `mode` field
- Returns 400 for missing or invalid `enabled` field
- Validates `mode` is either "plan" or "impl"

**Expected Outputs:**
- HTTP 404 for non-existent client
- HTTP 503 (or similar) for disconnected client
- HTTP 400 for invalid request body

### 1.9 Web UI: Toggle Buttons Display

**Behavior:** The web UI displays toggle buttons for auto-plan and auto-impl modes.

**Success Criteria:**
- Toggle button visible for Auto-Plan near Plan Agent status card
- Toggle button visible for Auto-Impl near Impl Agent status card
- Buttons display current state (ON when enabled, OFF when disabled)
- Initial state reflects `AutoPlan` and `AutoImpl` values from template data
- Buttons are styled (green/active when ON, muted/gray when OFF)

**Expected Outputs:**
- Buttons visible in UI
- State indicator text or visual shows ON/OFF

### 1.10 Web UI: Toggle Button Click Action

**Behavior:** Clicking a toggle button sends a request to change the auto-mode state.

**Success Criteria:**
- Clicking Auto-Plan button sends POST to `/api/clients/{id}/auto-mode`
- Request body contains `{"mode": "plan", "enabled": true/false}` (toggles current state)
- Clicking Auto-Impl button sends POST with `{"mode": "impl", "enabled": true/false}`
- Request includes authentication key
- Button state updates after response received

**Expected Outputs:**
- Network request sent to correct endpoint
- Request uses correct HTTP method (POST)
- Request body is valid JSON

### 1.11 Web UI: Polling Updates Button State

**Behavior:** The UI updates button states based on polling data.

**Success Criteria:**
- Polling already fetches client JSON data
- Button ON/OFF state updated from `auto_plan` and `auto_impl` fields in response
- UI reflects actual state from server (which came from pipeline client)

**Expected Outputs:**
- Button visually changes to reflect state after polling cycle

### 1.12 Full Data Flow: UI Toggle to Pipeline State Change

**Behavior:** Complete end-to-end flow from UI button click to pipeline state update.

**Success Criteria:**
1. User clicks "Auto-Plan ON" button in web UI
2. Browser POSTs `{"mode":"plan","enabled":true}` to `/api/clients/{id}/auto-mode`
3. Server sends WebSocket message `{"type":"set_auto_mode","mode":"plan","enabled":true}` to pipeline
4. Pipeline `ServerClient._handle_incoming()` invokes callback with ("plan", true)
5. TUI sets `self.auto_plan_enabled = True`
6. Next `push_state()` call includes `"auto_plan": true` in message
7. Server stores `AutoPlan = true` in `ClientState`
8. Next UI poll receives updated state and button shows ON

**Expected Outputs:**
- All 8 steps complete successfully
- UI reflects final state after polling update

---

## 2. Edge Cases

### 2.1 Auto-Mode State Not Present in State Update

**Scenario:** Pipeline client sends state_update without auto_plan/auto_impl fields.

**Expected Behavior:** Server should handle missing fields gracefully, either using default values or preserving existing state.

**Test:** Send state_update without auto fields, verify server behavior is defined.

### 2.2 Malformed Boolean Values

**Scenario:** Auto-plan or auto-impl values are strings like "true" instead of boolean true, or unexpected values.

**Expected Behavior:** Server handles parsing errors gracefully and returns error or uses default.

**Test:** Send invalid boolean values, verify appropriate error or fallback behavior.

### 2.3 Client Disconnects After API Request

**Scenario:** API endpoint receives request, validates client is connected, but client disconnects before WebSocket message is sent.

**Expected Behavior:** Server detects disconnection, returns appropriate error to API caller.

**Test:** Client connected, API request sent, client disconnects before WebSocket send, verify error returned.

### 2.4 Rapid Toggle Operations

**Scenario:** User rapidly clicks toggle button multiple times in quick succession.

**Expected Behavior:** Each request processed; final state consistent with last processed request.

**Test:** Send multiple rapid toggle requests, verify no race conditions, final state correct.

### 2.5 Concurrent Toggles from Multiple UI Sessions

**Scenario:** Multiple users/web sessions toggle auto-mode for the same client simultaneously.

**Expected Behavior:** All requests processed; last-write-wins or explicit ordering; no crashes.

**Test:** Multiple simultaneous toggle requests, verify all succeed and state is consistent.

### 2.6 Invalid Mode Value in API Request

**Scenario:** API request contains mode value other than "plan" or "impl".

**Expected Behavior:** Server returns 400 error with descriptive message.

**Test:** Send `{"mode": "invalid", "enabled": true}`, verify 400 error.

### 2.7 Missing Enabled Field

**Scenario:** API request contains mode but missing enabled field.

**Expected Behavior:** Server returns 400 error indicating missing required field.

**Test:** Send `{"mode": "plan"}` without enabled, verify 400 error.

### 2.8 Invalid Enabled Field Type

**Scenario:** API request contains enabled field with non-boolean value (string, number, null).

**Expected Behavior:** Server returns 400 error indicating invalid type.

**Test:** Send `{"mode": "plan", "enabled": "yes"}`, verify 400 error.

### 2.9 Empty Client ID

**Scenario:** API request uses empty string as client ID.

**Expected Behavior:** Server returns 404 error.

**Test:** POST to `/api/clients//auto-mode`, verify 404 error.

### 2.10 Pipeline Client State Mismatch

**Scenario:** Pipeline client's internal auto-mode state differs from what server has stored (due to restart or sync issue).

**Expected Behavior:** Server state updates on next state_update from client; UI eventually shows correct state.

**Test:** Force state mismatch, trigger state push, verify server and UI update.

### 2.11 WebSocket Message Queue During Disconnection

**Scenario:** Server sends set_auto_mode WebSocket but client temporarily disconnected.

**Expected Behavior:** Message either queued or returned as error; no silent failures.

**Test:** Disconnect client, send API request, verify error or queued message delivered on reconnect.

### 2.12 Malformed JSON in WebSocket Messages

**Scenario:** Pipeline client or server sends malformed JSON in WebSocket messages.

**Expected Behavior:** Receiving end handles parse error gracefully without crashing.

**Test:** Send malformed JSON, verify no crash and appropriate error handling.

### 2.13 Authentication Key Missing or Invalid

**Scenario:** API request submitted without authentication key or with invalid key.

**Expected Behavior:** Server returns 401 or 403 error.

**Test:** Omit key parameter or send wrong key, verify authentication error.

### 2.14 Very Long Mode String

**Scenario:** API request contains extremely long mode string.

**Expected Behavior:** Server validates and rejects with 400 error.

**Test:** Send mode with 10000 characters, verify 400 error.

### 2.15 Case Sensitivity in Mode

**Scenario:** API request uses "PLAN" or "Impl" instead of "plan" or "impl".

**Expected Behavior:** Server validates strictly and returns error for non-lowercase values.

**Test:** Send `{"mode": "PLAN", "enabled": true}`, verify 400 error.

---

## 3. What Constitutes Failure

### 3.1 Success Criteria for Test Failure

A test fails when any of the following occurs:

**Functional Failures:**
- REST endpoint returns non-200 status for valid request
- WebSocket message not sent to correct client
- WebSocket message missing required fields (type, mode, enabled)
- Pipeline client does not update internal state on set_auto_mode
- TUI does not pass auto-mode state to push_state()
- Server does not store auto-mode values in ClientState
- UI buttons do not reflect actual state from server

**Validation Failures:**
- Missing input validation on API endpoint (allows invalid mode values)
- Client validation bypassed (allows request to non-existent client)
- Authentication bypassed (allows request without valid key)
- Server accepts malformed JSON without error

**Data Integrity Failures:**
- Auto-mode state lost or corrupted
- State inconsistency between server and pipeline client
- Multiple concurrent requests cause state corruption

**Error Handling Failures:**
- Server crashes on invalid input
- Server does not return appropriate error messages
- Client does not handle malformed WebSocket messages gracefully
- Disconnection not detected properly

### 3.2 Expected Error Messages

| Scenario | Expected Error Message |
|----------|----------------------|
| Client not found | "Client not found" or similar |
| Client disconnected | "Client not connected" or similar |
| Invalid mode value | "Invalid mode: must be 'plan' or 'impl'" or similar |
| Missing enabled field | "enabled field is required" or similar |
| Invalid enabled type | "enabled must be a boolean" or similar |
| Invalid JSON body | "Invalid JSON body" or similar |
| Missing auth key | "Authentication required" or similar |
| Invalid auth key | "Invalid authentication" or similar |

### 3.3 Rollback Behavior

**Scenario:** Operation fails partway through.

**Expected Behavior:**
- If REST endpoint validation fails, no WebSocket message sent, no state change
- If WebSocket send fails after validation, return error (pipeline state unchanged)
- If pipeline client fails to update state, error should be logged; next push_state reflects original state
- No partial state: either fully succeeds or fully fails with error

**No automatic rollback needed** - failed operations should not leave partial state. The server's ClientState should only update when valid state_update received from client.

---

## 4. Out of Scope

The following are explicitly NOT tested in this specification:

### 4.1 UI/Visual Testing
- Visual layout and styling of toggle buttons
- Button placement or positioning
- Color schemes or visual feedback (beyond basic ON/OFF state)
- Animation effects on toggle
- Responsive design on different screen sizes

### 4.2 Timing/Performance Testing
- Exact polling interval timing
- UI refresh timing after button click
- WebSocket message delivery latency
- Page load times
- Time between button click and state update in UI

### 4.3 Browser-Specific Behavior
- Cross-browser compatibility
- JavaScript behavior differences between browsers
- Form submission behavior in different browsers

### 4.4 Network Conditions
- Slow network simulation
- High latency handling
- Bandwidth limitations
- DNS resolution

### 4.5 Persistence/Database
- Long-term data persistence
- Server restart behavior (beyond basic state sync)
- File system limits
- Disk space considerations

### 4.6 Auto-Mode Implementation Details
- Internal implementation of auto-plan logic
- Internal implementation of auto-implement logic
- How auto-modes affect pipeline behavior
- Race conditions within the pipeline itself

### 4.7 Security (Beyond Basic Auth)
- XSS prevention in inputs
- CSRF protection
- SQL injection (not applicable - no database)
- Rate limiting
- Input sanitization details beyond basic validation

### 4.8 Concurrency Limits
- Maximum concurrent connections
- Maximum concurrent toggle operations
- WebSocket message queue depth limits

### 4.9 Logging and Monitoring
- Log message formats
- Metrics collection
- Tracing implementation

### 4.10 Documentation
- Help text in UI
- User guides
- API documentation

### 4.11 Existing answer_questions Functionality
- The answer_questions toggle functionality that this feature parallels
- Any existing auto-mode or toggle behaviors (beyond verifying the new feature follows similar patterns)

---

## Test Summary

This specification covers the critical path from web UI toggle button through to pipeline state change and back to UI update, including validation, error handling, and edge cases. All functional requirements from the feature plan are verified, while explicitly scoped-out items focus on non-functional aspects that should be tested separately if needed.

**Key Verification Points:**
1. Pipeline client includes auto-mode state in push_state()
2. Pipeline client handles incoming set_auto_mode messages
3. TUI wires up callback and passes state correctly
4. Server stores AutoPlan/AutoImpl in ClientState
5. Server parses auto-mode from state_update messages
6. API endpoint validates requests and forwards WebSocket messages
7. Web UI displays toggle buttons with correct state
8. Web UI sends correct API requests on button click
9. Full end-to-end data flow works correctly
