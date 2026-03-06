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

func TestServeCreateIdeaHandler(t *testing.T) {
	cfg := testConfig("")
	hub := NewHub(cfg)
	go hub.Run()

	t.Run("success with all fields", func(t *testing.T) {
		clientID := "test-client"
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

		body := map[string]interface{}{
			"title":       "My Idea",
			"board":       "default",
			"description": "Test description",
		}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}

		var resp map[string]string
		json.NewDecoder(rec.Body).Decode(&resp)
		if resp["status"] != "ok" {
			t.Errorf("status = %v, want ok", resp)
		}
	})

	t.Run("client not found", func(t *testing.T) {
		body := map[string]interface{}{"title": "Test", "board": "b"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, "nonexistent")

		if rec.Code != http.StatusNotFound {
			t.Errorf("status = %d, want 404", rec.Code)
		}
	})

	t.Run("client never registered returns not found", func(t *testing.T) {
		// Requesting idea creation for a client that was never registered returns 404
		body := map[string]interface{}{"title": "Test", "board": "b"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, "never-registered")

		if rec.Code != http.StatusNotFound {
			t.Errorf("status = %d, want 404", rec.Code)
		}
	})

	t.Run("missing title", func(t *testing.T) {
		clientID := "valid-client-2"
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

		body := map[string]interface{}{"board": "b"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}

		respBody, _ := io.ReadAll(rec.Body)
		if !strings.Contains(string(respBody), "title") {
			t.Errorf("error should mention title, got: %s", respBody)
		}
	})

	t.Run("empty title", func(t *testing.T) {
		clientID := "valid-client-3"
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

		body := map[string]interface{}{"title": "", "board": "b"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}
	})

	t.Run("whitespace title", func(t *testing.T) {
		clientID := "valid-client-4"
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

		body := map[string]interface{}{"title": "   ", "board": "b"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}
	})

	t.Run("missing board", func(t *testing.T) {
		clientID := "valid-client-5"
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

		body := map[string]interface{}{"title": "Test"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}

		respBody, _ := io.ReadAll(rec.Body)
		if !strings.Contains(string(respBody), "board") {
			t.Errorf("error should mention board, got: %s", respBody)
		}
	})

	t.Run("empty board", func(t *testing.T) {
		clientID := "valid-client-6"
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

		body := map[string]interface{}{"title": "Test", "board": ""}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}
	})

	t.Run("invalid JSON", func(t *testing.T) {
		clientID := "valid-client-7"
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
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", rec.Code)
		}
	})

	t.Run("wrong method", func(t *testing.T) {
		clientID := "valid-client-8"
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
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusMethodNotAllowed {
			t.Errorf("status = %d, want 405", rec.Code)
		}
	})

	t.Run("special characters preserved", func(t *testing.T) {
		clientID := "special-client"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		specialTitle := `Quote "test" & ampersand <tag>`
		specialBoard := "board-with-dashes"
		specialDesc := "Line1\nLine2"

		body := map[string]interface{}{
			"title":       specialTitle,
			"board":       specialBoard,
			"description": specialDesc,
		}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}

		select {
		case msg := <-c.send:
			var sent map[string]interface{}
			json.Unmarshal(msg, &sent)
			if sent["title"] != specialTitle {
				t.Errorf("title = %v, want %v", sent["title"], specialTitle)
			}
			if sent["board"] != specialBoard {
				t.Errorf("board = %v, want %v", sent["board"], specialBoard)
			}
			if sent["description"] != specialDesc {
				t.Errorf("description = %v, want %v", sent["description"], specialDesc)
			}
		case <-time.After(2 * time.Second):
			t.Error("timeout waiting for message")
		}
	})

	t.Run("only required fields", func(t *testing.T) {
		clientID := "required-only-client"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{
			"title": "Minimal Idea",
			"board": "testboard",
		}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}

		select {
		case msg := <-c.send:
			var sent map[string]interface{}
			json.Unmarshal(msg, &sent)
			if sent["title"] != "Minimal Idea" {
				t.Errorf("title = %v", sent["title"])
			}
			if sent["board"] != "testboard" {
				t.Errorf("board = %v", sent["board"])
			}
			if sent["description"] != "" {
				t.Errorf("description = %v, want empty", sent["description"])
			}
		case <-time.After(2 * time.Second):
			t.Error("timeout")
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

		body := map[string]interface{}{"title": "Test", "board": "b"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfgAuth, clientID)

		// With both API key and dashboard key set, request without auth should fail
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
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"title": "Test", "board": "b"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer secret-key")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfgAuth, clientID)

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
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		body := map[string]interface{}{"title": "Test", "board": "b"}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/?key=dash-key", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfgAuth, clientID)

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}
	})

	t.Run("long inputs", func(t *testing.T) {
		clientID := "long-client"
		c := &Client{
			hub:      hub,
			conn:     nil,
			send:     make(chan []byte, 256),
			clientID: clientID,
			state: &ClientState{
				ClientID:  clientID,
				Connected: true,
			},
		}
		hub.register <- c
		time.Sleep(100 * time.Millisecond)

		longString := strings.Repeat("a", 10000)

		body := map[string]interface{}{
			"title":       longString,
			"board":       longString[:100],
			"description": longString,
		}
		bodyBytes, _ := json.Marshal(body)
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
		req.Header.Set("Content-Type", "application/json")

		rec := httptest.NewRecorder()
		serveCreateIdea(rec, req, hub, cfg, clientID)

		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}

		select {
		case msg := <-c.send:
			var sent map[string]interface{}
			json.Unmarshal(msg, &sent)
			if sent["title"] != longString {
				t.Errorf("title length = %d, want %d", len(sent["title"].(string)), len(longString))
			}
		case <-time.After(2 * time.Second):
			t.Error("timeout")
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
			},
		}
		time.Sleep(100 * time.Millisecond)

		var wg sync.WaitGroup
		errCh := make(chan error, 10)

		for i := 0; i < 10; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				body := map[string]interface{}{"title": "Test", "board": "b"}
				bodyBytes, _ := json.Marshal(body)
				req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(bodyBytes))
				req.Header.Set("Content-Type", "application/json")

				rec := httptest.NewRecorder()
				serveCreateIdea(rec, req, hub, cfg, clientID)

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
}
