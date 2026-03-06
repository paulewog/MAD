package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func testConfig(apiKey string) *Config {
	return &Config{
		Port:        0,
		APIKey:      apiKey,
		MaxLogLines: 1000,
	}
}

func setupServer(cfg *Config) (*Hub, *http.ServeMux) {
	hub := NewHub(cfg)
	go hub.Run()
	mux := http.NewServeMux()
	registerRoutes(mux, hub, cfg)
	return hub, mux
}

func startTestServer(cfg *Config) (*Hub, *httptest.Server) {
	hub, mux := setupServer(cfg)
	ts := httptest.NewServer(mux)
	return hub, ts
}

func startTestServerT(t *testing.T, cfg *Config) (*Hub, *httptest.Server) {
	t.Helper()
	hub, ts := startTestServer(cfg)
	t.Cleanup(func() {
		ts.CloseClientConnections()
		ts.Close()
		time.Sleep(50 * time.Millisecond) // let goroutines drain
	})
	return hub, ts
}

func wsURL(ts *httptest.Server, path string) string {
	return "ws" + strings.TrimPrefix(ts.URL, "http") + path
}

func wsConnect(t *testing.T, ts *httptest.Server, apiKey, clientID string) *websocket.Conn {
	t.Helper()
	url := wsURL(ts, "/ws")
	if apiKey != "" {
		url += "?api_key=" + apiKey
	}
	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatalf("ws dial failed: %v", err)
	}
	// Register
	reg := map[string]string{
		"type":      "register",
		"client_id": clientID,
		"api_key":   apiKey,
	}
	if err := conn.WriteJSON(reg); err != nil {
		t.Fatalf("ws write register: %v", err)
	}
	// Read ack
	var resp map[string]string
	if err := conn.ReadJSON(&resp); err != nil {
		t.Fatalf("ws read ack: %v", err)
	}
	if resp["type"] != "ack" {
		t.Fatalf("expected ack, got %v", resp)
	}
	return conn
}

func waitForClient(hub *Hub, id string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if _, ok := hub.GetClient(id); ok {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return false
}

func waitForClientGone(hub *Hub, id string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if _, ok := hub.GetClient(id); !ok {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return false
}

func apiGet(t *testing.T, ts *httptest.Server, path, apiKey string) *http.Response {
	t.Helper()
	req, _ := http.NewRequest("GET", ts.URL+path, nil)
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET %s failed: %v", path, err)
	}
	return resp
}

// ---------------------------------------------------------------------------
// Config Tests
// ---------------------------------------------------------------------------

func TestConfigDefaults(t *testing.T) {
	// Unset env vars for this test
	t.Setenv("SERVER_PORT", "")
	t.Setenv("SERVER_API_KEY", "")
	t.Setenv("SERVER_MAX_LOG_LINES", "")

	cfg := loadConfig()
	if cfg.Port != 8080 {
		t.Errorf("default port = %d, want 8080", cfg.Port)
	}
	if cfg.APIKey != "" {
		t.Errorf("default api key = %q, want empty", cfg.APIKey)
	}
	if cfg.MaxLogLines != 1000 {
		t.Errorf("default max log lines = %d, want 1000", cfg.MaxLogLines)
	}
}

func TestConfigFromEnv(t *testing.T) {
	t.Setenv("SERVER_PORT", "9999")
	t.Setenv("SERVER_API_KEY", "test-key-123")
	t.Setenv("SERVER_MAX_LOG_LINES", "500")

	cfg := loadConfig()
	if cfg.Port != 9999 {
		t.Errorf("port = %d, want 9999", cfg.Port)
	}
	if cfg.APIKey != "test-key-123" {
		t.Errorf("api key = %q, want test-key-123", cfg.APIKey)
	}
	if cfg.MaxLogLines != 500 {
		t.Errorf("max log lines = %d, want 500", cfg.MaxLogLines)
	}
}

func TestConfigInvalidPort(t *testing.T) {
	// loadConfig calls os.Exit(1) on invalid port - we can't easily test that
	// without subprocess, so test the env parsing logic indirectly
	t.Setenv("SERVER_PORT", "")
	t.Setenv("SERVER_API_KEY", "")
	cfg := loadConfig()
	if cfg.Port != 8080 {
		t.Errorf("expected default port 8080, got %d", cfg.Port)
	}
}

func TestConfigMaxLogLinesInvalidIgnored(t *testing.T) {
	t.Setenv("SERVER_PORT", "")
	t.Setenv("SERVER_API_KEY", "")
	t.Setenv("SERVER_MAX_LOG_LINES", "notanumber")

	cfg := loadConfig()
	if cfg.MaxLogLines != 1000 {
		t.Errorf("invalid max log lines should fall back to 1000, got %d", cfg.MaxLogLines)
	}
}

// ---------------------------------------------------------------------------
// Hub Tests
// ---------------------------------------------------------------------------

func TestHubNewHub(t *testing.T) {
	cfg := testConfig("key")
	hub := NewHub(cfg)
	if hub == nil {
		t.Fatal("NewHub returned nil")
	}
	if len(hub.clients) != 0 {
		t.Errorf("new hub should have 0 clients, got %d", len(hub.clients))
	}
}

func TestHubListClientsEmpty(t *testing.T) {
	cfg := testConfig("key")
	hub := NewHub(cfg)
	clients := hub.ListClients()
	if clients == nil {
		t.Fatal("ListClients returned nil, want empty slice")
	}
	if len(clients) != 0 {
		t.Errorf("expected 0 clients, got %d", len(clients))
	}
}

func TestHubGetClientUnknown(t *testing.T) {
	cfg := testConfig("key")
	hub := NewHub(cfg)
	_, ok := hub.GetClient("nonexistent")
	if ok {
		t.Error("GetClient should return false for unknown client")
	}
}

func TestHubRegisterAndUnregister(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "client-1")
	if !waitForClient(hub, "client-1", 2*time.Second) {
		t.Fatal("client-1 not registered")
	}

	state, ok := hub.GetClient("client-1")
	if !ok {
		t.Fatal("GetClient failed for client-1")
	}
	if !state.Connected {
		t.Error("client should be connected")
	}
	if state.ClientID != "client-1" {
		t.Errorf("client_id = %q, want client-1", state.ClientID)
	}

	conn.Close()
	if !waitForClientGone(hub, "client-1", 2*time.Second) {
		t.Error("client-1 should be removed after disconnect")
	}
}

func TestHubMultipleClients(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn1 := wsConnect(t, ts, "", "client-a")
	conn2 := wsConnect(t, ts, "", "client-b")
	conn3 := wsConnect(t, ts, "", "client-c")
	defer conn1.Close()
	defer conn2.Close()
	defer conn3.Close()

	waitForClient(hub, "client-a", 2*time.Second)
	waitForClient(hub, "client-b", 2*time.Second)
	waitForClient(hub, "client-c", 2*time.Second)

	clients := hub.ListClients()
	if len(clients) != 3 {
		t.Errorf("expected 3 clients, got %d", len(clients))
	}
}

func TestHubDuplicateClientIDReplacesOld(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn1 := wsConnect(t, ts, "", "dup-client")
	waitForClient(hub, "dup-client", 2*time.Second)

	// Second connection with same ID
	conn2 := wsConnect(t, ts, "", "dup-client")
	time.Sleep(200 * time.Millisecond) // let hub process

	clients := hub.ListClients()
	count := 0
	for _, c := range clients {
		if c.ClientID == "dup-client" {
			count++
		}
	}
	if count != 1 {
		t.Errorf("expected exactly 1 entry for dup-client, got %d", count)
	}

	// Old connection should be closed
	err := conn1.WriteMessage(websocket.TextMessage, []byte("test"))
	if err == nil {
		// Give it a moment - the old conn might not have errored on write yet
		time.Sleep(100 * time.Millisecond)
	}

	conn2.Close()
}

// ---------------------------------------------------------------------------
// Message Handling Tests
// ---------------------------------------------------------------------------

func TestStateUpdateStoresFeatures(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "feat-client")
	defer conn.Close()
	waitForClient(hub, "feat-client", 2*time.Second)

	msg := map[string]interface{}{
		"type": "state_update",
		"features": []map[string]string{
			{"title": "Feature 1", "stage": "implementing", "board": "default", "created": "2025-01-01", "id": "f1"},
			{"title": "Feature 2", "stage": "testing", "board": "default", "created": "2025-01-02", "id": "f2"},
		},
	}
	conn.WriteJSON(msg)
	time.Sleep(200 * time.Millisecond)

	state, ok := hub.GetClient("feat-client")
	if !ok {
		t.Fatal("client not found")
	}
	if len(state.Features) != 2 {
		t.Fatalf("expected 2 features, got %d", len(state.Features))
	}
	if state.Features[0].Title != "Feature 1" {
		t.Errorf("feature title = %q, want Feature 1", state.Features[0].Title)
	}
}

func TestStateUpdateReplacesFeatures(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "replace-client")
	defer conn.Close()
	waitForClient(hub, "replace-client", 2*time.Second)

	// First update
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]string{
			{"title": "Old Feature", "stage": "ideas", "board": "b", "id": "old"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	// Second update replaces
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]string{
			{"title": "New Feature", "stage": "done", "board": "b", "id": "new"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	state, _ := hub.GetClient("replace-client")
	if len(state.Features) != 1 {
		t.Fatalf("expected 1 feature after replace, got %d", len(state.Features))
	}
	if state.Features[0].Title != "New Feature" {
		t.Errorf("feature should be replaced, got %q", state.Features[0].Title)
	}
}

func TestStateUpdateEmptyFeatures(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "empty-feat")
	defer conn.Close()
	waitForClient(hub, "empty-feat", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type":     "state_update",
		"features": []map[string]string{},
	})
	time.Sleep(100 * time.Millisecond)

	state, _ := hub.GetClient("empty-feat")
	if state.Features == nil {
		t.Error("features should not be nil")
	}
	if len(state.Features) != 0 {
		t.Errorf("expected 0 features, got %d", len(state.Features))
	}
}

func TestLogEntriesAppendAndCap(t *testing.T) {
	cfg := testConfig("")
	cfg.MaxLogLines = 5 // small cap for testing
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "log-client")
	defer conn.Close()
	waitForClient(hub, "log-client", 2*time.Second)

	// Send 3 logs
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"logs": []map[string]string{
			{"timestamp": "t1", "phase": "plan", "output": "log1"},
			{"timestamp": "t2", "phase": "plan", "output": "log2"},
			{"timestamp": "t3", "phase": "plan", "output": "log3"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	state, _ := hub.GetClient("log-client")
	if len(state.Logs) != 3 {
		t.Fatalf("expected 3 logs, got %d", len(state.Logs))
	}

	// Send 4 more (total 7, cap is 5)
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"logs": []map[string]string{
			{"timestamp": "t4", "phase": "impl", "output": "log4"},
			{"timestamp": "t5", "phase": "impl", "output": "log5"},
			{"timestamp": "t6", "phase": "test", "output": "log6"},
			{"timestamp": "t7", "phase": "test", "output": "log7"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	state, _ = hub.GetClient("log-client")
	if len(state.Logs) != 5 {
		t.Fatalf("expected 5 logs (capped), got %d", len(state.Logs))
	}
	// Oldest should be dropped
	if state.Logs[0].Output != "log3" {
		t.Errorf("oldest log should be log3, got %q", state.Logs[0].Output)
	}
	if state.Logs[4].Output != "log7" {
		t.Errorf("newest log should be log7, got %q", state.Logs[4].Output)
	}
}

func TestLogBufferExactlyAtCap(t *testing.T) {
	cfg := testConfig("")
	cfg.MaxLogLines = 3
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "cap-client")
	defer conn.Close()
	waitForClient(hub, "cap-client", 2*time.Second)

	// Send exactly 3
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"logs": []map[string]string{
			{"timestamp": "t1", "phase": "a", "output": "l1"},
			{"timestamp": "t2", "phase": "a", "output": "l2"},
			{"timestamp": "t3", "phase": "a", "output": "l3"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	state, _ := hub.GetClient("cap-client")
	if len(state.Logs) != 3 {
		t.Fatalf("expected 3 logs at cap, got %d", len(state.Logs))
	}

	// Send 1 more
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"logs": []map[string]string{
			{"timestamp": "t4", "phase": "a", "output": "l4"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	state, _ = hub.GetClient("cap-client")
	if len(state.Logs) != 3 {
		t.Fatalf("expected 3 logs after overflow, got %d", len(state.Logs))
	}
	if state.Logs[0].Output != "l2" {
		t.Errorf("expected oldest to be l2 after drop, got %q", state.Logs[0].Output)
	}
}

func TestLogClientIDStamped(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "stamp-client")
	defer conn.Close()
	waitForClient(hub, "stamp-client", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"logs": []map[string]string{
			{"timestamp": "t1", "phase": "test", "output": "hello"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	state, _ := hub.GetClient("stamp-client")
	if len(state.Logs) != 1 {
		t.Fatal("expected 1 log")
	}
	if state.Logs[0].ClientID != "stamp-client" {
		t.Errorf("log client_id = %q, want stamp-client", state.Logs[0].ClientID)
	}
}

func TestMalformedJSONDoesNotDropConnection(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "malformed-client")
	defer conn.Close()
	waitForClient(hub, "malformed-client", 2*time.Second)

	// Send malformed JSON
	conn.WriteMessage(websocket.TextMessage, []byte("{not valid json"))
	time.Sleep(100 * time.Millisecond)

	// Connection should still be alive
	state, ok := hub.GetClient("malformed-client")
	if !ok {
		t.Fatal("client should still be connected after malformed JSON")
	}
	if !state.Connected {
		t.Error("client should still show connected")
	}

	// Verify we can still send valid messages
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]string{
			{"title": "After Malformed", "stage": "ideas", "board": "b", "id": "ok"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	state, _ = hub.GetClient("malformed-client")
	if len(state.Features) != 1 {
		t.Errorf("expected 1 feature after recovery, got %d", len(state.Features))
	}
}

func TestUnknownMessageTypeDiscarded(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "unknown-type")
	defer conn.Close()
	waitForClient(hub, "unknown-type", 2*time.Second)

	conn.WriteJSON(map[string]string{"type": "banana"})
	time.Sleep(100 * time.Millisecond)

	// Connection should still work
	_, ok := hub.GetClient("unknown-type")
	if !ok {
		t.Error("client should still be connected after unknown message type")
	}
}

func TestImmediateDisconnectBeforeMessage(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	url := wsURL(ts, "/ws")
	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatal(err)
	}
	// Close immediately without sending any message
	conn.Close()
	time.Sleep(500 * time.Millisecond)

	// Hub should not have any permanent record - the "unknown" client should be cleaned up
	clients := hub.ListClients()
	for _, c := range clients {
		if c.Connected {
			t.Errorf("no clients should remain connected, found %q", c.ClientID)
		}
	}
}

// ---------------------------------------------------------------------------
// Auth Tests - WebSocket
// ---------------------------------------------------------------------------

func TestWSAuthAcceptsValidKey(t *testing.T) {
	cfg := testConfig("secret")
	_, ts := startTestServerT(t, cfg)

	url := wsURL(ts, "/ws") + "?api_key=secret"
	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatalf("should accept valid api_key: %v", err)
	}
	conn.Close()
}

func TestWSAuthRejectsNoKey(t *testing.T) {
	cfg := testConfig("secret")
	_, ts := startTestServerT(t, cfg)

	url := wsURL(ts, "/ws")
	_, resp, err := websocket.DefaultDialer.Dial(url, nil)
	if err == nil {
		t.Fatal("should reject connection without API key")
	}
	if resp != nil && resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401", resp.StatusCode)
	}
}

func TestWSAuthRejectsWrongKey(t *testing.T) {
	cfg := testConfig("secret")
	_, ts := startTestServerT(t, cfg)

	url := wsURL(ts, "/ws") + "?api_key=wrong"
	_, resp, err := websocket.DefaultDialer.Dial(url, nil)
	if err == nil {
		t.Fatal("should reject connection with wrong API key")
	}
	if resp != nil && resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401", resp.StatusCode)
	}
}

func TestWSAuthViaHeader(t *testing.T) {
	cfg := testConfig("secret")
	_, ts := startTestServerT(t, cfg)

	url := wsURL(ts, "/ws")
	header := http.Header{}
	header.Set("Authorization", "Bearer secret")
	conn, _, err := websocket.DefaultDialer.Dial(url, header)
	if err != nil {
		t.Fatalf("should accept valid Bearer header: %v", err)
	}
	conn.Close()
}

func TestWSAuthNoKeyConfigMeansOpen(t *testing.T) {
	cfg := testConfig("")
	_, ts := startTestServerT(t, cfg)

	url := wsURL(ts, "/ws")
	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatalf("should accept when no API key configured: %v", err)
	}
	conn.Close()
}

func TestWSRegisterWithInvalidAPIKey(t *testing.T) {
	cfg := testConfig("secret")
	_, ts := startTestServerT(t, cfg)

	// Connect with valid key but register with invalid key
	url := wsURL(ts, "/ws") + "?api_key=secret"
	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	conn.WriteJSON(map[string]string{
		"type":      "register",
		"client_id": "client-bad",
		"api_key":   "wrong-key",
	})

	var resp map[string]string
	conn.ReadJSON(&resp)
	if resp["type"] != "error" {
		t.Errorf("expected error response, got %v", resp)
	}
}

// ---------------------------------------------------------------------------
// REST API Tests
// ---------------------------------------------------------------------------

func TestAPIClientsEmptyArray(t *testing.T) {
	cfg := testConfig("key")
	_, ts := startTestServerT(t, cfg)

	resp := apiGet(t, ts, "/api/clients", "key")
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	var clients []ClientState
	json.NewDecoder(resp.Body).Decode(&clients)
	if clients == nil {
		t.Fatal("response should not be null")
	}
	if len(clients) != 0 {
		t.Errorf("expected 0 clients, got %d", len(clients))
	}
}

func TestAPIClientsReturnsConnected(t *testing.T) {
	cfg := testConfig("key")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "key", "api-client")
	defer conn.Close()
	waitForClient(hub, "api-client", 2*time.Second)

	resp := apiGet(t, ts, "/api/clients", "key")
	defer resp.Body.Close()

	var clients []ClientState
	json.NewDecoder(resp.Body).Decode(&clients)
	if len(clients) != 1 {
		t.Fatalf("expected 1 client, got %d", len(clients))
	}
	if clients[0].ClientID != "api-client" {
		t.Errorf("client_id = %q, want api-client", clients[0].ClientID)
	}
}

func TestAPIClientByID(t *testing.T) {
	cfg := testConfig("key")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "key", "detail-client")
	defer conn.Close()
	waitForClient(hub, "detail-client", 2*time.Second)

	// Push some state
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]string{
			{"title": "F1", "stage": "ideas", "board": "b", "id": "f1"},
		},
		"logs": []map[string]string{
			{"timestamp": "t1", "phase": "test", "output": "hello"},
		},
	})
	time.Sleep(200 * time.Millisecond)

	resp := apiGet(t, ts, "/api/clients/detail-client", "key")
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	var state ClientState
	json.NewDecoder(resp.Body).Decode(&state)
	if state.ClientID != "detail-client" {
		t.Errorf("client_id = %q", state.ClientID)
	}
	if len(state.Features) != 1 {
		t.Errorf("expected 1 feature, got %d", len(state.Features))
	}
	if len(state.Logs) != 1 {
		t.Errorf("expected 1 log, got %d", len(state.Logs))
	}
}

func TestAPIClientByIDNotFound(t *testing.T) {
	cfg := testConfig("key")
	_, ts := startTestServerT(t, cfg)

	resp := apiGet(t, ts, "/api/clients/nonexistent", "key")
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Fatalf("status = %d, want 404", resp.StatusCode)
	}

	var body map[string]string
	json.NewDecoder(resp.Body).Decode(&body)
	if body["error"] != "client not found" {
		t.Errorf("error = %q, want 'client not found'", body["error"])
	}
}

func TestAPIClientLogs(t *testing.T) {
	cfg := testConfig("key")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "key", "log-api-client")
	defer conn.Close()
	waitForClient(hub, "log-api-client", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"logs": []map[string]string{
			{"timestamp": "t1", "phase": "test", "output": "line1"},
			{"timestamp": "t2", "phase": "test", "output": "line2"},
		},
	})
	time.Sleep(200 * time.Millisecond)

	resp := apiGet(t, ts, "/api/clients/log-api-client/logs", "key")
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	var logs []LogEntry
	json.NewDecoder(resp.Body).Decode(&logs)
	if len(logs) != 2 {
		t.Errorf("expected 2 logs, got %d", len(logs))
	}
}

func TestAPIClientLogsNotFound(t *testing.T) {
	cfg := testConfig("key")
	_, ts := startTestServerT(t, cfg)

	resp := apiGet(t, ts, "/api/clients/nope/logs", "key")
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
}

func TestAPIAuthRequired(t *testing.T) {
	cfg := testConfig("secret")
	_, ts := startTestServerT(t, cfg)

	endpoints := []string{"/api/clients", "/api/clients/test", "/api/clients/test/logs"}
	for _, ep := range endpoints {
		resp := apiGet(t, ts, ep, "")
		if resp.StatusCode != 401 {
			t.Errorf("GET %s without auth: status = %d, want 401", ep, resp.StatusCode)
		}
		resp.Body.Close()
	}
}

func TestAPIAuthWrongKey(t *testing.T) {
	cfg := testConfig("secret")
	_, ts := startTestServerT(t, cfg)

	resp := apiGet(t, ts, "/api/clients", "wrong")
	defer resp.Body.Close()

	if resp.StatusCode != 401 {
		t.Errorf("status = %d, want 401", resp.StatusCode)
	}
}

func TestAPIAuthViaXAPIKey(t *testing.T) {
	cfg := testConfig("mykey")
	_, ts := startTestServerT(t, cfg)

	req, _ := http.NewRequest("GET", ts.URL+"/api/clients", nil)
	req.Header.Set("X-API-Key", "mykey")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200 with X-API-Key", resp.StatusCode)
	}
}

func TestAPIAuthNotRequiredWhenNoKey(t *testing.T) {
	cfg := testConfig("")
	_, ts := startTestServerT(t, cfg)

	resp := apiGet(t, ts, "/api/clients", "")
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200 when no API key configured", resp.StatusCode)
	}
}

func TestAPIDisconnectedClientNotFound(t *testing.T) {
	cfg := testConfig("key")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "key", "temp-client")
	waitForClient(hub, "temp-client", 2*time.Second)
	conn.Close()
	waitForClientGone(hub, "temp-client", 2*time.Second)

	resp := apiGet(t, ts, "/api/clients/temp-client", "key")
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("disconnected client: status = %d, want 404", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// Dashboard Tests
// ---------------------------------------------------------------------------

func TestDashboardServes(t *testing.T) {
	cfg := testConfig("")
	_, ts := startTestServerT(t, cfg)

	resp, err := http.Get(ts.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("dashboard status = %d, want 200", resp.StatusCode)
	}
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "text/html") {
		t.Errorf("content-type = %q, want text/html", ct)
	}
}

func TestDashboardNoAuthRequired(t *testing.T) {
	cfg := testConfig("secret")
	_, ts := startTestServerT(t, cfg)

	resp, err := http.Get(ts.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("dashboard should not require auth, status = %d", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// Concurrent Access Tests
// ---------------------------------------------------------------------------

func TestConcurrentAPIRequests(t *testing.T) {
	cfg := testConfig("key")
	hub, ts := startTestServerT(t, cfg)

	// Connect a client with state
	conn := wsConnect(t, ts, "key", "concurrent-client")
	defer conn.Close()
	waitForClient(hub, "concurrent-client", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]string{
			{"title": "F", "stage": "ideas", "board": "b", "id": "1"},
		},
	})
	time.Sleep(100 * time.Millisecond)

	var wg sync.WaitGroup
	errors := make(chan error, 20)

	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			resp := apiGet(t, ts, "/api/clients", "key")
			defer resp.Body.Close()
			if resp.StatusCode != 200 {
				errors <- fmt.Errorf("status %d", resp.StatusCode)
			}
		}()
	}
	wg.Wait()
	close(errors)

	for err := range errors {
		t.Errorf("concurrent request error: %v", err)
	}
}

func TestConcurrentStatePushes(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	var conns []*websocket.Conn
	for i := 0; i < 5; i++ {
		id := fmt.Sprintf("push-client-%d", i)
		conn := wsConnect(t, ts, "", id)
		defer conn.Close()
		waitForClient(hub, id, 2*time.Second)
		conns = append(conns, conn)
	}

	var wg sync.WaitGroup
	for i, conn := range conns {
		wg.Add(1)
		go func(c *websocket.Conn, idx int) {
			defer wg.Done()
			for j := 0; j < 10; j++ {
				c.WriteJSON(map[string]interface{}{
					"type": "state_update",
					"features": []map[string]string{
						{"title": fmt.Sprintf("F%d-%d", idx, j), "stage": "ideas", "board": "b", "id": fmt.Sprintf("%d-%d", idx, j)},
					},
				})
			}
		}(conn, i)
	}
	wg.Wait()
	time.Sleep(300 * time.Millisecond)

	// All 5 clients should exist with data
	clients := hub.ListClients()
	if len(clients) != 5 {
		t.Errorf("expected 5 clients, got %d", len(clients))
	}
}

func TestDisconnectDuringAPICall(t *testing.T) {
	cfg := testConfig("key")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "key", "race-client")
	waitForClient(hub, "race-client", 2*time.Second)

	// Start API request and disconnect simultaneously
	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		resp := apiGet(t, ts, "/api/clients/race-client", "key")
		resp.Body.Close()
		// Either 200 or 404 is acceptable - just must not panic
	}()
	go func() {
		defer wg.Done()
		conn.Close()
	}()

	wg.Wait()
}

// ---------------------------------------------------------------------------
// checkAPIAuth unit tests
// ---------------------------------------------------------------------------

func TestCheckAPIAuthBearerToken(t *testing.T) {
	req := httptest.NewRequest("GET", "/", nil)
	req.Header.Set("Authorization", "Bearer mykey")
	if !checkAPIAuth(req, "mykey") {
		t.Error("should accept valid Bearer token")
	}
}

func TestCheckAPIAuthXAPIKey(t *testing.T) {
	req := httptest.NewRequest("GET", "/", nil)
	req.Header.Set("X-API-Key", "mykey")
	if !checkAPIAuth(req, "mykey") {
		t.Error("should accept valid X-API-Key")
	}
}

func TestCheckAPIAuthEmptyKeyAllowsAll(t *testing.T) {
	req := httptest.NewRequest("GET", "/", nil)
	if !checkAPIAuth(req, "") {
		t.Error("empty API key should allow all")
	}
}

func TestCheckAPIAuthRejectsWrong(t *testing.T) {
	req := httptest.NewRequest("GET", "/", nil)
	req.Header.Set("Authorization", "Bearer wrong")
	if checkAPIAuth(req, "correct") {
		t.Error("should reject wrong key")
	}
}

func TestCheckAPIAuthRejectsNoBearerPrefix(t *testing.T) {
	req := httptest.NewRequest("GET", "/", nil)
	req.Header.Set("Authorization", "mykey")
	if checkAPIAuth(req, "mykey") {
		t.Error("should reject Authorization without Bearer prefix")
	}
}

// ---------------------------------------------------------------------------
// Large payload test
// ---------------------------------------------------------------------------

func TestLargePayload(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "large-client")
	defer conn.Close()
	waitForClient(hub, "large-client", 2*time.Second)

	// Build a large but valid payload under 10MB
	features := make([]map[string]string, 500)
	for i := range features {
		features[i] = map[string]string{
			"title": fmt.Sprintf("Feature %d with some extra text to bulk it up", i),
			"stage": "implementing",
			"board": "default",
			"id":    fmt.Sprintf("f-%d", i),
		}
	}
	conn.WriteJSON(map[string]interface{}{
		"type":     "state_update",
		"features": features,
	})
	time.Sleep(300 * time.Millisecond)

	state, ok := hub.GetClient("large-client")
	if !ok {
		t.Fatal("client should still exist after large payload")
	}
	if len(state.Features) != 500 {
		t.Errorf("expected 500 features, got %d", len(state.Features))
	}
}

// ---------------------------------------------------------------------------
// Rapid state push test
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Helper: POST answers to submit-answers endpoint
// ---------------------------------------------------------------------------

func apiPost(t *testing.T, ts *httptest.Server, path, apiKey string, body interface{}) *http.Response {
	t.Helper()
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", ts.URL+path, strings.NewReader(string(b)))
	req.Header.Set("Content-Type", "application/json")
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST %s failed: %v", path, err)
	}
	return resp
}

func apiPostWithDashboardKey(t *testing.T, ts *httptest.Server, path, dashboardKey string, body interface{}) *http.Response {
	t.Helper()
	b, _ := json.Marshal(body)
	url := ts.URL + path
	if dashboardKey != "" {
		url += "?key=" + dashboardKey
	}
	req, _ := http.NewRequest("POST", url, strings.NewReader(string(b)))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST %s failed: %v", path, err)
	}
	return resp
}

func decodeJSON(t *testing.T, resp *http.Response) map[string]string {
	t.Helper()
	var body map[string]string
	json.NewDecoder(resp.Body).Decode(&body)
	return body
}

// registerClientWithFeatures connects a WS client, registers, and pushes features with questions.
func registerClientWithFeatures(t *testing.T, hub *Hub, ts *httptest.Server, apiKey, clientID string, features []map[string]interface{}) *websocket.Conn {
	t.Helper()
	conn := wsConnect(t, ts, apiKey, clientID)
	waitForClient(hub, clientID, 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type":     "state_update",
		"features": features,
	})
	time.Sleep(200 * time.Millisecond)
	return conn
}

// ---------------------------------------------------------------------------
// Submit Answers Endpoint Tests
// ---------------------------------------------------------------------------

func TestSubmitAnswersSuccess(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
			"questions": []map[string]string{
				{"question": "What color?", "answer": ""},
				{"question": "What size?", "answer": ""},
			}},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "client-1", features)
	defer conn.Close()

	// Set up reader for the WS message the server will send
	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/client-1/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{
			{"question": "What color?", "answer": "blue"},
			{"question": "What size?", "answer": "large"},
		},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if body["status"] != "ok" {
		t.Errorf("expected status ok, got %v", body)
	}

	// Verify WebSocket message was sent to client
	select {
	case msg := <-msgCh:
		if msg["type"] != "answer_questions" {
			t.Errorf("expected type answer_questions, got %v", msg["type"])
		}
		if msg["feature_id"] != "f1" {
			t.Errorf("expected feature_id f1, got %v", msg["feature_id"])
		}
		answers, ok := msg["answers"].([]interface{})
		if !ok || len(answers) != 2 {
			t.Errorf("expected 2 answers, got %v", msg["answers"])
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout waiting for WebSocket message")
	}
}

func TestSubmitAnswersClientNotFound(t *testing.T) {
	cfg := testConfig("")
	_, ts := startTestServerT(t, cfg)

	resp := apiPost(t, ts, "/api/clients/nonexistent/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "not found") {
		t.Errorf("error = %q, want 'client not found'", body["error"])
	}
}

func TestSubmitAnswersFeatureNotFound(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "client-2", features)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/client-2/features/nonexistent/answers", "", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "feature not found") {
		t.Errorf("error = %q, want 'feature not found'", body["error"])
	}
}

func TestSubmitAnswersFeatureNotInRequestedInput(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "implementing", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "client-3", features)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/client-3/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 422 {
		t.Errorf("status = %d, want 422", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "not in requested-input") {
		t.Errorf("error = %q, want 'not in requested-input'", body["error"])
	}
}

func TestSubmitAnswersMalformedJSON(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "client-json")
	defer conn.Close()
	waitForClient(hub, "client-json", 2*time.Second)

	req, _ := http.NewRequest("POST", ts.URL+"/api/clients/client-json/features/f1/answers", strings.NewReader("{not valid"))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(strings.ToLower(body["error"]), "invalid") {
		t.Errorf("error = %q, want something with 'invalid'", body["error"])
	}
}

func TestSubmitAnswersMethodNotAllowed(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "client-method")
	defer conn.Close()
	waitForClient(hub, "client-method", 2*time.Second)

	resp := apiGet(t, ts, "/api/clients/client-method/features/f1/answers", "")
	defer resp.Body.Close()

	if resp.StatusCode != 405 {
		t.Errorf("status = %d, want 405", resp.StatusCode)
	}
}

func TestSubmitAnswersAuthWithAPIKey(t *testing.T) {
	cfg := testConfig("secret")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
			"questions": []map[string]string{{"question": "q", "answer": ""}}},
	}
	conn := registerClientWithFeatures(t, hub, ts, "secret", "auth-client", features)
	defer conn.Close()

	// Read the WS message in background to avoid blocking
	go func() { var m interface{}; conn.ReadJSON(&m) }()

	resp := apiPost(t, ts, "/api/clients/auth-client/features/f1/answers", "secret", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
}

func TestSubmitAnswersAuthWithDashboardKey(t *testing.T) {
	cfg := testConfig("")
	cfg.DashboardKey = "dash-key"
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
			"questions": []map[string]string{{"question": "q", "answer": ""}}},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "dash-client", features)
	defer conn.Close()
	go func() { var m interface{}; conn.ReadJSON(&m) }()

	resp := apiPostWithDashboardKey(t, ts, "/api/clients/dash-client/features/f1/answers", "dash-key", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
}

func TestSubmitAnswersUnauthorized(t *testing.T) {
	cfg := testConfig("secret")
	cfg.DashboardKey = "dashsecret"
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeatures(t, hub, ts, "secret", "unauth-client", features)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/unauth-client/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 401 {
		t.Errorf("status = %d, want 401", resp.StatusCode)
	}
}

func TestSubmitAnswersWebSocketMessageFormat(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "feat-42",
			"questions": []map[string]string{
				{"question": "How many?", "answer": ""},
			}},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "msg-client", features)
	defer conn.Close()

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	apiPost(t, ts, "/api/clients/msg-client/features/feat-42/answers", "", map[string]interface{}{
		"answers": []map[string]string{
			{"question": "How many?", "answer": "42"},
		},
	})

	select {
	case raw := <-msgCh:
		var msg map[string]json.RawMessage
		if err := json.Unmarshal(raw, &msg); err != nil {
			t.Fatalf("failed to parse ws message: %v", err)
		}
		// Verify required fields
		var msgType string
		json.Unmarshal(msg["type"], &msgType)
		if msgType != "answer_questions" {
			t.Errorf("type = %q, want answer_questions", msgType)
		}
		var featureID string
		json.Unmarshal(msg["feature_id"], &featureID)
		if featureID != "feat-42" {
			t.Errorf("feature_id = %q, want feat-42", featureID)
		}
		var answers []QuestionAnswer
		json.Unmarshal(msg["answers"], &answers)
		if len(answers) != 1 {
			t.Fatalf("expected 1 answer, got %d", len(answers))
		}
		if answers[0].Question != "How many?" {
			t.Errorf("question = %q, want 'How many?'", answers[0].Question)
		}
		if answers[0].Answer != "42" {
			t.Errorf("answer = %q, want '42'", answers[0].Answer)
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout waiting for WS message")
	}
}

func TestSubmitAnswersSpecialCharacters(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
			"questions": []map[string]string{{"question": "q", "answer": ""}}},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "special-client", features)
	defer conn.Close()

	specialAnswer := "line1\nline2\ttab \"quotes\" <html> & emoji: 🎉 unicode: こんにちは"
	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	resp := apiPost(t, ts, "/api/clients/special-client/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{
			{"question": "q", "answer": specialAnswer},
		},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	// Verify the special characters survive the round trip
	select {
	case raw := <-msgCh:
		var msg struct {
			Answers []QuestionAnswer `json:"answers"`
		}
		json.Unmarshal(raw, &msg)
		if len(msg.Answers) != 1 {
			t.Fatalf("expected 1 answer, got %d", len(msg.Answers))
		}
		if msg.Answers[0].Answer != specialAnswer {
			t.Errorf("answer mangled: got %q, want %q", msg.Answers[0].Answer, specialAnswer)
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout")
	}
}

// ---------------------------------------------------------------------------
// Hub.SendToClient tests
// ---------------------------------------------------------------------------

func TestSendToClientUnknownID(t *testing.T) {
	cfg := testConfig("")
	hub := NewHub(cfg)
	go hub.Run()

	err := hub.SendToClient("nonexistent", []byte(`{"type":"test"}`))
	if err == nil {
		t.Error("SendToClient should return error for unknown client")
	}
	if !strings.Contains(err.Error(), "not found") {
		t.Errorf("error = %q, want 'not found'", err.Error())
	}
}

func TestSendToClientConnected(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "send-client")
	defer conn.Close()
	waitForClient(hub, "send-client", 2*time.Second)

	msg := []byte(`{"type":"test_msg","data":"hello"}`)
	err := hub.SendToClient("send-client", msg)
	if err != nil {
		t.Fatalf("SendToClient failed: %v", err)
	}

	// Read the message from the WS connection
	var received map[string]string
	conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	if err := conn.ReadJSON(&received); err != nil {
		t.Fatalf("failed to read ws message: %v", err)
	}
	if received["type"] != "test_msg" {
		t.Errorf("type = %q, want test_msg", received["type"])
	}
}

// ---------------------------------------------------------------------------
// State update includes questions
// ---------------------------------------------------------------------------

func TestStateUpdateIncludesQuestions(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "q-client")
	defer conn.Close()
	waitForClient(hub, "q-client", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]interface{}{
			{
				"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
				"questions": []map[string]string{
					{"question": "What color?", "answer": ""},
					{"question": "What size?", "answer": "large"},
				},
			},
		},
	})
	time.Sleep(200 * time.Millisecond)

	state, ok := hub.GetClient("q-client")
	if !ok {
		t.Fatal("client not found")
	}
	if len(state.Features) != 1 {
		t.Fatalf("expected 1 feature, got %d", len(state.Features))
	}
	if len(state.Features[0].Questions) != 2 {
		t.Fatalf("expected 2 questions, got %d", len(state.Features[0].Questions))
	}
	if state.Features[0].Questions[0].Question != "What color?" {
		t.Errorf("question = %q", state.Features[0].Questions[0].Question)
	}
	if state.Features[0].Questions[0].Answer != "" {
		t.Errorf("answer should be empty, got %q", state.Features[0].Questions[0].Answer)
	}
	if state.Features[0].Questions[1].Answer != "large" {
		t.Errorf("answer = %q, want 'large'", state.Features[0].Questions[1].Answer)
	}
}

func TestStateUpdateQuestionsIncludedInAPIResponse(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "api-q-client")
	defer conn.Close()
	waitForClient(hub, "api-q-client", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]interface{}{
			{
				"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
				"questions": []map[string]string{
					{"question": "How?", "answer": "Like this"},
				},
			},
		},
	})
	time.Sleep(200 * time.Millisecond)

	resp := apiGet(t, ts, "/api/clients/api-q-client", "")
	defer resp.Body.Close()

	var state ClientState
	json.NewDecoder(resp.Body).Decode(&state)
	if len(state.Features[0].Questions) != 1 {
		t.Fatalf("expected 1 question in API response, got %d", len(state.Features[0].Questions))
	}
	if state.Features[0].Questions[0].Answer != "Like this" {
		t.Errorf("answer = %q in API response", state.Features[0].Questions[0].Answer)
	}
}

func TestStateUpdateNoQuestionsField(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "noq-client")
	defer conn.Close()
	waitForClient(hub, "noq-client", 2*time.Second)

	// Feature without questions field
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]string{
			{"title": "F1", "stage": "ideas", "board": "b", "id": "f1"},
		},
	})
	time.Sleep(200 * time.Millisecond)

	state, _ := hub.GetClient("noq-client")
	if state.Features[0].Questions != nil && len(state.Features[0].Questions) != 0 {
		t.Errorf("expected nil or empty questions, got %v", state.Features[0].Questions)
	}
}

// ---------------------------------------------------------------------------
// Concurrent submit tests
// ---------------------------------------------------------------------------

func TestSubmitAnswersSecondSubmitRejectedAfterStageMoved(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
			"questions": []map[string]string{{"question": "q", "answer": ""}}},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "race-client", features)
	defer conn.Close()

	// Drain the first WS message
	go func() { var m interface{}; conn.ReadJSON(&m) }()

	// First submit succeeds
	resp1 := apiPost(t, ts, "/api/clients/race-client/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})
	resp1.Body.Close()
	if resp1.StatusCode != 200 {
		t.Fatalf("first submit: status = %d, want 200", resp1.StatusCode)
	}

	// Simulate the feature moving to plan-inbox (state_update from client)
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]interface{}{
			{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
		},
	})
	time.Sleep(200 * time.Millisecond)

	// Second submit should be rejected
	resp2 := apiPost(t, ts, "/api/clients/race-client/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "b"}},
	})
	defer resp2.Body.Close()

	if resp2.StatusCode != 422 {
		t.Errorf("second submit: status = %d, want 422", resp2.StatusCode)
	}
}

func TestSubmitAnswersDisconnectedClient(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "disc-client", features)
	conn.Close()
	waitForClientGone(hub, "disc-client", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/disc-client/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404 for disconnected client", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// Multiple features, partial answers
// ---------------------------------------------------------------------------

func TestSubmitAnswersMultipleQuestionsPartialAnswers(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
			"questions": []map[string]string{
				{"question": "Q1", "answer": ""},
				{"question": "Q2", "answer": ""},
				{"question": "Q3", "answer": ""},
			}},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "partial-client", features)
	defer conn.Close()

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	// Only answer Q1 and Q3, leave Q2 empty
	resp := apiPost(t, ts, "/api/clients/partial-client/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{
			{"question": "Q1", "answer": "A1"},
			{"question": "Q2", "answer": ""},
			{"question": "Q3", "answer": "A3"},
		},
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case raw := <-msgCh:
		var msg struct {
			Answers []QuestionAnswer `json:"answers"`
		}
		json.Unmarshal(raw, &msg)
		if len(msg.Answers) != 3 {
			t.Fatalf("expected 3 answers, got %d", len(msg.Answers))
		}
		// Empty answer for Q2 should be passed through (Python client decides what to do)
		if msg.Answers[1].Answer != "" {
			t.Errorf("Q2 answer = %q, want empty", msg.Answers[1].Answer)
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout")
	}
}

func TestSubmitAnswersCorrectClientRouting(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features1 := []map[string]interface{}{
		{"title": "F1", "stage": "requested-input", "board": "b", "id": "f1",
			"questions": []map[string]string{{"question": "q", "answer": ""}}},
	}
	features2 := []map[string]interface{}{
		{"title": "F2", "stage": "requested-input", "board": "b", "id": "f2",
			"questions": []map[string]string{{"question": "q", "answer": ""}}},
	}
	conn1 := registerClientWithFeatures(t, hub, ts, "", "route-a", features1)
	defer conn1.Close()
	conn2 := registerClientWithFeatures(t, hub, ts, "", "route-b", features2)
	defer conn2.Close()

	// Set up readers on both connections
	msg1Ch := make(chan []byte, 1)
	msg2Ch := make(chan []byte, 1)
	go func() { _, raw, _ := conn1.ReadMessage(); msg1Ch <- raw }()
	go func() { _, raw, _ := conn2.ReadMessage(); msg2Ch <- raw }()

	// Submit answers only for client route-a
	apiPost(t, ts, "/api/clients/route-a/features/f1/answers", "", map[string]interface{}{
		"answers": []map[string]string{{"question": "q", "answer": "a"}},
	})

	// Client route-a should receive the message
	select {
	case raw := <-msg1Ch:
		var msg map[string]string
		json.Unmarshal(raw, &msg)
		if msg["type"] != "answer_questions" {
			t.Errorf("route-a got wrong type: %s", msg["type"])
		}
	case <-time.After(2 * time.Second):
		t.Error("route-a did not receive message")
	}

	// Client route-b should NOT receive anything within a short window
	select {
	case <-msg2Ch:
		t.Error("route-b should not have received any message")
	case <-time.After(500 * time.Millisecond):
		// Good - no message received
	}
}

func TestRapidStatePush(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "rapid-client")
	defer conn.Close()
	waitForClient(hub, "rapid-client", 2*time.Second)

	for i := 0; i < 100; i++ {
		conn.WriteJSON(map[string]interface{}{
			"type": "state_update",
			"features": []map[string]string{
				{"title": fmt.Sprintf("F%d", i), "stage": "ideas", "board": "b", "id": "1"},
			},
		})
	}
	time.Sleep(500 * time.Millisecond)

	state, ok := hub.GetClient("rapid-client")
	if !ok {
		t.Fatal("client should still exist after rapid pushes")
	}
	// Should have exactly 1 feature (last update wins)
	if len(state.Features) != 1 {
		t.Errorf("expected 1 feature, got %d", len(state.Features))
	}
}

// ---------------------------------------------------------------------------
// State update: stage_actions and available_actions parsing
// ---------------------------------------------------------------------------

func registerClientWithFeaturesAndStageActions(t *testing.T, hub *Hub, ts *httptest.Server, apiKey, clientID string, features []map[string]interface{}, stageActions map[string][]string, planAgent, implAgent map[string]interface{}) *websocket.Conn {
	t.Helper()
	conn := wsConnect(t, ts, apiKey, clientID)
	waitForClient(hub, clientID, 2*time.Second)

	msg := map[string]interface{}{
		"type":     "state_update",
		"features": features,
	}
	if stageActions != nil {
		msg["stage_actions"] = stageActions
	}
	if planAgent != nil {
		msg["plan_agent"] = planAgent
	}
	if implAgent != nil {
		msg["impl_agent"] = implAgent
	}
	conn.WriteJSON(msg)
	time.Sleep(200 * time.Millisecond)
	return conn
}

func TestStateUpdateParsesStageActions(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{
		"plan":      {"plan-inbox", "reviewing-plan", "requested-input", "approved"},
		"implement": {"approved", "spec-writing"},
	}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "sa-client", features, stageActions, nil, nil)
	defer conn.Close()

	state, ok := hub.GetClient("sa-client")
	if !ok {
		t.Fatal("client not found")
	}
	if state.StageActions == nil {
		t.Fatal("StageActions should not be nil")
	}
	if len(state.StageActions["plan"]) != 4 {
		t.Errorf("expected 4 plan stages, got %d", len(state.StageActions["plan"]))
	}
	if len(state.StageActions["implement"]) != 2 {
		t.Errorf("expected 2 implement stages, got %d", len(state.StageActions["implement"]))
	}
}

func TestStateUpdateParsesAvailableActions(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "approved", "board": "b", "id": "f1",
			"available_actions": []string{"plan", "implement"}},
		{"title": "F2", "stage": "done", "board": "b", "id": "f2",
			"available_actions": []string{}},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "aa-client", features)
	defer conn.Close()

	state, ok := hub.GetClient("aa-client")
	if !ok {
		t.Fatal("client not found")
	}
	if len(state.Features) != 2 {
		t.Fatalf("expected 2 features, got %d", len(state.Features))
	}
	if len(state.Features[0].AvailableActions) != 2 {
		t.Errorf("feature 0 expected 2 available_actions, got %d", len(state.Features[0].AvailableActions))
	}
	if len(state.Features[1].AvailableActions) != 0 {
		t.Errorf("feature 1 expected 0 available_actions, got %d", len(state.Features[1].AvailableActions))
	}
}

func TestStateUpdateParsesAgentState(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	planAgent := map[string]interface{}{"running": true, "phase": "planning", "feature": "f1", "agent": "claude"}
	implAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "agent-client", features, nil, planAgent, implAgent)
	defer conn.Close()

	state, ok := hub.GetClient("agent-client")
	if !ok {
		t.Fatal("client not found")
	}
	if !state.PlanAgent.Running {
		t.Error("plan agent should be running")
	}
	if state.PlanAgent.Phase != "planning" {
		t.Errorf("plan agent phase = %q, want planning", state.PlanAgent.Phase)
	}
	if state.ImplAgent.Running {
		t.Error("impl agent should not be running")
	}
}

// ---------------------------------------------------------------------------
// Start Agent Endpoint Tests
// ---------------------------------------------------------------------------

func TestStartAgentHappyPathPlan(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{
		"plan":      {"plan-inbox", "reviewing-plan", "requested-input", "approved"},
		"implement": {"approved", "spec-writing"},
	}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "start-client", features, stageActions, planAgent, nil)
	defer conn.Close()

	// Read WS message in background
	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/start-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	// Verify WS message sent to client
	select {
	case msg := <-msgCh:
		if msg["type"] != "start_agent" {
			t.Errorf("type = %v, want start_agent", msg["type"])
		}
		if msg["feature_id"] != "f1" {
			t.Errorf("feature_id = %v, want f1", msg["feature_id"])
		}
		if msg["action"] != "plan" {
			t.Errorf("action = %v, want plan", msg["action"])
		}
	case <-time.After(2 * time.Second):
		t.Error("did not receive start_agent WS message")
	}
}

func TestStartAgentHappyPathImplement(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{
		"plan":      {"plan-inbox", "reviewing-plan", "requested-input", "approved"},
		"implement": {"approved", "spec-writing"},
	}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "approved", "board": "b", "id": "f1"},
	}
	implAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "impl-client", features, stageActions, nil, implAgent)
	defer conn.Close()

	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/impl-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "implement",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case msg := <-msgCh:
		if msg["type"] != "start_agent" {
			t.Errorf("type = %v, want start_agent", msg["type"])
		}
		if msg["action"] != "implement" {
			t.Errorf("action = %v, want implement", msg["action"])
		}
	case <-time.After(2 * time.Second):
		t.Error("did not receive start_agent WS message")
	}
}

func TestStartAgentClientNotFound(t *testing.T) {
	cfg := testConfig("")
	_, ts := startTestServerT(t, cfg)

	resp := apiPost(t, ts, "/api/clients/nonexistent/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "client not found") {
		t.Errorf("error = %q, want 'client not found'", body["error"])
	}
}

func TestStartAgentFeatureNotFound(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "fnf-client", features, stageActions, nil, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/fnf-client/features/nonexistent/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "feature not found") {
		t.Errorf("error = %q, want 'feature not found'", body["error"])
	}
}

func TestStartAgentWrongStageForAction(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "done", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "stage-client", features, stageActions, nil, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/stage-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "not valid for action") {
		t.Errorf("error = %q, want stage not valid message", body["error"])
	}
}

func TestStartAgentImplementWrongStage(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}, "implement": {"approved", "spec-writing"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "implementing", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "impl-stage-client", features, stageActions, nil, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/impl-stage-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "implement",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
}

func TestStartAgentPlanAgentBusy(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": true, "phase": "planning", "feature": "other", "agent": "claude"}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "busy-plan-client", features, stageActions, planAgent, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/busy-plan-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 409 {
		t.Errorf("status = %d, want 409", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "already running") {
		t.Errorf("error = %q, want 'already running'", body["error"])
	}
}

func TestStartAgentImplAgentBusy(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "approved", "board": "b", "id": "f1"},
	}
	implAgent := map[string]interface{}{"running": true, "phase": "implementing", "feature": "other", "agent": "claude"}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "busy-impl-client", features, stageActions, nil, implAgent)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/busy-impl-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "implement",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 409 {
		t.Errorf("status = %d, want 409", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "already running") {
		t.Errorf("error = %q, want 'already running'", body["error"])
	}
}

func TestStartAgentInvalidAction(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "invalid-action-client", features)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/invalid-action-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "unknown",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "invalid action") {
		t.Errorf("error = %q, want 'invalid action'", body["error"])
	}
}

func TestStartAgentMissingAction(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "missing-action-client", features)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/missing-action-client/features/f1/start-agent", "", map[string]interface{}{})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
}

func TestStartAgentMethodNotAllowed(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "method-client")
	defer conn.Close()
	waitForClient(hub, "method-client", 2*time.Second)

	resp := apiGet(t, ts, "/api/clients/method-client/features/f1/start-agent", "")
	defer resp.Body.Close()

	if resp.StatusCode != 405 {
		t.Errorf("status = %d, want 405", resp.StatusCode)
	}
}

func TestStartAgentMalformedJSON(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "json-client")
	defer conn.Close()
	waitForClient(hub, "json-client", 2*time.Second)

	req, _ := http.NewRequest("POST", ts.URL+"/api/clients/json-client/features/f1/start-agent", strings.NewReader("{not valid"))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
}

func TestStartAgentAuthWithAPIKey(t *testing.T) {
	cfg := testConfig("secret")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "secret", "auth-start-client", features, stageActions, planAgent, nil)
	defer conn.Close()

	// Read WS message
	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/auth-start-client/features/f1/start-agent", "secret", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
}

func TestStartAgentAuthWithDashboardKey(t *testing.T) {
	cfg := testConfig("")
	cfg.DashboardKey = "dash-key"
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "dash-start-client", features, stageActions, planAgent, nil)
	defer conn.Close()

	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPostWithDashboardKey(t, ts, "/api/clients/dash-start-client/features/f1/start-agent", "dash-key", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
}

func TestStartAgentUnauthorized(t *testing.T) {
	cfg := testConfig("secret")
	cfg.DashboardKey = "dashsecret"
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "secret", "unauth-start-client", features, stageActions, nil, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/unauth-start-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 401 {
		t.Errorf("status = %d, want 401", resp.StatusCode)
	}
}

func TestStartAgentDisconnectedClient(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "disc-start-client", features, stageActions, nil, nil)
	conn.Close()
	waitForClientGone(hub, "disc-start-client", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/disc-start-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404 for disconnected client", resp.StatusCode)
	}
}

func TestStartAgentFeatureOnDifferentClient(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	featuresA := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	featuresB := []map[string]interface{}{
		{"title": "F2", "stage": "plan-inbox", "board": "b", "id": "f2"},
	}
	connA := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "cross-a", featuresA, stageActions, nil, nil)
	defer connA.Close()
	connB := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "cross-b", featuresB, stageActions, nil, nil)
	defer connB.Close()

	// Try to start agent on cross-a for f2 (which belongs to cross-b)
	resp := apiPost(t, ts, "/api/clients/cross-a/features/f2/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404 for feature on different client", resp.StatusCode)
	}
}

func TestStartAgentStageActionsNotAvailable(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	// Client with no stage_actions
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeatures(t, hub, ts, "", "no-sa-client", features)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/no-sa-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 when stage_actions not available", resp.StatusCode)
	}
}

func TestStartAgentWebSocketMessageFormat(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{
		"plan":      {"plan-inbox", "reviewing-plan", "requested-input", "approved"},
		"implement": {"approved", "spec-writing"},
	}
	features := []map[string]interface{}{
		{"title": "My Feature", "stage": "approved", "board": "b", "id": "feat-99"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	implAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "ws-fmt-client", features, stageActions, planAgent, implAgent)
	defer conn.Close()

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	apiPost(t, ts, "/api/clients/ws-fmt-client/features/feat-99/start-agent", "", map[string]interface{}{
		"action": "implement",
	})

	select {
	case raw := <-msgCh:
		var msg map[string]json.RawMessage
		if err := json.Unmarshal(raw, &msg); err != nil {
			t.Fatalf("failed to parse ws message: %v", err)
		}
		// Verify all required fields
		var msgType string
		json.Unmarshal(msg["type"], &msgType)
		if msgType != "start_agent" {
			t.Errorf("type = %q, want start_agent", msgType)
		}
		var featureID string
		json.Unmarshal(msg["feature_id"], &featureID)
		if featureID != "feat-99" {
			t.Errorf("feature_id = %q, want feat-99", featureID)
		}
		var action string
		json.Unmarshal(msg["action"], &action)
		if action != "implement" {
			t.Errorf("action = %q, want implement", action)
		}
	case <-time.After(2 * time.Second):
		t.Error("did not receive start_agent WS message")
	}
}

func TestStartAgentEmptyFeatureList(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "empty-feat-client", features, stageActions, nil, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/empty-feat-client/features/any-id/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
}

func TestStartAgentDualActionStage(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{
		"plan":      {"plan-inbox", "approved"},
		"implement": {"approved", "spec-writing"},
	}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "approved", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	implAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "dual-client", features, stageActions, planAgent, implAgent)
	defer conn.Close()

	// Both plan and implement should work for "approved" stage

	// Drain messages in background
	msgCh := make(chan map[string]interface{}, 2)
	go func() {
		for i := 0; i < 2; i++ {
			var msg map[string]interface{}
			if err := conn.ReadJSON(&msg); err != nil {
				return
			}
			msgCh <- msg
		}
	}()

	resp1 := apiPost(t, ts, "/api/clients/dual-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	resp1.Body.Close()
	if resp1.StatusCode != 200 {
		t.Errorf("plan on approved: status = %d, want 200", resp1.StatusCode)
	}

	resp2 := apiPost(t, ts, "/api/clients/dual-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "implement",
	})
	resp2.Body.Close()
	if resp2.StatusCode != 200 {
		t.Errorf("implement on approved: status = %d, want 200", resp2.StatusCode)
	}
}

func TestStartAgentCorrectClientRouting(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	featuresA := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	featuresB := []map[string]interface{}{
		{"title": "F2", "stage": "plan-inbox", "board": "b", "id": "f2"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	connA := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "route-sa-a", featuresA, stageActions, planAgent, nil)
	defer connA.Close()
	connB := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "route-sa-b", featuresB, stageActions, planAgent, nil)
	defer connB.Close()

	msg1Ch := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		connA.ReadJSON(&msg)
		msg1Ch <- msg
	}()

	msg2Ch := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		connB.ReadJSON(&msg)
		msg2Ch <- msg
	}()

	apiPost(t, ts, "/api/clients/route-sa-a/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})

	// Client route-sa-a should receive the message
	select {
	case msg := <-msg1Ch:
		if msg["type"] != "start_agent" {
			t.Errorf("route-sa-a got wrong type: %v", msg["type"])
		}
	case <-time.After(2 * time.Second):
		t.Error("route-sa-a did not receive message")
	}

	// Client route-sa-b should NOT receive anything
	select {
	case <-msg2Ch:
		t.Error("route-sa-b should not have received any message")
	case <-time.After(500 * time.Millisecond):
		// Good - no message received
	}
}

func TestStartAgentRaceStageChanged(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "race-stage-client", features, stageActions, planAgent, nil)
	defer conn.Close()

	// Simulate stage change: feature moves to "implementing" (not valid for plan)
	conn.WriteJSON(map[string]interface{}{
		"type": "state_update",
		"features": []map[string]interface{}{
			{"title": "F1", "stage": "implementing", "board": "b", "id": "f1"},
		},
		"stage_actions": stageActions,
	})
	time.Sleep(200 * time.Millisecond)

	resp := apiPost(t, ts, "/api/clients/race-stage-client/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 after stage change", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// Auto-Mode Endpoint Tests
// ---------------------------------------------------------------------------

func TestAutoModeHappyPathPlan(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "auto-client")
	defer conn.Close()
	waitForClient(hub, "auto-client", 2*time.Second)

	// Read WS message in background
	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/auto-client/auto-mode", "", map[string]interface{}{
		"mode": "plan", "enabled": true,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if body["status"] != "ok" {
		t.Errorf("expected status ok, got %v", body)
	}

	select {
	case msg := <-msgCh:
		if msg["type"] != "set_auto_mode" {
			t.Errorf("expected type set_auto_mode, got %v", msg["type"])
		}
		if msg["mode"] != "plan" {
			t.Errorf("expected mode plan, got %v", msg["mode"])
		}
		if msg["enabled"] != true {
			t.Errorf("expected enabled true, got %v", msg["enabled"])
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout waiting for set_auto_mode WebSocket message")
	}
}

func TestAutoModeHappyPathImpl(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "auto-client-2")
	defer conn.Close()
	waitForClient(hub, "auto-client-2", 2*time.Second)

	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/auto-client-2/auto-mode", "", map[string]interface{}{
		"mode": "impl", "enabled": false,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case msg := <-msgCh:
		if msg["mode"] != "impl" {
			t.Errorf("expected mode impl, got %v", msg["mode"])
		}
		if msg["enabled"] != false {
			t.Errorf("expected enabled false, got %v", msg["enabled"])
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout waiting for set_auto_mode WebSocket message")
	}
}

func TestAutoModeInvalidMode(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "am-invalid")
	defer conn.Close()
	waitForClient(hub, "am-invalid", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/am-invalid/auto-mode", "", map[string]interface{}{
		"mode": "foo", "enabled": true,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if body["error"] != "invalid mode" {
		t.Errorf("expected 'invalid mode', got %q", body["error"])
	}
}

func TestAutoModeEmptyMode(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "am-empty")
	defer conn.Close()
	waitForClient(hub, "am-empty", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/am-empty/auto-mode", "", map[string]interface{}{
		"mode": "", "enabled": true,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
}

func TestAutoModeClientNotFound(t *testing.T) {
	cfg := testConfig("")
	_, ts := startTestServerT(t, cfg)

	resp := apiPost(t, ts, "/api/clients/nonexistent/auto-mode", "", map[string]interface{}{
		"mode": "plan", "enabled": true,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if body["error"] != "client not found" {
		t.Errorf("expected 'client not found', got %q", body["error"])
	}
}

func TestAutoModeClientDisconnected(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "am-disc")
	waitForClient(hub, "am-disc", 2*time.Second)
	conn.Close()
	time.Sleep(200 * time.Millisecond)

	resp := apiPost(t, ts, "/api/clients/am-disc/auto-mode", "", map[string]interface{}{
		"mode": "plan", "enabled": true,
	})
	defer resp.Body.Close()

	// Client is gone after disconnect - either 404 or 422
	if resp.StatusCode != 404 && resp.StatusCode != 422 {
		t.Errorf("status = %d, want 404 or 422", resp.StatusCode)
	}
}

func TestAutoModeMalformedJSON(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "am-bad-json")
	defer conn.Close()
	waitForClient(hub, "am-bad-json", 2*time.Second)

	req, _ := http.NewRequest("POST", ts.URL+"/api/clients/am-bad-json/auto-mode", strings.NewReader("{invalid"))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if body["error"] != "invalid JSON" {
		t.Errorf("expected 'invalid JSON', got %q", body["error"])
	}
}

func TestAutoModeEmptyBody(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "am-empty-body")
	defer conn.Close()
	waitForClient(hub, "am-empty-body", 2*time.Second)

	req, _ := http.NewRequest("POST", ts.URL+"/api/clients/am-empty-body/auto-mode", strings.NewReader(""))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
}

func TestAutoModeMethodNotAllowed(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "am-method")
	defer conn.Close()
	waitForClient(hub, "am-method", 2*time.Second)

	resp := apiGet(t, ts, "/api/clients/am-method/auto-mode", "")
	defer resp.Body.Close()

	if resp.StatusCode != 405 {
		t.Errorf("status = %d, want 405", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if body["error"] != "method not allowed" {
		t.Errorf("expected 'method not allowed', got %q", body["error"])
	}
}

func TestAutoModeAuthRequired(t *testing.T) {
	cfg := testConfig("secret-key")
	cfg.DashboardKey = "dash-key"
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "secret-key", "am-auth")
	defer conn.Close()
	waitForClient(hub, "am-auth", 2*time.Second)

	// No auth
	req, _ := http.NewRequest("POST", ts.URL+"/api/clients/am-auth/auto-mode", strings.NewReader(`{"mode":"plan","enabled":true}`))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 401 {
		t.Errorf("status = %d, want 401", resp.StatusCode)
	}
}

func TestAutoModeAuthWithAPIKey(t *testing.T) {
	cfg := testConfig("secret-key")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "secret-key", "am-apikey")
	defer conn.Close()
	waitForClient(hub, "am-apikey", 2*time.Second)

	// Drain WS
	go func() { var m map[string]interface{}; conn.ReadJSON(&m) }()

	resp := apiPost(t, ts, "/api/clients/am-apikey/auto-mode", "secret-key", map[string]interface{}{
		"mode": "plan", "enabled": true,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
}

func TestAutoModeAuthWithDashboardKey(t *testing.T) {
	cfg := testConfig("secret-key")
	cfg.DashboardKey = "dash-key"
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "secret-key", "am-dashkey")
	defer conn.Close()
	waitForClient(hub, "am-dashkey", 2*time.Second)

	go func() { var m map[string]interface{}; conn.ReadJSON(&m) }()

	resp := apiPostWithDashboardKey(t, ts, "/api/clients/am-dashkey/auto-mode", "dash-key", map[string]interface{}{
		"mode": "plan", "enabled": true,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
}

func TestAutoModeExtraFieldsIgnored(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "am-extra")
	defer conn.Close()
	waitForClient(hub, "am-extra", 2*time.Second)

	go func() { var m map[string]interface{}; conn.ReadJSON(&m) }()

	resp := apiPost(t, ts, "/api/clients/am-extra/auto-mode", "", map[string]interface{}{
		"mode": "plan", "enabled": true, "extra": "stuff",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// State Update: auto_plan / auto_impl persistence
// ---------------------------------------------------------------------------

func TestStateUpdateStoresAutoPlan(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "ap-store")
	defer conn.Close()
	waitForClient(hub, "ap-store", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type":      "state_update",
		"features":  []interface{}{},
		"auto_plan": true,
		"auto_impl": false,
	})
	time.Sleep(200 * time.Millisecond)

	state, ok := hub.GetClient("ap-store")
	if !ok {
		t.Fatal("client not found")
	}
	if !state.AutoPlan {
		t.Error("AutoPlan should be true")
	}
	if state.AutoImpl {
		t.Error("AutoImpl should be false")
	}
}

func TestStateUpdateStoresAutoImpl(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "ai-store")
	defer conn.Close()
	waitForClient(hub, "ai-store", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type":      "state_update",
		"features":  []interface{}{},
		"auto_plan": false,
		"auto_impl": true,
	})
	time.Sleep(200 * time.Millisecond)

	state, ok := hub.GetClient("ai-store")
	if !ok {
		t.Fatal("client not found")
	}
	if state.AutoPlan {
		t.Error("AutoPlan should be false")
	}
	if !state.AutoImpl {
		t.Error("AutoImpl should be true")
	}
}

func TestStateUpdateToggleAutoMode(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "toggle-auto")
	defer conn.Close()
	waitForClient(hub, "toggle-auto", 2*time.Second)

	// First: set auto_plan true
	conn.WriteJSON(map[string]interface{}{
		"type":      "state_update",
		"features":  []interface{}{},
		"auto_plan": true,
	})
	time.Sleep(200 * time.Millisecond)

	state, _ := hub.GetClient("toggle-auto")
	if !state.AutoPlan {
		t.Error("AutoPlan should be true after first update")
	}

	// Second: set auto_plan false
	conn.WriteJSON(map[string]interface{}{
		"type":      "state_update",
		"features":  []interface{}{},
		"auto_plan": false,
	})
	time.Sleep(200 * time.Millisecond)

	state, _ = hub.GetClient("toggle-auto")
	if state.AutoPlan {
		t.Error("AutoPlan should be false after second update")
	}
}

func TestStateUpdateMissingAutoFieldsPreserved(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "preserve-auto")
	defer conn.Close()
	waitForClient(hub, "preserve-auto", 2*time.Second)

	// Set both to true
	conn.WriteJSON(map[string]interface{}{
		"type":      "state_update",
		"features":  []interface{}{},
		"auto_plan": true,
		"auto_impl": true,
	})
	time.Sleep(200 * time.Millisecond)

	// Send update without auto fields
	conn.WriteJSON(map[string]interface{}{
		"type":     "state_update",
		"features": []interface{}{},
	})
	time.Sleep(200 * time.Millisecond)

	state, _ := hub.GetClient("preserve-auto")
	if !state.AutoPlan {
		t.Error("AutoPlan should be preserved as true when field missing")
	}
	if !state.AutoImpl {
		t.Error("AutoImpl should be preserved as true when field missing")
	}
}

func TestAutoModeInClientJSON(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "json-auto")
	defer conn.Close()
	waitForClient(hub, "json-auto", 2*time.Second)

	conn.WriteJSON(map[string]interface{}{
		"type":      "state_update",
		"features":  []interface{}{},
		"auto_plan": true,
		"auto_impl": false,
	})
	time.Sleep(200 * time.Millisecond)

	resp := apiGet(t, ts, "/api/clients/json-auto", "")
	defer resp.Body.Close()

	var state map[string]interface{}
	json.NewDecoder(resp.Body).Decode(&state)
	if state["auto_plan"] != true {
		t.Errorf("expected auto_plan true in JSON, got %v", state["auto_plan"])
	}
	if state["auto_impl"] != false {
		t.Errorf("expected auto_impl false in JSON, got %v", state["auto_impl"])
	}
}

func TestAutoModeRapidToggling(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "rapid-auto-toggle")
	defer conn.Close()
	waitForClient(hub, "rapid-auto-toggle", 2*time.Second)

	// Drain WS messages
	go func() {
		for {
			var m map[string]interface{}
			if err := conn.ReadJSON(&m); err != nil {
				return
			}
		}
	}()

	// Rapid toggle: ON, OFF, ON
	for _, enabled := range []bool{true, false, true} {
		resp := apiPost(t, ts, "/api/clients/rapid-auto-toggle/auto-mode", "", map[string]interface{}{
			"mode": "plan", "enabled": enabled,
		})
		resp.Body.Close()
		if resp.StatusCode != 200 {
			t.Fatalf("status = %d, want 200", resp.StatusCode)
		}
	}
}

// ---------------------------------------------------------------------------
// End-to-end: API → WS → state_update round trip
// ---------------------------------------------------------------------------

func TestAutoModeEndToEnd(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "e2e-auto")
	defer conn.Close()
	waitForClient(hub, "e2e-auto", 2*time.Second)

	// 1. API sends set_auto_mode to client
	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/e2e-auto/auto-mode", "", map[string]interface{}{
		"mode": "plan", "enabled": true,
	})
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	// 2. Client receives set_auto_mode
	select {
	case msg := <-msgCh:
		if msg["type"] != "set_auto_mode" || msg["mode"] != "plan" || msg["enabled"] != true {
			t.Fatalf("unexpected WS message: %v", msg)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timeout waiting for WS message")
	}

	// 3. Client sends state_update with auto_plan=true (simulating the pipeline client)
	conn.WriteJSON(map[string]interface{}{
		"type":      "state_update",
		"features":  []interface{}{},
		"auto_plan": true,
		"auto_impl": false,
	})
	time.Sleep(200 * time.Millisecond)

	// 4. Verify server stored the state
	state, ok := hub.GetClient("e2e-auto")
	if !ok {
		t.Fatal("client not found")
	}
	if !state.AutoPlan {
		t.Error("AutoPlan should be true after round trip")
	}

	// 5. Verify JSON API reflects the state
	resp2 := apiGet(t, ts, "/api/clients/e2e-auto", "")
	defer resp2.Body.Close()
	var jsonState map[string]interface{}
	json.NewDecoder(resp2.Body).Decode(&jsonState)
	if jsonState["auto_plan"] != true {
		t.Errorf("JSON API auto_plan should be true, got %v", jsonState["auto_plan"])
	}
}

// ---------------------------------------------------------------------------
// Start Agent Edge Cases - additional tests
// ---------------------------------------------------------------------------

func TestStartAgentPlanWhileImplBusy(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"approved"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "approved", "board": "b", "id": "f1"},
	}
	implAgent := map[string]interface{}{"running": true, "phase": "implementing", "feature": "f1", "agent": "claude"}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "plan-while-impl-busy", features, stageActions, nil, implAgent)
	defer conn.Close()

	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/plan-while-impl-busy/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200 (plan should work while impl runs)", resp.StatusCode)
	}
}

func TestStartAgentImplWhilePlanBusy(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"approved"}, "implement": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "approved", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": true, "phase": "planning", "feature": "f1", "agent": "claude"}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "impl-while-plan-busy", features, stageActions, planAgent, nil)
	defer conn.Close()

	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/impl-while-plan-busy/features/f1/start-agent", "", map[string]interface{}{
		"action": "implement",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200 (implement should work while plan runs)", resp.StatusCode)
	}
}

func TestStartAgentActionUppercase(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "uppercase-action", features, stageActions, nil, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/uppercase-action/features/f1/start-agent", "", map[string]interface{}{
		"action": "PLAN",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 for uppercase action", resp.StatusCode)
	}
	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "invalid action") {
		t.Errorf("error = %q, want 'invalid action'", body["error"])
	}
}

func TestStartAgentActionMixedCase(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "mixedcase-action", features, stageActions, nil, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/mixedcase-action/features/f1/start-agent", "", map[string]interface{}{
		"action": "Plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 for mixed case action", resp.StatusCode)
	}
}

func TestStartAgentEmptyAction(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "empty-action", features, stageActions, nil, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/empty-action/features/f1/start-agent", "", map[string]interface{}{
		"action": "",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 for empty action", resp.StatusCode)
	}
}

func TestStartAgentNullAction(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "f1"},
	}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "null-action", features, stageActions, nil, nil)
	defer conn.Close()

	req, _ := http.NewRequest("POST", ts.URL+"/api/clients/null-action/features/f1/start-agent", strings.NewReader(`{"action": null}`))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 for null action", resp.StatusCode)
	}
}

func TestStartAgentSpecialCharsInFeatureID(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "plan-inbox", "board": "b", "id": "feature-with-dashes_underscores.123"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "special-chars", features, stageActions, planAgent, nil)
	defer conn.Close()

	msgCh := make(chan map[string]interface{}, 1)
	go func() {
		var msg map[string]interface{}
		conn.ReadJSON(&msg)
		msgCh <- msg
	}()

	resp := apiPost(t, ts, "/api/clients/special-chars/features/feature-with-dashes_underscores.123/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case msg := <-msgCh:
		if msg["feature_id"] != "feature-with-dashes_underscores.123" {
			t.Errorf("feature_id = %v, want feature-with-dashes_underscores.123", msg["feature_id"])
		}
	case <-time.After(2 * time.Second):
		t.Error("did not receive start_agent WS message")
	}
}

func TestStartAgentFeatureWithNoAvailableActions(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "done", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "no-avail-actions", features, stageActions, planAgent, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/no-avail-actions/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 for stage not allowed", resp.StatusCode)
	}
}

func TestStartAgentFeatureUnknownStage(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox", "approved"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": "unknown-stage-xyz", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "unknown-stage", features, stageActions, planAgent, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/unknown-stage/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 for unknown stage", resp.StatusCode)
	}
}

func TestStartAgentFeatureNoStage(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "no-stage", features, stageActions, planAgent, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/no-stage/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 for feature with no stage", resp.StatusCode)
	}
}

func TestStartAgentFeatureNullStage(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	stageActions := map[string][]string{"plan": {"plan-inbox"}}
	features := []map[string]interface{}{
		{"title": "F1", "stage": nil, "board": "b", "id": "f1"},
	}
	planAgent := map[string]interface{}{"running": false, "phase": "", "feature": "", "agent": ""}
	conn := registerClientWithFeaturesAndStageActions(t, hub, ts, "", "null-stage", features, stageActions, planAgent, nil)
	defer conn.Close()

	resp := apiPost(t, ts, "/api/clients/null-stage/features/f1/start-agent", "", map[string]interface{}{
		"action": "plan",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Errorf("status = %d, want 400 for feature with null stage", resp.StatusCode)
	}
}
