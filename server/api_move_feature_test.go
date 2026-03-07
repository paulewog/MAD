package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestServeMoveFeature(t *testing.T) {
	cfg := testConfig("")
	hub := NewHub(cfg)
	go hub.Run()

	t.Run("successful move request", func(t *testing.T) {
		clientID := "test-client"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
				Features: []FeatureSummary{
					{ID: "feature-1", Title: "Test Feature", Stage: "ideas", Board: "default"},
				},
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": "plan-inbox"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "feature-1")

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}

		var resp map[string]bool
		json.NewDecoder(rec.Body).Decode(&resp)
		if !resp["ok"] {
			t.Errorf("response ok = false, want true")
		}

		select {
		case msg := <-c.send:
			var sent map[string]interface{}
			json.Unmarshal(msg, &sent)
			if sent["type"] != "move_feature" {
				t.Errorf("message type = %v, want move_feature", sent["type"])
			}
			if sent["feature_id"] != "feature-1" {
				t.Errorf("feature_id = %v, want feature-1", sent["feature_id"])
			}
			if sent["target_stage"] != "plan-inbox" {
				t.Errorf("target_stage = %v, want plan-inbox", sent["target_stage"])
			}
		case <-time.After(2 * time.Second):
			t.Error("timeout waiting for message")
		}
	})

	t.Run("client not found", func(t *testing.T) {
		body := map[string]interface{}{"target_stage": "plan-inbox"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, "nonexistent", "feature-1")

		if rec.Code != http.StatusNotFound {
			t.Errorf("status = %d, want 404", rec.Code)
		}

		respBody, _ := io.ReadAll(rec.Body)
		if !strings.Contains(string(respBody), "client not found") {
			t.Errorf("error should contain 'client not found', got: %s", respBody)
		}
	})

	_ = cfg
	t.Run("client not connected", func(t *testing.T) {
		t.Skip("Skipping - difficult to simulate in test setup as hub always sets Connected=true on register")
	})

	t.Run("feature not found", func(t *testing.T) {
		clientID := "test-client-2"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
				Features: []FeatureSummary{
					{ID: "feature-1", Title: "Test Feature", Stage: "ideas", Board: "default"},
				},
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": "plan-inbox"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "nonexistent-feature")

		if rec.Code != http.StatusNotFound {
			t.Errorf("status = %d, want 404", rec.Code)
		}

		respBody, _ := io.ReadAll(rec.Body)
		if !strings.Contains(string(respBody), "feature not found") {
			t.Errorf("error should contain 'feature not found', got: %s", respBody)
		}
	})

	t.Run("missing target_stage", func(t *testing.T) {
		clientID := "test-client-3"
		hub.register <- &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
			},
		}
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "feature-1")

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}

		respBody, _ := io.ReadAll(rec.Body)
		if !strings.Contains(string(respBody), "target_stage is required") {
			t.Errorf("error should contain 'target_stage is required', got: %s", respBody)
		}
	})

	t.Run("empty target_stage", func(t *testing.T) {
		clientID := "test-client-4"
		hub.register <- &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
			},
		}
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": ""}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "feature-1")

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}

		respBody, _ := io.ReadAll(rec.Body)
		if !strings.Contains(string(respBody), "target_stage is required") {
			t.Errorf("error should contain 'target_stage is required', got: %s", respBody)
		}
	})

	t.Run("invalid target stage", func(t *testing.T) {
		clientID := "test-client-5"
		hub.register <- &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
				Features: []FeatureSummary{
					{ID: "feature-1", Title: "Test Feature", Stage: "ideas", Board: "default"},
				},
			},
		}
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": "invalid-stage"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "feature-1")

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}

		respBody, _ := io.ReadAll(rec.Body)
		if !strings.Contains(string(respBody), "invalid target stage") {
			t.Errorf("error should contain 'invalid target stage', got: %s", respBody)
		}
	})

	t.Run("invalid JSON", func(t *testing.T) {
		clientID := "test-client-6"
		hub.register <- &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
			},
		}
		time.Sleep(100 * time.Millisecond)

		req := httptest.NewRequest(http.MethodPost, "/", strings.NewReader("{not valid json"))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "feature-1")

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}

		respBody, _ := io.ReadAll(rec.Body)
		if !strings.Contains(string(respBody), "invalid JSON") {
			t.Errorf("error should contain 'invalid JSON', got: %s", respBody)
		}
	})

	t.Run("wrong method GET", func(t *testing.T) {
		clientID := "test-client-7"
		hub.register <- &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
			},
		}
		time.Sleep(100 * time.Millisecond)

		req := httptest.NewRequest(http.MethodGet, "/", nil)

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "feature-1")

		if rec.Code != http.StatusMethodNotAllowed {
			t.Errorf("status = %d, want 405", rec.Code)
		}
	})

	t.Run("unauthorized without auth when required", func(t *testing.T) {
		cfgAuth := &Config{
			Port:         0,
			APIKey:       "secret-key",
			DashboardKey: "secret-key",
			MaxLogLines:  1000,
		}

		clientID := "auth-client"
		hub.register <- &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
			},
		}
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": "plan-inbox"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfgAuth, clientID, "feature-1")

		if rec.Code != http.StatusUnauthorized {
			t.Errorf("status = %d, want 401", rec.Code)
		}
	})

	t.Run("authorized with API key", func(t *testing.T) {
		cfgAuth := testConfig("secret-key")

		clientID := "apikey-client"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
				Features: []FeatureSummary{
					{ID: "feature-1", Title: "Test Feature", Stage: "ideas", Board: "default"},
				},
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": "plan-inbox"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer secret-key")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfgAuth, clientID, "feature-1")

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}
	})

	t.Run("authorized with dashboard key", func(t *testing.T) {
		cfgAuth := &Config{
			Port:         0,
			APIKey:       "",
			DashboardKey: "dash-key",
			MaxLogLines:  1000,
		}

		clientID := "dash-client"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
				Features: []FeatureSummary{
					{ID: "feature-1", Title: "Test Feature", Stage: "ideas", Board: "default"},
				},
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": "plan-inbox"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/?key=dash-key", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfgAuth, clientID, "feature-1")

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}
	})

	t.Run("target stage same as current stage", func(t *testing.T) {
		clientID := "same-stage-client"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
				Features: []FeatureSummary{
					{ID: "feature-1", Title: "Test Feature", Stage: "ideas", Board: "default"},
				},
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": "ideas"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "feature-1")

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}

		select {
		case msg := <-c.send:
			var sent map[string]interface{}
			json.Unmarshal(msg, &sent)
			if sent["target_stage"] != "ideas" {
				t.Errorf("target_stage = %v, want ideas", sent["target_stage"])
			}
		case <-time.After(2 * time.Second):
			t.Error("timeout waiting for message")
		}
	})

	t.Run("concurrent requests", func(t *testing.T) {
		clientID := "concurrent-client"
		hub.register <- &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
				Features: []FeatureSummary{
					{ID: "feature-1", Title: "Test Feature", Stage: "ideas", Board: "default"},
				},
			},
		}
		time.Sleep(100 * time.Millisecond)

		var wg sync.WaitGroup
		errCh := make(chan error, 10)

		for i := 0; i < 10; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				body := map[string]interface{}{"target_stage": "plan-inbox"}
				bodyBytes, _ := json.Marshal(body)
				req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
				req.Header.Set("Content-Type", "application/json")

				rec := httptest.NewRecorder()
				serveMoveFeature(rec, req, hub, cfg, clientID, "feature-1")

				if rec.Code != http.StatusOK {
					errCh <- nil
				}
			}()
		}

		wg.Wait()
		close(errCh)

		errors := 0
		for range errCh {
			errors++
		}
		if errors > 0 {
			t.Errorf("%d requests failed", errors)
		}
	})

	t.Run("message to multiple features", func(t *testing.T) {
		clientID := "multi-feature-client"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
				Features: []FeatureSummary{
					{ID: "feature-1", Title: "Test Feature 1", Stage: "ideas", Board: "default"},
					{ID: "feature-2", Title: "Test Feature 2", Stage: "plan-inbox", Board: "default"},
				},
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"target_stage": "plan-inbox"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveMoveFeature(rec, req, hub, cfg, clientID, "feature-2")

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}

		select {
		case msg := <-c.send:
			var sent map[string]interface{}
			json.Unmarshal(msg, &sent)
			if sent["feature_id"] != "feature-2" {
				t.Errorf("feature_id = %v, want feature-2", sent["feature_id"])
			}
		case <-time.After(2 * time.Second):
			t.Error("timeout waiting for message")
		}
	})
}
