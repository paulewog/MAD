package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestCreateIdeaSuccess(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "idea-client")
	defer conn.Close()
	waitForClient(hub, "idea-client", 2*time.Second)

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	resp := apiPost(t, ts, "/api/clients/idea-client/ideas", "", map[string]interface{}{
		"title":       "My New Idea",
		"board":       "default",
		"description": "A test idea",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case raw := <-msgCh:
		var msg map[string]interface{}
		json.Unmarshal(raw, &msg)
		if msg["type"] != "create_idea" {
			t.Errorf("type = %v, want create_idea", msg["type"])
		}
		if msg["title"] != "My New Idea" {
			t.Errorf("title = %v, want My New Idea", msg["title"])
		}
		if msg["board"] != "default" {
			t.Errorf("board = %v, want default", msg["board"])
		}
		if msg["description"] != "A test idea" {
			t.Errorf("description = %v, want A test idea", msg["description"])
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout waiting for WS message")
	}
}

func TestCreateIdeaOnlyRequiredFields(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "idea-client-2")
	defer conn.Close()
	waitForClient(hub, "idea-client-2", 2*time.Second)

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	resp := apiPost(t, ts, "/api/clients/idea-client-2/ideas", "", map[string]interface{}{
		"title": "Idea Without Description",
		"board": "myboard",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case raw := <-msgCh:
		var msg map[string]interface{}
		json.Unmarshal(raw, &msg)
		if msg["title"] != "Idea Without Description" {
			t.Errorf("title = %v", msg["title"])
		}
		if msg["board"] != "myboard" {
			t.Errorf("board = %v", msg["board"])
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout")
	}
}

func TestCreateIdeaClientNotFound(t *testing.T) {
	cfg := testConfig("")
	_, ts := startTestServerT(t, cfg)

	resp := apiPost(t, ts, "/api/clients/nonexistent/ideas", "", map[string]interface{}{
		"title": "Test",
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Fatalf("status = %d, want 404", resp.StatusCode)
	}

	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "not found") {
		t.Errorf("error = %q, want 'client not found'", body["error"])
	}
}

func TestCreateIdeaDisconnectedClient(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "disc-idea-client")
	waitForClient(hub, "disc-idea-client", 2*time.Second)
	conn.Close()
	waitForClientGone(hub, "disc-idea-client", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/disc-idea-client/ideas", "", map[string]interface{}{
		"title": "Test",
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Fatalf("status = %d, want 404 for disconnected client", resp.StatusCode)
	}
}

func TestCreateIdeaMissingTitle(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "valid-idea-client")
	defer conn.Close()
	waitForClient(hub, "valid-idea-client", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/valid-idea-client/ideas", "", map[string]interface{}{
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}

	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "title") {
		t.Errorf("error = %q, want 'title is required'", body["error"])
	}
}

func TestCreateIdeaEmptyTitle(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "valid-idea-client-2")
	defer conn.Close()
	waitForClient(hub, "valid-idea-client-2", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/valid-idea-client-2/ideas", "", map[string]interface{}{
		"title": "",
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}

	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "title") {
		t.Errorf("error = %q, want 'title is required'", body["error"])
	}
}

func TestCreateIdeaWhitespaceTitle(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "valid-idea-client-3")
	defer conn.Close()
	waitForClient(hub, "valid-idea-client-3", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/valid-idea-client-3/ideas", "", map[string]interface{}{
		"title": "   ",
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}

	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "title") {
		t.Errorf("error = %q, want 'title is required'", body["error"])
	}
}

func TestCreateIdeaMissingBoard(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "valid-idea-client-4")
	defer conn.Close()
	waitForClient(hub, "valid-idea-client-4", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/valid-idea-client-4/ideas", "", map[string]interface{}{
		"title": "Test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}

	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "board") {
		t.Errorf("error = %q, want 'board is required'", body["error"])
	}
}

func TestCreateIdeaEmptyBoard(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "valid-idea-client-5")
	defer conn.Close()
	waitForClient(hub, "valid-idea-client-5", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/valid-idea-client-5/ideas", "", map[string]interface{}{
		"title": "Test",
		"board": "",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}

	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "board") {
		t.Errorf("error = %q, want 'board is required'", body["error"])
	}
}

func TestCreateIdeaWhitespaceBoard(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "valid-idea-client-6")
	defer conn.Close()
	waitForClient(hub, "valid-idea-client-6", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/valid-idea-client-6/ideas", "", map[string]interface{}{
		"title": "Test",
		"board": "   ",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}

	body := decodeJSON(t, resp)
	if !strings.Contains(body["error"], "board") {
		t.Errorf("error = %q, want 'board is required'", body["error"])
	}
}

func TestCreateIdeaInvalidJSON(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "json-idea-client")
	defer conn.Close()
	waitForClient(hub, "json-idea-client", 2*time.Second)

	req, _ := http.NewRequest("POST", ts.URL+"/api/clients/json-idea-client/ideas", strings.NewReader("{not valid"))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 400 {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}

	body := decodeJSON(t, resp)
	if !strings.Contains(strings.ToLower(body["error"]), "invalid") {
		t.Errorf("error = %q, want something with 'invalid'", body["error"])
	}
}

func TestCreateIdeaWrongMethod(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "method-idea-client")
	defer conn.Close()
	waitForClient(hub, "method-idea-client", 2*time.Second)

	resp := apiGet(t, ts, "/api/clients/method-idea-client/ideas", "")
	defer resp.Body.Close()

	if resp.StatusCode != 405 {
		t.Fatalf("status = %d, want 405", resp.StatusCode)
	}
}

func TestCreateIdeaSpecialCharacters(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "special-idea-client")
	defer conn.Close()
	waitForClient(hub, "special-idea-client", 2*time.Second)

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	specialTitle := `Idea with "quotes" <br> & ampersand 🎉 日本語`
	specialBoard := "board-with-dashes_and_underscores"
	specialDesc := "Line1\nLine2\tTab"

	resp := apiPost(t, ts, "/api/clients/special-idea-client/ideas", "", map[string]interface{}{
		"title":       specialTitle,
		"board":       specialBoard,
		"description": specialDesc,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case raw := <-msgCh:
		var msg map[string]interface{}
		json.Unmarshal(raw, &msg)
		if msg["title"] != specialTitle {
			t.Errorf("title mangled: got %q, want %q", msg["title"], specialTitle)
		}
		if msg["board"] != specialBoard {
			t.Errorf("board mangled: got %q, want %q", msg["board"], specialBoard)
		}
		if msg["description"] != specialDesc {
			t.Errorf("description mangled: got %q, want %q", msg["description"], specialDesc)
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout")
	}
}

func TestCreateIdeaMultipleClientsCorrectRouting(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn1 := wsConnect(t, ts, "", "client-a")
	defer conn1.Close()
	conn2 := wsConnect(t, ts, "", "client-b")
	defer conn2.Close()
	waitForClient(hub, "client-a", 2*time.Second)
	waitForClient(hub, "client-b", 2*time.Second)

	msgCh1 := make(chan []byte, 1)
	msgCh2 := make(chan []byte, 1)

	go func() {
		_, raw, _ := conn1.ReadMessage()
		msgCh1 <- raw
	}()
	go func() {
		_, raw, _ := conn2.ReadMessage()
		msgCh2 <- raw
	}()

	resp := apiPost(t, ts, "/api/clients/client-a/ideas", "", map[string]interface{}{
		"title": "Idea for A",
		"board": "board1",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case raw := <-msgCh1:
		var msg map[string]interface{}
		json.Unmarshal(raw, &msg)
		if msg["title"] != "Idea for A" {
			t.Errorf("client-a received wrong title: %v", msg["title"])
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout waiting for client-a message")
	}

	select {
	case raw := <-msgCh2:
		t.Errorf("client-b should not receive message, got: %s", string(raw))
	case <-time.After(1 * time.Second):
	}
}

func TestCreateIdeaWithAPIKey(t *testing.T) {
	cfg := testConfig("secret")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "secret", "apikey-idea-client")
	defer conn.Close()
	waitForClient(hub, "apikey-idea-client", 2*time.Second)

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	resp := apiPost(t, ts, "/api/clients/apikey-idea-client/ideas", "secret", map[string]interface{}{
		"title": "Idea with API Key",
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case <-msgCh:
	case <-time.After(2 * time.Second):
		t.Error("timeout")
	}
}

func TestCreateIdeaWithDashboardKey(t *testing.T) {
	cfg := testConfig("")
	cfg.DashboardKey = "dashkey"
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "dash-idea-client")
	defer conn.Close()
	waitForClient(hub, "dash-idea-client", 2*time.Second)

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	resp := apiPostWithDashboardKey(t, ts, "/api/clients/dash-idea-client/ideas", "dashkey", map[string]interface{}{
		"title": "Idea with Dashboard Key",
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case <-msgCh:
	case <-time.After(2 * time.Second):
		t.Error("timeout")
	}
}

func TestCreateIdeaUnauthorized(t *testing.T) {
	cfg := testConfig("secret")
	cfg.DashboardKey = "dashsecret"
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "secret", "unauth-idea-client")
	defer conn.Close()
	waitForClient(hub, "unauth-idea-client", 2*time.Second)

	resp := apiPost(t, ts, "/api/clients/unauth-idea-client/ideas", "", map[string]interface{}{
		"title": "Unauthorized Idea",
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 401 {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

func TestCreateIdeaUnauthorizedWrongDashboardKey(t *testing.T) {
	cfg := testConfig("")
	cfg.DashboardKey = "correct-key"
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "wrongkey-idea-client")
	defer conn.Close()
	waitForClient(hub, "wrongkey-idea-client", 2*time.Second)

	resp := apiPostWithDashboardKey(t, ts, "/api/clients/wrongkey-idea-client/ideas", "wrong-key", map[string]interface{}{
		"title": "Wrong Key Idea",
		"board": "test",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 401 {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

func TestCreateIdeaConcurrentRequests(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "concurrent-idea-client")
	defer conn.Close()
	waitForClient(hub, "concurrent-idea-client", 2*time.Second)

	var wg sync.WaitGroup
	errors := make(chan error, 10)

	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			resp := apiPost(t, ts, "/api/clients/concurrent-idea-client/ideas", "", map[string]interface{}{
				"title": "Concurrent Idea",
				"board": "test",
			})
			defer resp.Body.Close()
			if resp.StatusCode != 200 {
				errors <- fmt.Errorf("request %d: status %d", idx, resp.StatusCode)
			}
		}(i)
	}
	wg.Wait()
	close(errors)

	for err := range errors {
		t.Errorf("concurrent request error: %v", err)
	}
}

func TestCreateIdeaBoardWithSpaces(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "space-board-client")
	defer conn.Close()
	waitForClient(hub, "space-board-client", 2*time.Second)

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	resp := apiPost(t, ts, "/api/clients/space-board-client/ideas", "", map[string]interface{}{
		"title": "Idea",
		"board": "my board name",
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case raw := <-msgCh:
		var msg map[string]interface{}
		json.Unmarshal(raw, &msg)
		if msg["board"] != "my board name" {
			t.Errorf("board = %v, want 'my board name'", msg["board"])
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout")
	}
}

func TestCreateIdeaLongInputs(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "long-idea-client")
	defer conn.Close()
	waitForClient(hub, "long-idea-client", 2*time.Second)

	longString := strings.Repeat("a", 10000)

	msgCh := make(chan []byte, 1)
	go func() {
		_, raw, _ := conn.ReadMessage()
		msgCh <- raw
	}()

	resp := apiPost(t, ts, "/api/clients/long-idea-client/ideas", "", map[string]interface{}{
		"title":       longString,
		"board":       longString[:100],
		"description": longString,
	})
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	select {
	case raw := <-msgCh:
		var msg map[string]interface{}
		json.Unmarshal(raw, &msg)
		if msg["title"] != longString {
			t.Errorf("title was truncated or modified, length: %d vs %d", len(msg["title"].(string)), len(longString))
		}
	case <-time.After(2 * time.Second):
		t.Error("timeout")
	}
}

func TestCreateIdeaClientDisconnectsDuringRequest(t *testing.T) {
	cfg := testConfig("")
	hub, ts := startTestServerT(t, cfg)

	conn := wsConnect(t, ts, "", "disconnect-race-client")
	waitForClient(hub, "disconnect-race-client", 2*time.Second)

	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		resp := apiPost(t, ts, "/api/clients/disconnect-race-client/ideas", "", map[string]interface{}{
			"title": "Race Condition Idea",
			"board": "test",
		})
		resp.Body.Close()
	}()

	go func() {
		defer wg.Done()
		time.Sleep(50 * time.Millisecond)
		conn.Close()
	}()

	wg.Wait()
}
