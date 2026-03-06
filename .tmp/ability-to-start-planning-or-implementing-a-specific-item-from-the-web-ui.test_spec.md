# Test Specification: Ability to Start Planning or Implementing a Specific Item from the Web UI

## Overview

This test specification validates the feature that allows users to trigger plan or implement agents on a specific feature from the web UI. The feature follows a specific flow: Web UI sends HTTP POST to Go server → Go validates the request → Go sends WebSocket command to Python client → Python client executes the action in the TUI.

---

## 1. Behaviors That MUST Be Verified

### 1.1 Stage-Actions Configuration in Python

**Behavior**: The `STAGE_ACTIONS` dictionary in `pipeline/state.py` correctly defines which actions are allowed at which stages.

**Success Criteria**:
- The `STAGE_ACTIONS` dict contains "plan" action mapped to stages: "plan-inbox", "reviewing-plan", "requested-input", "approved"
- The `STAGE_ACTIONS` dict contains "implement" action mapped to stages: "approved", "spec-writing"
- The configuration is accessible to other Python modules that need to reference it

### 1.2 TUI Uses Shared Stage-Actions Config

**Behavior**: The TUI refactors hardcoded stage checks to use the shared `STAGE_ACTIONS` from `state.py`.

**Success Criteria**:
- `action_plan_only()` checks stage against `STAGE_ACTIONS["plan"]` instead of hardcoded values
- `action_implement_only()` checks stage against `STAGE_ACTIONS["implement"]` instead of hardcoded values
- `_auto_plan_check()` uses `STAGE_ACTIONS["plan"]` for validation
- `_auto_implement_check()` uses `STAGE_ACTIONS["implement"]` for validation

### 1.3 State Push Includes Stage Actions and Available Actions

**Behavior**: The Python server_client push_state() includes stage_actions and computes available_actions for each feature.

**Success Criteria**:
- The `stage_actions` field in the pushed state contains the full `STAGE_ACTIONS` dictionary
- Each feature in the features list includes an `available_actions` array
- `available_actions` correctly reflects actions allowed by stage when respective agent is idle
- `available_actions` excludes an action when that agent is currently running (busy)
- When plan agent is running, "plan" is not in available_actions regardless of stage
- When implement agent is running, "implement" is not in available_actions regardless of stage

### 1.4 Start Agent Endpoint Validation

**Behavior**: The Go server correctly validates start_agent requests before executing.

**Success Criteria**:
- POST to `/api/clients/{id}/features/{fid}/start-agent` with valid action returns 200 OK
- Request with action "plan" on a feature in "approved" stage succeeds
- Request with action "implement" on a feature in "approved" stage succeeds
- Request with action "plan" on a feature in "in-progress" stage returns 400 Bad Request
- Request with action "implement" on a feature in "plan-inbox" stage returns 400 Bad Request
- Request for action "plan" when plan agent is already running returns 409 Conflict
- Request for action "implement" when implement agent is already running returns 409 Conflict
- Request with non-existent client ID returns 404 Not Found
- Request with non-existent feature ID returns 404 Not Found
- Request with invalid action value returns 400 Bad Request

### 1.5 WebSocket Command Sent to Client

**Behavior**: After successful validation, Go server sends a start_agent WebSocket message to the Python client.

**Success Criteria**:
- WebSocket message has type "start_agent"
- Message includes "feature_id" matching the requested feature
- Message includes "action" matching the requested action ("plan" or "implement")
- Message is only sent when all validation checks pass

### 1.6 Python Client Handles start_agent Message

**Behavior**: The Python server_client correctly handles incoming start_agent WebSocket messages.

**Success Criteria**:
- Handler recognizes msg_type "start_agent"
- Handler extracts feature_id from the message data
- Handler extracts action from the message data
- Handler triggers the on_start_agent callback when registered

### 1.7 TUI Executes Action from Callback

**Behavior**: The TUI correctly executes the requested action when the on_start_agent callback is triggered.

**Success Criteria**:
- PipelineClient registers on_start_agent callback during initialization
- Callback receives correct feature_id and action parameters
- For action "plan": calls _run_plan_async() for the specified feature
- For action "implement": calls _run_implement_async() for the specified feature
- Callback validates stage is allowed before executing (using STAGE_ACTIONS)
- Callback checks agent is not already busy before executing
- UI remains responsive during async execution

### 1.8 Web UI Renders Action Buttons

**Behavior**: The web UI correctly displays action buttons based on available_actions from state.

**Success Criteria**:
- Feature cards display "Start Planning" button when "plan" is in available_actions
- Feature cards display "Start Implementing" button when "implement" is in available_actions
- Buttons are disabled when available_actions is empty
- Buttons are disabled when action is not in available_actions
- Clicking a button sends POST request to correct endpoint
- Button shows loading/disabled state after click until agent starts

### 1.9 Go Hub Stores and Exposes Stage Actions

**Behavior**: The Go hub correctly stores and makes available the stage_actions and per-feature available_actions.

**Success Criteria**:
- ClientState struct includes StageActions map field
- ClientState parses stage_actions from state_update WebSocket message
- Each feature in ClientState includes AvailableActions array
- API response includes stage_actions for client
- API response includes available_actions for each feature

---

## 2. Edge Cases

### 2.1 Empty and Null Inputs

- Request with empty action string should be rejected with 400
- Request with null/missing action field should be rejected with 400
- State push with null stage_actions should default to empty dict
- Feature with null stage value should result in empty available_actions

### 2.2 Boundary Conditions

- Feature at exact boundary stage (first or last in allowed_stages list) should be correctly evaluated
- Client with zero features should receive valid state with empty features array
- Agent transitioning from busy to idle between state push and API request (race condition)

### 2.3 Invalid States

- Feature with unknown/invalid stage value should result in empty available_actions
- Client disconnected between API request and WebSocket send should return error to caller
- Multiple rapid start_agent requests for same feature should be handled gracefully (all but first should fail)
- Request to start plan when implement agent is running (but plan is idle) should succeed

### 2.4 Concurrent Operations

- Two simultaneous requests for different actions on same feature (one plan, one implement) - only one should succeed based on order
- WebSocket message in flight when client disconnects
- State update received while start_agent request is being processed
- User clicks button multiple times rapidly - should debounce or handle gracefully

### 2.5 Error Conditions

- WebSocket connection drops after API validates but before message sent
- Python client crashes after receiving start_agent command
- Feature gets deleted while action is queued
- Stage changes via another mechanism (TUI) while web UI button is enabled

---

## 3. What Constitutes Failure

### 3.1 Criteria for Test Failure

- Any API endpoint returns unexpected status code
- WebSocket message not sent after successful validation
- Wrong action executed for a given feature
- Action executes on feature at disallowed stage
- Action executes when respective agent is already busy
- Available_actions includes action that should be excluded (wrong stage or agent busy)
- Available_actions excludes action that should be included (correct stage and agent idle)
- Buttons appear when they should not or fail to appear when they should
- Duplicate actions start as a result of single button click

### 3.2 Error Messages Expected

- 400 Bad Request: "Invalid action for current stage" (stage not in allowed_stages)
- 400 Bad Request: "Invalid action value: {action}"
- 409 Conflict: "Plan agent is currently running"
- 409 Conflict: "Implement agent is currently running"
- 404 Not Found: "Client not found"
- 404 Not Found: "Feature not found"

### 3.3 Rollback Behavior

- If WebSocket send fails after validation, no change to agent state occurs
- If Python client fails to execute action, client remains in consistent state
- Failed requests do not leave any partial state on server or client

---

## 4. Out of Scope

The following items are explicitly NOT tested:

### 4.1 UI Timing and Visual Assertions

- Exact pixel positioning of buttons
- CSS styling and visual appearance
- Animation timing
- Button hover/active states
- Tooltip display timing
- Loading spinner animation details

### 4.2 Performance Benchmarks

- Request latency measurements
- WebSocket message throughput
- State push frequency
- UI render performance
- Memory usage under load

### 4.3 Network Conditions

- Slow network simulation
- Packet loss handling
- DNS resolution
- TLS/SSL handshake details

### 4.4 Concurrent User Scenarios

- Multiple web UI users controlling same client
- Session management across browser tabs
- Authentication/authorization for API endpoints

### 4.5 TUI Internal Behavior

- Exact rendering of TUI elements
- User interaction within TUI
- TUI keyboard shortcuts
- TUI scroll behavior

### 4.6 Long-Running State

- Agent running for extended periods
- Feature state persistence across restarts
- Database consistency

### 4.7 Edge Platform Cases

- Browser compatibility
- Mobile device layouts
- Touch vs mouse interactions
- Accessibility features
