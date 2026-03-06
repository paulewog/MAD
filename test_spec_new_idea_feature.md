# Test Specification: Add Ability to Create New Ideas from Server View

## Overview

This document specifies the test requirements for the "New Idea" feature, which allows users to create ideas through the web interface that are then forwarded to a connected pipeline client for local creation via `FeatureFile.create()`.

---

## 1. Behaviors That MUST Be Verified

### 1.1 REST Endpoint: `POST /api/clients/{id}/ideas`

**Behavior:** Server accepts a POST request with JSON body containing title, board, and description, then forwards a WebSocket message to the specified client.

**Success Criteria:**
- Endpoint accepts valid JSON with all required fields (title, board)
- Endpoint returns 200 OK when client is connected and message sent successfully
- WebSocket message sent to client contains the exact data from the REST request
- Response includes appropriate success indicator

**Expected Outputs:**
- HTTP 200 status code on success
- Response body contains created idea details or success confirmation
- WebSocket message payload matches: `{"type": "create_idea", "title": "...", "board": "...", "description": "..."}`

### 1.2 Client Validation

**Behavior:** Server validates that the target client exists and is connected before attempting to send a WebSocket message.

**Success Criteria:**
- Request to non-existent client ID returns 404 error
- Request to disconnected client returns appropriate error (client not connected)
- Request to connected client succeeds

**Expected Outputs:**
- HTTP 404 for non-existent client
- HTTP 503 or similar for disconnected client with descriptive error message

### 1.3 Input Validation

**Behavior:** Server validates that required fields are present and non-empty.

**Success Criteria:**
- Request with missing title returns 400 error
- Request with empty title returns 400 error
- Request with missing board returns 400 error
- Request with empty board returns 400 error
- Request with only title and board (no description) succeeds
- Request with all fields succeeds

**Expected Outputs:**
- HTTP 400 for missing/empty required fields
- Error message specifies which field is invalid

### 1.4 WebSocket Message Forwarding

**Behavior:** Server forwards the create_idea message to the correct connected client via WebSocket.

**Success Criteria:**
- Message delivered only to the specified client
- Message contains correct type: "create_idea"
- Message contains all fields from REST request
- No other clients receive the message

**Expected Outputs:**
- Client receives WebSocket message with exact payload structure

### 1.5 Pipeline Client Message Handling

**Behavior:** Pipeline client receives and processes the create_idea WebSocket message.

**Success Criteria:**
- Client correctly parses incoming message with type "create_idea"
- Client extracts title, board, and description from message
- Client invokes the on_idea_created callback with extracted data

**Expected Outputs:**
- Callback receives correct parameters: (board, title, description)

### 1.6 FeatureFile.create() Execution

**Behavior:** The callback invokes FeatureFile.create() with the correct arguments.

**Success Criteria:**
- FeatureFile.create() called with board name as first argument
- FeatureFile.create() called with title as second argument
- FeatureFile.create() called with description as third argument (or empty string if not provided)
- Method executes without errors

**Expected Outputs:**
- New idea file created in appropriate directory
- No exceptions thrown

### 1.7 State Push After Creation

**Behavior:** After idea creation, pipeline triggers a state push to update the web UI.

**Success Criteria:**
- State push occurs after FeatureFile.create() completes
- New idea appears in ideas stage on next poll
- UI reflects the newly created idea

**Expected Outputs:**
- Web UI shows new idea within polling interval (5 seconds)

### 1.8 Web UI Form Submission

**Behavior:** User can fill out and submit the New Idea form from client.html.

**Success Criteria:**
- Form displays fields: Title (required), Board (dropdown or text), Description (optional)
- Form prevents submission when required fields empty
- Form submits to correct endpoint with correct method (POST)
- Form includes authentication key in request

**Expected Outputs:**
- Network request sent to `/api/clients/{clientID}/ideas?key=...`
- Request body is valid JSON

### 1.9 Form Feedback

**Behavior:** User receives feedback on form submission success or failure.

**Success Criteria:**
- Success message displayed when idea created successfully
- Error message displayed when creation fails
- Form resets or closes after successful submission

**Expected Outputs:**
- Visible feedback to user within reasonable time

### 1.10 Aggregate View (index.html)

**Behavior:** Optional feature - New Idea button appears on aggregate dashboard.

**Success Criteria:**
- Button visible when at least one client connected
- If one client connected, defaults to that client
- If multiple clients connected, allows client selection
- Form functions same as client.html

**Expected Outputs:**
- Button visible only when clients exist
- Correct client targeted in request

---

## 2. Edge Cases

### 2.1 Client Disconnection During Request

**Scenario:** Client disconnects after REST endpoint receives request but before WebSocket message is sent.

**Expected Behavior:** Server detects client disconnected, returns appropriate error response to web UI.

**Test:** Verify HTTP error returned and no orphaned WebSocket messages.

### 2.2 Duplicate Title / Slug Collision

**Scenario:** User creates idea with title that generates same slug as existing idea.

**Expected Behavior:** FeatureFile.create() handles collision (existing behavior should append unique identifier or allow overwrite with different content).

**Test:** Create two ideas with same title, verify both exist with unique identifiers or content differentiation.

### 2.3 Empty Description

**Scenario:** User submits form with title and board but leaves description empty.

**Expected Behavior:** Idea created with empty string for description field.

**Test:** Verify idea created with description="" or description omitted.

### 2.4 Very Long Inputs

**Scenario:** User submits extremely long title, board name, or description.

**Expected Behavior:** System handles gracefully - either truncates, returns error, or accepts full length depending on implementation limits.

**Test:** Submit maximum length strings, verify no crashes and behavior matches specification.

### 2.5 Special Characters in Inputs

**Scenario:** Title, board, or description contain special characters (quotes, brackets, unicode, etc.).

**Expected Behavior:** Proper escaping/encoding in both JSON and file creation.

**Test:** Submit ideas with various special characters, verify stored correctly.

### 2.6 Concurrent Idea Creation

**Scenario:** Multiple users create ideas simultaneously for same or different clients.

**Expected Behavior:** All ideas created successfully without race conditions or data corruption.

**Test:** Submit multiple simultaneous requests, verify all succeed.

### 2.7 Invalid JSON in Request Body

**Scenario:** Request body is malformed JSON or not JSON at all.

**Expected Behavior:** Server returns 400 error with descriptive message.

**Test:** Send invalid JSON, verify error response.

### 2.8 Missing Authentication Key

**Scenario:** Request submitted without required key query parameter.

**Expected Behavior:** Server returns 401 or 403 error.

**Test:** Omit key parameter, verify authentication error.

### 2.9 Invalid Authentication Key

**Scenario:** Request submitted with incorrect or expired key.

**Expected Behavior:** Server returns 401 or 403 error.

**Test:** Send wrong key, verify authentication error.

### 2.10 Board Name with Spaces or Special Chars

**Scenario:** User provides board name containing spaces or special characters.

**Expected Behavior:** Board directory created with appropriate handling (escaped or quoted).

**Test:** Create idea with multi-word board name, verify file/directory created correctly.

### 2.11 Network Interruption After Submission

**Scenario:** Network fails after form submission but before response received.

**Expected Behavior:** UI shows appropriate error, does not display false success.

**Test:** Simulate network failure, verify error handling.

### 2.12 Client Reconnection During Request

**Scenario:** Disconnected client reconnects while request is being processed.

**Expected Behavior:** Server correctly identifies current connection state.

**Test:** Client disconnects, reconnects, then request sent, verify correct handling.

---

## 3. What Constitutes Failure

### 3.1 Success Criteria for Test Failure

A test fails when any of the following occurs:

**Functional Failures:**
- REST endpoint returns non-200 status for valid request
- WebSocket message not sent to correct client
- WebSocket message missing or has incorrect payload
- FeatureFile.create() not called with correct arguments
- New idea does not appear in UI after state push
- Form submission does not reach server (client-side error)

**Validation Failures:**
- Missing input validation (allows empty required fields)
- Client validation bypassed (allows request to non-existent client)
- Authentication bypassed (allows request without valid key)

**Data Integrity Failures:**
- Idea file not created in correct location
- Idea file contains incorrect or corrupted data
- Multiple concurrent requests cause data loss or corruption

**Error Handling Failures:**
- Server crashes on invalid input
- Server does not return appropriate error messages
- Client does not handle malformed WebSocket messages gracefully

### 3.2 Expected Error Messages

| Scenario | Expected Error Message |
|----------|----------------------|
| Client not found | "Client not found" or similar |
| Client disconnected | "Client not connected" or similar |
| Missing title | "Title is required" or similar |
| Missing board | "Board is required" or similar |
| Empty title | "Title cannot be empty" or similar |
| Empty board | "Board cannot be empty" or similar |
| Invalid JSON | "Invalid JSON body" or similar |
| Missing auth key | "Authentication required" or similar |
| Invalid auth key | "Invalid authentication" or similar |

### 3.3 Rollback Behavior

**Scenario:** Operation fails partway through.

**Expected Behavior:**
- If REST endpoint fails, no WebSocket message sent, no idea created
- If WebSocket send fails after validation, return error (idea not created on client)
- If FeatureFile.create() fails, error propagated back to UI
- No partial state - either fully succeeds or fully fails

**No automatic rollback needed** - failed operations should not leave partial state.

---

## 4. Out of Scope

The following are explicitly NOT tested in this specification:

### 4.1 UI/Visual Testing
- Visual layout and styling of the form
- Button placement or appearance
- Color schemes or visual feedback
- Modal or inline form rendering
- Responsive design on different screen sizes

### 4.2 Timing/Performance Testing
- Exact polling interval timing
- UI refresh timing after state push
- WebSocket message delivery latency
- Page load times
- Animation timing

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
- File system limits
- Disk space considerations
- Backup/restore functionality

### 4.6 FeatureFile Implementation Details
- Internal implementation of FeatureFile.create()
- File locking mechanisms
- Directory creation logic (beyond basic existence)
- Existing idea overwrite behavior

### 4.7 Security (Beyond Basic Auth)
- XSS prevention in inputs
- CSRF protection
- SQL injection (not applicable - no database)
- Rate limiting
- Input sanitization details

### 4.8 Concurrency Limits
- Maximum concurrent connections
- Maximum concurrent idea creations
- Queue depth limits

### 4.9 Logging and Monitoring
- Log message formats
- Metrics collection
- Tracing implementation

### 4.10 Documentation
- Help text in UI
- User guides
- API documentation

---

## Test Summary

This specification covers the critical path from web form submission through to idea creation and UI update, including validation, error handling, and edge cases. All functional requirements from the feature plan are verified, while explicitly scoped-out items focus on non-functional aspects that should be tested separately if needed.

**Key Verification Points:**
1. REST endpoint accepts and validates requests
2. WebSocket forwarding works correctly
3. Pipeline client processes messages properly
4. FeatureFile.create() is invoked with correct arguments
5. UI updates after state push
6. Errors are handled gracefully at all layers
