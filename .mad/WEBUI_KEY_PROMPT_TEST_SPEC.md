# WebUI Key Authentication - Test Specification

## Feature Summary
Modal overlay prompting for a dashboard key, stored in localStorage. Without a valid key, the backend must not send any actual data to the frontend. Covers dashboard page (`/`), client page (`/clients/{id}`), HTMX fragment endpoints, and API endpoints.

---

## 1. Behaviors That MUST Be Verified

### 1.1 Dashboard Page Authentication

**B1: Key modal renders for unauthenticated users when DashboardKey is configured**
- Given: `DashboardKey` is set in config
- When: User visits `/` with no `?key=` parameter
- Then: The HTML response contains the key modal (`id="key-modal"`) and the modal script block
- Success: Modal HTML is present in the response body inside a valid `<body>` tag, not hidden behind a conditional that prevents rendering

**B2: No client/feature data in response when unauthenticated**
- Given: `DashboardKey` is set, hub has registered clients with features
- When: User visits `/` without a valid key
- Then: The response body contains zero client rows and zero feature cards
- Success: Inspecting the raw HTML reveals no client IDs, feature titles, or feature data attributes

**B3: Dashboard shows data when authenticated**
- Given: `DashboardKey` is "secret123", hub has clients with features
- When: User visits `/?key=secret123`
- Then: Response includes client table rows and feature cards with actual data
- Success: Client IDs and feature titles appear in the response

**B4: No authentication required when DashboardKey is empty**
- Given: `DashboardKey` is "" (empty string)
- When: User visits `/` with no key parameter
- Then: Dashboard renders with full data, no modal shown
- Success: `ShowKeyModal` is false, `Authenticated` is true, data is present

### 1.2 Client Page Authentication

**B5: Client page shows key modal when unauthenticated**
- Given: `DashboardKey` is set, client "test-client" exists
- When: User visits `/clients/test-client` without a valid key
- Then: Key modal is rendered, and no feature/log data is present in the response
- Success: Response contains modal HTML but `ClientState` fields are empty/zero-value

**B6: Client page shows data when authenticated**
- Given: `DashboardKey` is "secret123", client "test-client" exists with features and logs
- When: User visits `/clients/test-client?key=secret123`
- Then: Full client data is rendered including features, logs, agent status
- Success: Feature cards and log entries appear in the response

**B7: Client page returns 404 for nonexistent client (regardless of auth)**
- Given: No client "nonexistent" in hub
- When: User visits `/clients/nonexistent?key=secret123`
- Then: 404 response
- Success: HTTP status is 404

### 1.3 Key Validation Endpoint

**B8: Validation endpoint returns valid=true for correct key**
- Given: `DashboardKey` is "secret123"
- When: GET `/api/auth/validate?key=secret123`
- Then: 200 response with `{"valid": true}`

**B9: Validation endpoint returns valid=false for wrong key**
- Given: `DashboardKey` is "secret123"
- When: GET `/api/auth/validate?key=wrongkey`
- Then: 401 response with `{"valid": false}`

**B10: Validation endpoint returns valid=true when no DashboardKey configured**
- Given: `DashboardKey` is ""
- When: GET `/api/auth/validate?key=anything`
- Then: 200 response with `{"valid": true}`

**B11: Validation endpoint returns valid=false when key param is missing**
- Given: `DashboardKey` is "secret123"
- When: GET `/api/auth/validate` (no key param)
- Then: 401 response with `{"valid": false}`

### 1.4 Invalid Key Error Feedback

**B12: Error message shown in modal for invalid key**
- Given: User enters wrong key in the modal and clicks Save
- When: Validation endpoint returns `{"valid": false}`
- Then: An error message ("Invalid key" or similar) is displayed inside the modal
- Success: Error element becomes visible, key is NOT saved to localStorage, page does NOT reload

**B13: Valid key saves and reloads**
- Given: User enters correct key in modal
- When: Validation endpoint returns `{"valid": true}`
- Then: Key is saved to localStorage, page reloads with key in URL
- Success: `localStorage.getItem('mad_key')` returns the entered key

### 1.5 Logout / Clear Key

**B14: Logout button visible when authenticated on dashboard**
- Given: User is authenticated on the dashboard
- Then: A logout button is present in the rendered HTML

**B15: Logout clears localStorage and reloads**
- Given: `mad_key` is stored in localStorage
- When: User clicks logout
- Then: `localStorage.removeItem('mad_key')` is called, URL params are cleared, page reloads
- Success: After reload, user sees the key modal again

**B16: Logout button on client page**
- Given: User is authenticated on a client page
- Then: A logout button is present and functions the same as on the dashboard

### 1.6 HTMX Endpoint Protection

**B17: `/api/clients` HTMX requests require dashboard key**
- Given: `DashboardKey` is set
- When: HTMX request (with `HX-Request: true` header) to `/api/clients` without valid key
- Then: 401 Unauthorized response
- Success: HTTP status is 401

**B18: `/api/clients` HTMX requests succeed with valid dashboard key**
- Given: `DashboardKey` is "secret123"
- When: HTMX request to `/api/clients?key=secret123` with `HX-Request: true`
- Then: 200 response with HTML fragment containing client rows

**B19: `/api/board` requires dashboard key**
- Given: `DashboardKey` is set
- When: GET `/api/board` without valid key
- Then: 401 Unauthorized

**B20: `/api/board` succeeds with valid dashboard key**
- Given: `DashboardKey` is "secret123"
- When: GET `/api/board?key=secret123`
- Then: 200 response with board HTML fragment

**B21: Client HTMX fragment endpoints enforce auth**
- Given: `DashboardKey` is set, client exists
- When: GET `/clients/test-client/features` without valid key
- Then: 401 Unauthorized response (not empty data)
- Success: HTTP status is 401

**B22: Client HTMX fragment endpoints work with valid key**
- Given: `DashboardKey` is set, client has features
- When: GET `/clients/test-client/features?key=secret123`
- Then: 200 response with feature stage HTML

### 1.7 API Endpoint Protection

**B23: `/api/clients/{id}/features/{fid}/answers` requires dashboard key or API key**
- Given: Both `DashboardKey` and `APIKey` are set
- When: POST without any valid key
- Then: 401 Unauthorized

**B24: `/api/clients/{id}/auto-mode` requires dashboard key or API key**
- Given: Both keys configured
- When: POST without valid key
- Then: 401 Unauthorized

**B25: `/api/clients/{id}/ideas` requires dashboard key or API key**
- Given: Both keys configured
- When: POST without valid key
- Then: 401 Unauthorized

**B26: `/api/clients/{id}/features/{fid}/start-agent` requires dashboard key or API key**
- Given: Both keys configured
- When: POST without valid key
- Then: 401 Unauthorized

**B27: JSON API `/api/clients/{id}` requires API key or dashboard key**
- Given: Both keys configured
- When: GET without valid key
- Then: 401 Unauthorized

**B28: API endpoints accept dashboard key as alternative to API key**
- Given: `APIKey` is "api-secret", `DashboardKey` is "dash-secret"
- When: POST to any protected endpoint with `?key=dash-secret`
- Then: Request is authorized (assuming other validations pass)

### 1.8 Key Propagation in UI

**B29: Links to client pages include key parameter**
- Given: User is authenticated with key "secret123"
- When: Dashboard renders client table
- Then: Client links use href `/clients/{id}?key=secret123`

**B30: HTMX polling URLs include key parameter**
- Given: User is authenticated
- Then: HTMX `hx-get` attributes on auto-polling elements include `?key=...`

**B31: "Back to Dashboard" link on client page includes key**
- Given: User views client page with valid key
- Then: The back link href is `/?key=secret123`

### 1.9 localStorage Handling

**B32: Key retrieval works when localStorage is unavailable**
- Given: localStorage throws on access (private browsing mode)
- When: Page loads with `?key=mykey` in URL
- Then: Key is read from URL parameter without uncaught exceptions

**B33: saveKey gracefully handles localStorage failure**
- Given: localStorage throws on `setItem`
- When: User enters key and clicks Save
- Then: No uncaught exceptions; page attempts to reload with key

---

## 2. Edge Cases

### 2.1 Key Values

**E1: Empty string key** - `?key=` (empty value) is treated as unauthenticated. The code checks `key != ""` first, so empty key correctly fails.

**E2: Key with special characters** - Key containing `&`, `=`, `<`, `>`, `"`, spaces, unicode must be properly URL-encoded in query params and HTML-escaped in template output.

**E3: Key with very long value** - 10,000+ character key should not crash the server.

**E4: Multiple key parameters in URL** - `/?key=wrong&key=correct` — Go's `Query().Get()` returns the first value, so the first (wrong) key is used.

### 2.2 State Transitions

**E5: Key becomes invalid mid-session** - Server restarts with different DashboardKey while user has old key in localStorage. Next HTMX poll returns 401; modal should appear or error displayed.

**E6: Client disconnects while user views client page** - Page still renders with stale data, status shows "Disconnected".

### 2.3 Concurrent Access

**E7: Multiple browser tabs** - Each tab's auth is independent based on URL params. All tabs share the same localStorage key.

### 2.4 Template Rendering

**E8: Dashboard with zero clients and valid auth** - Empty table with "No clients connected" message renders without error.

**E9: Client page with zero features/logs** - Empty state messages rendered, no template errors.

**E10: HTML validity when unauthenticated** - The response must be valid HTML with proper `<html><head></head><body>...</body></html>` structure even when unauthenticated. The current bug has `</head><body>` inside the `{{if .Authenticated}}` block.

---

## 3. What Constitutes Failure

### 3.1 Critical Failures (Security)

**F1: Data leakage** - Any client ID, feature title, feature ID, log entry, or board name appears in the HTML response when unauthenticated and DashboardKey is configured. This is the single most critical failure.

**F2: Auth bypass on API endpoints** - Any API endpoint returns 200 with data when neither valid DashboardKey nor APIKey is provided (and at least one is configured).

**F3: Key modal not rendering** - DashboardKey is configured, user has no valid key, but modal HTML is absent from response or page is blank/malformed.

### 3.2 Functional Failures

**F4: Key not persisted** - After entering a valid key, `localStorage.getItem('mad_key')` does not return the key.

**F5: Polling breaks auth** - HTMX auto-polling requests return 401 when user has valid key, or return data when they don't.

**F6: No error feedback** - Wrong key entered, Save clicked, page reloads without showing error.

**F7: Logout incomplete** - After logout, key remains in localStorage or URL and user stays authenticated.

**F8: Malformed HTML** - Unauthenticated response has `</head>` and `<body>` missing, resulting in modal rendering in `<head>` (invisible).

### 3.3 Expected Error Responses

| Scenario | Expected Status | Expected Body |
|----------|----------------|---------------|
| Missing key on API endpoint | 401 | `{"error": "unauthorized"}` |
| Invalid key on validation | 401 | `{"valid": false}` |
| Valid key on validation | 200 | `{"valid": true}` |
| Client not found | 404 | `{"error": "client not found"}` or HTTP 404 page |
| Missing key on HTMX endpoint | 401 | "unauthorized" text |

---

## 4. Out of Scope

- Visual/CSS assertions: modal styling, colors, fonts, layout
- Animation timing: modal transitions, fade effects
- Performance benchmarks: page load times, polling latency
- Browser compatibility: cross-browser testing
- WebSocket authentication for pipeline client connections (uses APIKey, separate concern)
- Service worker behavior: caching, offline mode
- Mobile responsiveness
- Rate limiting on auth validation endpoint
- HTTPS/TLS transport security
- CORS headers
- Content Security Policy
- Actual browser localStorage API behavior (test application logic, not browser internals)
- Keyboard navigation within modals (beyond Enter-to-submit which is already implemented)
