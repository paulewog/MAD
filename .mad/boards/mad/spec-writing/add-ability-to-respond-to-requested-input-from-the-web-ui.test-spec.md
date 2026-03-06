# Test Spec: Answer Planning Questions from Web UI

## 1. Behaviors That MUST Be Verified

### 1.1 Server-to-Client Message Protocol

**Behavior:** The server can send an `answer_questions` message to a connected Python client via WebSocket.

- When the server sends `{"type": "answer_questions", "feature_id": "abc123", "answers": [...]}`, the client receives and parses it correctly.
- The message must include `type`, `feature_id`, and `answers` fields.
- `answers` is an array of objects, each with `question` (string) and `answer` (string).
- Success: client's receive loop dispatches the message to the answer handler without error.

### 1.2 REST Endpoint: POST /api/clients/{id}/features/{fid}/answers

**Behavior:** A new endpoint accepts answer submissions from the web UI and routes them to the correct client.

- A POST with valid client ID, feature ID, and answers array returns 200.
- The response body confirms the answers were sent.
- The endpoint sends an `answer_questions` WebSocket message to the target client.
- Success: round-trip from HTTP POST to WebSocket delivery completes; client receives the answers.

**Validation behavior:**
- If `{id}` does not match any connected client, return 404 with an error message indicating client not found.
- If `{id}` matches a known but currently disconnected client, return 404 (or 503) indicating client not connected.
- If `{fid}` does not match any feature on the client, return 404 indicating feature not found.
- If the feature exists but is NOT in `requested-input` stage, return 422 with a message indicating the feature is not awaiting input.
- If the request body is missing or malformed JSON, return 400.
- If the `answers` array is empty, return 400.
- Authentication is required (API key or dashboard key) — unauthenticated requests return 401/403.

### 1.3 Hub: Send-to-Client Capability

**Behavior:** The Hub can route a message TO a specific client by client ID.

- Given a client ID and a JSON message, the Hub writes the message to that client's `send` channel.
- The client's `writePump` picks it up and sends it over the WebSocket.
- If the client ID doesn't exist in the Hub's registry, the method returns an error (not a panic or silent failure).
- If the client's send channel is full (buffer exhausted), the Hub handles it gracefully (returns error or drops with logging, does not block indefinitely).

### 1.4 Python Client: Receive Loop and Answer Processing

**Behavior:** The Python WebSocket client listens for incoming messages and processes `answer_questions` commands.

- The client runs a receive loop concurrently with the existing send/push logic.
- When an `answer_questions` message arrives:
  1. The client looks up the feature by `feature_id`.
  2. For each answer in the array, it calls `feature.answer_question(index, answer_text)` matching by question text.
  3. The feature is moved to `plan-inbox` stage.
  4. A history entry is added noting answers were received from web UI.
- Success: after processing, the feature JSON file on disk has the answers populated and stage is `plan-inbox`.

**Callback/notification:**
- After answers are applied, the client invokes a callback (or signals an event) so the TUI or pipeline can react.
- If the TUI is running, it refreshes to show the feature has moved back to `plan-inbox`.

### 1.5 State Updates Include Questions

**Behavior:** The `state_update` message pushed by the Python client to the server includes the `questions` field for each feature.

- Each feature summary in the state update contains a `questions` array.
- Questions with empty answers show `"answer": ""`.
- Questions with provided answers include the full answer text.
- The Go server's `FeatureSummary` struct stores and serves the questions data.
- The REST endpoint `GET /api/clients/{id}` returns features with their questions.

### 1.6 Web UI: Question-Answering Form

**Behavior:** The client detail page displays questions for features in `requested-input` stage and allows submitting answers.

- For each feature in `requested-input` stage, the page shows:
  - The feature title.
  - Each question as a labeled input field.
  - Any existing answer pre-filled in the input.
  - A "Submit Answers" button.
- Clicking "Submit Answers" sends a POST to `/api/clients/{id}/features/{fid}/answers` via JavaScript fetch().
- On success (200): the UI shows a success message and the form disables or updates.
- On error (4xx): the UI shows the error message from the response body.
- Features NOT in `requested-input` stage do not show the answer form.

### 1.7 Auto-Plan Trigger After Answers

**Behavior:** When a feature moves from `requested-input` back to `plan-inbox` via web-submitted answers, the pipeline picks it up for re-planning.

- The existing auto-plan loop detects the feature in `plan-inbox` and runs `run_planning()`.
- The planning prompt includes the "Previous Answers" section with the newly submitted answers.
- The agent can then produce a refined plan or ask follow-up questions (repeating the cycle).

---

## 2. Edge Cases

### 2.1 Empty and Null Inputs

- **Empty answers array in POST body:** Server returns 400, does not send WebSocket message.
- **Answer with empty string value:** Accepted — an answer of `""` is treated as "explicitly left blank" and overwrites nothing (or is skipped). Only non-empty answers overwrite.
- **Null/missing fields in answer objects:** Server returns 400 if `question` or `answer` key is missing from any answer object.
- **Feature with no questions:** If a feature is in `requested-input` but somehow has an empty questions list, the endpoint returns 422 (nothing to answer).

### 2.2 Boundary Conditions

- **Very long answer text:** Answers up to a reasonable limit (e.g., 5000 chars) are accepted. Answers beyond the limit are rejected or truncated by the server.
- **Many questions:** A feature with a large number of questions (e.g., 20+) is handled without issue — all answers are applied.
- **Single answer out of many:** Submitting answers for only some questions is allowed (partial answers). Only non-empty submitted answers overwrite existing values. Unanswered questions retain their previous state.
- **Answer text with special characters:** JSON special characters, unicode, newlines, HTML entities are all preserved correctly through the round-trip (POST -> WebSocket -> file write -> state push -> UI display).

### 2.3 Invalid States

- **Feature moved out of `requested-input` between page load and submit:** Server validates stage at submit time and returns 422 if the feature is no longer in `requested-input`.
- **Feature deleted between page load and submit:** Server returns 404 for unknown feature ID.
- **Client disconnects between page load and submit:** Server returns 404 (client not connected). Web UI shows appropriate error.
- **Client reconnects with different features:** After reconnect, the server has fresh state from the new `state_update`. Old feature IDs that no longer exist are rejected.

### 2.4 Concurrent Operations

- **TUI and web UI both answer simultaneously:** Last write wins. Both use `feature.answer_question()` which writes to the same JSON file. The file-level locking prevents corruption, but the last writer's answers persist.
- **Multiple web UI tabs submit for the same feature:** First submission succeeds and moves feature to `plan-inbox`. Second submission gets 422 because feature is no longer in `requested-input`.
- **State push happens while answers are being written:** The push reads feature state from disk; if answers are mid-write, the file lock ensures the push reads either the pre-answer or post-answer state, never a partial write.
- **Planning agent starts while answers are in-flight:** If the feature reaches `plan-inbox` and planning starts before the state push reflects the change, no conflict — planning reads from disk, not from cached state.

### 2.5 Error Conditions

- **WebSocket connection drops during answer delivery:** The server's write to the client's send channel may succeed, but the writePump fails to deliver. The client never receives the answers. The web UI got a 200 (server successfully queued the message), but answers are lost. The user must resubmit after the client reconnects.
- **Python client crashes while processing answers:** The feature may be in a partially-updated state. On restart, the client re-reads feature files from disk. If answers were written but stage wasn't moved, the feature stays in `requested-input` with partial answers — the user can resubmit or answer from TUI.
- **Malformed WebSocket message received by Python client:** The receive loop logs the error and continues listening. It does not crash or disconnect.
- **Feature file locked by another process:** The answer handler waits for the lock (or retries). It does not silently drop answers.

---

## 3. What Constitutes Failure

### 3.1 Critical Failures (test MUST fail)

- Answers submitted via web UI are never received by the Python client.
- Answers are received but not written to the feature's JSON file on disk.
- Feature does not move from `requested-input` to `plan-inbox` after answers are applied.
- Server sends answers to the wrong client (client ID mismatch).
- Server returns 200 for a POST to a disconnected client.
- Server returns 200 for a POST to a feature not in `requested-input` stage.
- Python client's receive loop crashes and does not recover.
- WebSocket connection is broken by adding the receive loop (regression).
- State updates stop including feature data after the questions field is added (serialization regression).
- Questions field is missing from state updates for features that have questions.

### 3.2 Data Integrity Failures

- Answer text is corrupted, truncated, or escaped incorrectly during transmission.
- Question-answer index mapping is wrong (answer applied to wrong question).
- Feature JSON file is left in an invalid state (malformed JSON, missing required fields).
- History entry for "answers received" is missing or has wrong metadata.

### 3.3 Expected Error Responses

| Scenario | Expected Status | Expected Body Contains |
|----------|----------------|----------------------|
| Unknown client ID | 404 | "client not found" or "not connected" |
| Unknown feature ID | 404 | "feature not found" |
| Feature not in requested-input | 422 | "not in requested-input" or "not awaiting input" |
| Malformed JSON body | 400 | "invalid" or "malformed" |
| Empty answers array | 400 | "empty" or "no answers" |
| Missing auth | 401 or 403 | "unauthorized" or "forbidden" |

### 3.4 Rollback Behavior

- If the Python client fails to move the feature to `plan-inbox` after writing answers, the answers should still be persisted (partial success is better than full rollback — answers can be re-read on retry).
- If the server fails to deliver the WebSocket message after accepting the POST, no rollback is needed on the server side, but the web UI should be informed if possible (or the user resubmits).

---

## 4. Out of Scope

The following are explicitly NOT covered by this test spec:

- **Visual/CSS assertions:** Layout, styling, colors, spacing of the answer form in the web UI.
- **HTMX polling timing:** Whether the 5-second refresh interval is optimal or whether the UI updates "fast enough."
- **Performance benchmarks:** Latency of WebSocket message delivery, throughput under load, memory usage.
- **Browser compatibility:** Whether the JavaScript fetch() call works across all browsers.
- **Planning agent quality:** Whether the agent produces better plans after receiving answers — only that answers are included in the prompt.
- **WebSocket reconnection timing:** The exact backoff intervals or reconnection behavior (already tested elsewhere).
- **TUI rendering:** Whether the TUI correctly re-renders widgets after refresh — only that the refresh is triggered.
- **Multi-board scenarios:** Answering questions for features on different boards simultaneously.
- **Server horizontal scaling:** Multiple server instances sharing WebSocket connections.
- **File system edge cases:** Disk full, permission denied, NFS latency — these are infrastructure concerns.
- **Load testing:** Many clients submitting answers simultaneously.
- **Upgrade/migration:** Existing features without the questions field in their JSON files (backward compat of stored data is a separate concern).
