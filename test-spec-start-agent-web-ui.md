# Test Specification: Start Planning/Implementing from Web UI

## Overview

This document specifies the test requirements for the feature that enables users to trigger plan or implement agents on specific features from the web UI. The feature follows a command pattern where the web UI sends an HTTP request to the Go server, which validates and forwards the command via WebSocket to the connected Python pipeline client.

---

## 1. Behaviors That MUST Be Verified

### 1.1 Stage-Actions Configuration (Python)

**Behavior**: The `STAGE_ACTIONS` dictionary in `pipeline/state.py` correctly maps actions to their allowed stages.

**Success Criteria**:
- The `plan` action is allowed only for stages: `plan-inbox`, `reviewing-plan`, `requested-input`, `approved`
- The `implement` action is allowed only for stages: `approved`, `spec-writing`
- The dictionary is importable by both `tui.py` and `server_client.py`

**Expected Output**: No import errors when other modules reference `STAGE_ACTIONS` from `state.py`.

---

### 1.2 TUI Refactoring to Use Shared Config

**Behavior**: The TUI correctly uses `STAGE_ACTIONS` from `state.py` instead of hardcoded stage lists.

**Success Criteria**:
- `action_plan_only()` returns true only when current stage is in `STAGE_ACTIONS["plan"]`
- `action_implement_only()` returns true only when current stage is in `STAGE_ACTIONS["implement"]`
- `_auto_plan_check()` and `_auto_implement_check()` reference the same config

**Expected Output**: TUI buttons and menu options reflect the same rules as the server-side validation.

---

### 1.3 State Push Includes Stage Actions and Available Actions

**Behavior**: When the Python pipeline client pushes state to the Go server, it includes both the stage-actions configuration and computed available actions for each feature.

**Success Criteria**:
- The `push_state()` method includes a `stage_actions` field containing the full `STAGE_ACTIONS` dict
- Each feature in the state includes an `available_actions` array
- The `available_actions` array contains only actions allowed for that feature's current stage
- Actions are excluded from `available_actions` when the respective agent is already running (busy)

**Expected Output**: JSON payload sent to server contains:
```json
{
  "stage_actions": {
    "plan": ["plan-inbox", "reviewing-plan", "requested-input", "approved"],
    "implement": ["approved", "spec-writing"]
  },
  "features": [
    {
      "id": "feature-123",
      "stage": "approved",
      "available_actions": ["plan", "implement"]
    }
  ]
}
```

---

### 1.4 Start Agent Endpoint Validation

**Behavior**: The Go server validates requests to the `start_agent` endpoint before forwarding to the client.

**Success Criteria**:
- Endpoint accepts POST requests at `/api/clients/{client_id}/features/{feature_id}/start-agent`
- Request body contains valid `action` field ("plan" or "implement")
- Server validates the client exists
- Server validates the feature exists in the client's state
- Server validates the feature's stage is in the allowed stages for the requested action
- Server validates the respective agent (plan/impl) is not already running
- Valid requests result in WebSocket message sent to client
- Invalid requests return appropriate HTTP error codes

**Expected Outputs**:
- **200 OK**: When all validations pass and command is sent
- **400 Bad Request**: When action is missing or invalid
- **404 Not Found**: When client or feature does not exist
- **409 Conflict**: When the requested agent is already busy

---

### 1.5 WebSocket Command Delivery

**Behavior**: The Go server sends a `start_agent` WebSocket message to the Python client.

**Success Criteria**:
- Message format: `{"type": "start_agent", "feature_id": "...", "action": "plan"|"implement"}`
- Message is delivered to the correct connected client
- Message is delivered only after all server-side validations pass

**Expected Output**: Python client receives the message with correct payload.

---

### 1.6 Python Client Handles Start Agent Message

**Behavior**: The Python pipeline client processes incoming `start_agent` WebSocket messages.

**Success Criteria**:
- Client correctly parses `start_agent` message type
- Client extracts `feature_id` and `action` from message
- Client invokes the registered `on_start_agent` callback with correct parameters

**Expected Output**: The `on_start_agent(feature_id, action)` callback is invoked with correct values.

---

### 1.7 TUI Executes Agent on Callback

**Behavior**: The TUI starts the appropriate agent when the `on_start_agent` callback is triggered.

**Success Criteria**:
- TUI registers an `on_start_agent` callback when creating `PipelineClient`
- Callback finds the correct feature by ID
- Callback re-validates the stage is still allowed (using `STAGE_ACTIONS`)
- Callback re-validates the agent is not already busy
- If validations pass, callback invokes `_run_plan_async()` for "plan" action
- If validations pass, callback invokes `_run_implement_async()` for "implement" action
- Callback uses `call_from_thread()` to safely interact with TUI from WebSocket thread

**Expected Output**: The agent begins execution for the specified feature.

---

### 1.8 Web UI Displays Action Buttons

**Behavior**: The web UI renders action buttons based on the feature's `available_actions`.

**Success Criteria**:
- "Start Planning" button appears only when "plan" is in `available_actions`
- "Start Implementing" button appears only when "implement" is in `available_actions`
- Buttons are disabled when `available_actions` is empty or does not contain the action
- Buttons reflect the current state after each state update

**Expected Output**: Buttons are visible and enabled only when the action is actually available.

---

### 1.9 Web UI Button Click Sends Request

**Behavior**: Clicking an action button in the web UI sends the correct HTTP POST request.

**Success Criteria**:
- Clicking "Start Planning" sends POST to `/api/clients/{id}/features/{fid}/start-agent` with `{"action": "plan"}`
- Clicking "Start Implementing" sends POST to `/api/clients/{id}/features/{fid}/start-agent` with `{"action": "implement"}`
- Request includes appropriate authentication/headers
- UI shows loading state during request

**Expected Output**: HTTP request is sent with correct payload.

---

### 1.10 Go Hub Stores Stage Actions

**Behavior**: The Go server's `ClientState` correctly stores and exposes stage actions from the state update.

**Success Criteria**:
- `ClientState` struct includes `StageActions` map field
- `ClientState` includes per-feature `AvailableActions` 
- These fields are parsed from the incoming `state_update` WebSocket message
- These fields are included in the client state API response

**Expected Output**: API endpoint `/api/clients/{id}` returns stage_actions and available_actions in response.

---

### 1.11 Server-Side Validation Uses Exposed Data

**Behavior**: The `start-agent` endpoint uses the stored stage_actions for validation.

**Success Criteria**:
- Endpoint reads `StageActions` from client state
- Endpoint reads `AvailableActions` from the specific feature
- Validation uses these stored values (not hardcoded)

**Expected Output**: Validation logic uses dynamically received configuration.

---

## 2. Edge Cases

### 2.1 Empty or Null Inputs

| Scenario | Expected Behavior |
|----------|-------------------|
| Client ID is empty in request | Return 404 (client not found) |
| Feature ID is empty in request | Return 404 (feature not found) |
| Action field is missing from request body | Return 400 Bad Request |
| Action field is null | Return 400 Bad Request |
| Action field is empty string | Return 400 Bad Request |

### 2.2 Invalid Action Values

| Scenario | Expected Behavior |
|----------|-------------------|
| Action is "plan" (valid) | Process normally |
| Action is "implement" (valid) | Process normally |
| Action is "delete" (invalid) | Return 400 Bad Request |
| Action is "start" (invalid) | Return 400 Bad Request |
| Action is "PLAN" (uppercase) | Return 400 Bad Request |
| Action is "Plan" (mixed case) | Return 400 Bad Request |
| Action is a number | Return 400 Bad Request |
| Action is boolean | Return 400 Bad Request |

### 2.3 Stage Validation Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Feature stage is "plan-inbox", action is "plan" | Allow (stage in allowed list) |
| Feature stage is "in-progress", action is "plan" | Reject (stage not in allowed list) |
| Feature stage is "completed", action is "implement" | Reject (stage not in allowed list) |
| Feature stage is "approved", action is "implement" | Allow (stage in allowed list) |
| Feature has no stage field | Reject (cannot validate) |
| Feature stage is null | Reject (cannot validate) |
| Feature stage is unknown value | Reject (not in allowed list) |

### 2.4 Agent Busy State Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Plan agent is idle, "plan" action requested | Allow |
| Plan agent is running, "plan" action requested | Reject with 409 |
| Implement agent is idle, "implement" action requested | Allow |
| Implement agent is running, "implement" action requested | Reject with 409 |
| Plan agent is running, "implement" action requested | Allow (different agent) |
| Implement agent is running, "plan" action requested | Allow (different agent) |
| Agent status is unknown/missing | Reject (assume busy for safety) |

### 2.5 Client Connection Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Client connected via WebSocket, then action requested | Command sent via WebSocket |
| Client disconnected after validation but before send | Command not sent (no error to client) |
| Client not connected at all | 503 Service Unavailable |
| Multiple requests for same action while agent starts | All reject with 409 after first starts |
| Client reconnects during request processing | Depends on server implementation (document) |

### 2.6 Feature State Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Feature ID does not exist in client's features list | Return 404 |
| Feature ID exists but feature data is malformed | Return 500 Internal Server Error |
| Multiple features have same ID | Use first match (undefined behavior) |
| Features list is empty | Return 404 for any feature request |
| Feature has no available_actions field | Reject (assume no actions available) |

### 2.7 Concurrent Operations

| Scenario | Expected Behavior |
|----------|-------------------|
| Two "plan" requests for different features simultaneously | Both processed (different features) |
| Two "plan" requests for same feature simultaneously | First succeeds, second gets 409 |
| "plan" and "implement" for same feature simultaneously | Both processed (different agents) |
| State update arrives while processing request | Use state at validation time |
| Rapid toggle of actions | Each validated independently |

### 2.8 WebSocket Message Handling

| Scenario | Expected Behavior |
|----------|-------------------|
| Malformed JSON in start_agent message | Log error, ignore message |
| Missing feature_id in message | Log error, ignore message |
| Missing action in message | Log error, ignore message |
| Client callback throws exception | Catch exception, log error |

---

## 3. What Constitutes Failure

### 3.1 Functional Failures

| Failure Condition | Criteria | Expected Error |
|-------------------|----------|----------------|
| Action button shows but should be disabled | Button enabled when action not in available_actions | UI displays incorrect state |
| Action allowed when stage doesn't permit | Request succeeds for disallowed stage | Agent starts incorrectly |
| Request rejected when it should succeed | 400/404/409 returned for valid request | User cannot perform valid action |
| Agent busy but action appears available | available_actions includes busy agent's action | UI shows incorrect availability |
| WebSocket message not delivered | Client doesn't receive start_agent message | Agent never starts |
| Wrong feature gets action | Action executed on different feature | Feature X receives feature Y's action |
| State push missing required fields | stage_actions or available_actions not in payload | Frontend cannot render buttons |

### 3.2 Error Message Requirements

| Scenario | Expected Error Message |
|----------|----------------------|
| Invalid action value | "Invalid action: {value}. Must be 'plan' or 'implement'" |
| Feature not found | "Feature {fid} not found in client {id}'s state" |
| Client not found | "Client {id} not found" |
| Stage not allowed | "Action '{action}' not allowed for stage '{stage}'" |
| Agent busy | "Agent '{action}' is currently busy" |
| Missing action in body | "Action field is required" |

### 3.3 Rollback Behavior

| Scenario | Rollback Requirement |
|----------|---------------------|
| Server validates but WebSocket fails to send | No rollback needed (nothing happened on client) |
| WebSocket sends but client fails to process | Agent does not start; no rollback needed |
| TUI callback throws exception | Agent does not start; exception logged |
| UI button clicked twice rapidly | First request processes, second returns 409 |

### 3.4 Data Integrity

| Failure Condition | Criteria |
|-------------------|----------|
| State corruption | After action completes, feature stage should update appropriately |
| Race condition | Concurrent requests should not cause inconsistent state |
| Memory leak | Long-running server should not accumulate memory from repeated actions |

---

## 4. Out of Scope

### 4.1 UI/Visual Testing

| Out of Scope Item | Reason |
|-------------------|--------|
| Button visual appearance (colors, fonts, sizing) | Visual design testing separate from functional testing |
| Button hover/active states | CSS/UI framework behavior |
| Animation timing for button transitions | UI timing not critical to functionality |
| Responsive layout on different screen sizes | UI layout testing |
| Button placement within feature card | UI layout testing |
| Loading spinner appearance | Visual testing |

### 4.2 Performance Testing

| Out of Scope Item | Reason |
|-------------------|--------|
| Request latency benchmarks | Performance requirements not specified |
| WebSocket throughput under load | Load testing out of scope |
| Memory usage during extended operation | Resource monitoring separate |
| CPU usage during agent execution | Agent performance separate from this feature |
| State push payload size optimization | Not critical for functional correctness |

### 4.3 Timing and Concurrency

| Out of Scope Item | Reason |
|-------------------|--------|
| Exact timing of button enable/disable after state change | Race condition not critical |
| WebSocket message delivery timing | Network-dependent |
| UI render timing after state update | Framework-dependent |
| Agent startup time | Agent implementation separate |

### 4.4 Integration with External Systems

| Out of Scope Item | Reason |
|-------------------|--------|
| Authentication/authorization at API level | Assumed to be handled by existing infrastructure |
| Rate limiting on API endpoint | Infrastructure concern |
| SSL/TLS certificate handling | Infrastructure concern |
| Network connectivity loss during request | Network reliability testing |

### 4.5 Agent Execution Details

| Out of Scope Item | Reason |
|-------------------|--------|
| What the plan agent actually does | Agent implementation tested separately |
| What the implement agent actually does | Agent implementation tested separately |
| Agent error handling and recovery | Agent implementation |
| Agent output logging | Agent implementation |
| Agent resource limits | Agent implementation |

### 4.6 TUI Behavior (Beyond Callback Integration)

| Out of Scope Item | Reason |
|-------------------|--------|
| TUI button keyboard shortcuts | Existing TUI functionality |
| TUI menu navigation | Existing TUI functionality |
| TUI display rendering | Existing TUI functionality |
| TUI auto-plan/auto-implement behavior | Existing functionality |

### 4.7 Edge Cases That Don't Require Testing

| Out of Scope Item | Reason |
|-------------------|--------|
| JSON injection in request body | Schema validation prevents this |
| Extremely long client/feature IDs | ID format is controlled |
| Unicode in action values | Validation rejects non-allowed values |
| Negative test for WebSocket reconnection | Client handles reconnections separately |

---

## 5. Test Environment Prerequisites

To execute these tests, the following must be in place:

1. **Go server** running with the new `start_agent` endpoint and WebSocket hub updated
2. **Python pipeline client** connected to the Go server with TUI
3. **Web UI** served by the Go server with action buttons implemented
4. **Test client** with at least one feature in various stages to test different scenarios
5. **Test tools**: Ability to send HTTP requests, inspect WebSocket messages, read state

---

## 6. Summary

This test specification covers the complete flow from web UI button click through server validation, WebSocket message delivery, and Python client callback execution. The focus is on verifying that valid requests succeed, invalid requests are rejected with appropriate errors, and the system correctly computes and displays available actions based on feature stage and agent busy state.

Key verification points:
- Stage-action configuration is shared between Python components
- Available actions are correctly computed considering both stage and agent status
- Server validates all conditions before forwarding commands
- Web UI correctly reflects available actions and handles user interaction
- Error cases return appropriate HTTP status codes with meaningful messages
