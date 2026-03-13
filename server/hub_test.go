package main

import (
	"sync"
	"testing"
	"time"
)

func TestCreateAndResolveMoveRequest(t *testing.T) {
	cfg := &Config{MoveTimeout: 1 * time.Second}
	hub := NewHub(cfg)
	defer hub.Stop()

	clientID := "test-client"
	requestID, resultCh := hub.CreateMoveRequest(clientID)

	if requestID == "" {
		t.Error("expected non-empty requestID")
	}

	select {
	case <-resultCh:
		t.Error("should not receive before resolve")
	default:
	}

	hub.ResolveMoveRequest(requestID, moveResult{Success: true})

	select {
	case result := <-resultCh:
		if !result.Success {
			t.Error("expected success=true")
		}
		if result.Error != "" {
			t.Error("expected empty error")
		}
	case <-time.After(1 * time.Second):
		t.Error("timeout waiting for result")
	}

	hub.mu.Lock()
	if _, ok := hub.pendingRequests[requestID]; ok {
		t.Error("pendingRequests should be cleaned up")
	}
	hub.mu.Unlock()

	hub.mu.Lock()
	for _, reqs := range hub.clientRequests {
		for _, rid := range reqs {
			if rid == requestID {
				t.Error("clientRequests should not contain requestID")
			}
		}
	}
	hub.mu.Unlock()
}

func TestResolveMoveRequestFailure(t *testing.T) {
	cfg := &Config{MoveTimeout: 1 * time.Second}
	hub := NewHub(cfg)
	defer hub.Stop()

	clientID := "test-client"
	requestID, resultCh := hub.CreateMoveRequest(clientID)

	hub.ResolveMoveRequest(requestID, moveResult{Success: false, Error: "feature not found"})

	select {
	case result := <-resultCh:
		if result.Success {
			t.Error("expected success=false")
		}
		if result.Error != "feature not found" {
			t.Errorf("error = %q, want 'feature not found'", result.Error)
		}
	case <-time.After(1 * time.Second):
		t.Error("timeout waiting for result")
	}

	hub.mu.Lock()
	if _, ok := hub.pendingRequests[requestID]; ok {
		t.Error("pendingRequests should be cleaned up")
	}
	hub.mu.Unlock()
}

func TestResolveMoveRequestTimeout(t *testing.T) {
	cfg := &Config{MoveTimeout: 50 * time.Millisecond}
	hub := NewHub(cfg)
	defer hub.Stop()

	clientID := "test-client"
	requestID, resultCh := hub.CreateMoveRequest(clientID)

	select {
	case <-resultCh:
		t.Error("should not receive before timeout")
	case <-time.After(100 * time.Millisecond):
	}

	hub.ResolveMoveRequest(requestID, moveResult{Success: false, Error: "timeout"})

	hub.mu.Lock()
	if _, ok := hub.pendingRequests[requestID]; ok {
		t.Error("pendingRequests should be cleaned up after timeout resolution")
	}
	hub.mu.Unlock()
}

func TestResolveMoveRequestUnknownID(t *testing.T) {
	cfg := &Config{MoveTimeout: 1 * time.Second}
	hub := NewHub(cfg)
	defer hub.Stop()

	defer func() {
		if r := recover(); r != nil {
			t.Errorf("ResolveMoveRequest panicked: %v", r)
		}
	}()

	hub.ResolveMoveRequest("unknown-request-id", moveResult{Success: false, Error: "test"})
}

func TestClientDisconnectCleansUpPendingRequests(t *testing.T) {
	cfg := &Config{MoveTimeout: 1 * time.Second}
	hub := NewHub(cfg)
	go hub.Run()
	defer hub.Stop()

	clientID := "test-client"
	requestID1, resultCh1 := hub.CreateMoveRequest(clientID)
	requestID2, resultCh2 := hub.CreateMoveRequest(clientID)

	client := &Client{
		hub:      hub,
		conn:     nil,
		send:     make(chan []byte, 256),
		clientID: clientID,
		state: &ClientState{
			ClientID:  clientID,
			Connected: true,
		},
	}
	hub.register <- client
	time.Sleep(100 * time.Millisecond)

	hub.unregister <- client
	time.Sleep(100 * time.Millisecond)

	select {
	case result := <-resultCh1:
		if result.Success {
			t.Error("expected success=false for disconnect")
		}
		if result.Error != "client disconnected" {
			t.Errorf("error = %q, want 'client disconnected'", result.Error)
		}
	case <-time.After(1 * time.Second):
		t.Error("timeout waiting for result1")
	}

	select {
	case result := <-resultCh2:
		if result.Success {
			t.Error("expected success=false for disconnect")
		}
		if result.Error != "client disconnected" {
			t.Errorf("error = %q, want 'client disconnected'", result.Error)
		}
	case <-time.After(1 * time.Second):
		t.Error("timeout waiting for result2")
	}

	hub.mu.Lock()
	if _, ok := hub.pendingRequests[requestID1]; ok {
		t.Error("pendingRequests should be cleaned up for request1")
	}
	if _, ok := hub.pendingRequests[requestID2]; ok {
		t.Error("pendingRequests should be cleaned up for request2")
	}
	if _, ok := hub.clientRequests[clientID]; ok {
		t.Error("clientRequests should be cleaned up")
	}
	hub.mu.Unlock()
}

func TestConcurrentMoveRequests(t *testing.T) {
	cfg := &Config{MoveTimeout: 1 * time.Second}
	hub := NewHub(cfg)
	defer hub.Stop()

	var wg sync.WaitGroup
	numGoroutines := 100

	for i := 0; i < numGoroutines; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			clientID := "client-" + string(rune(i))
			requestID, _ := hub.CreateMoveRequest(clientID)
			hub.ResolveMoveRequest(requestID, moveResult{Success: true})
		}(i)
	}

	wg.Wait()

	hub.mu.Lock()
	if len(hub.pendingRequests) != 0 {
		t.Errorf("expected 0 pending requests, got %d", len(hub.pendingRequests))
	}
	hub.mu.Unlock()
}

func TestMoveTimeoutConfigDefault(t *testing.T) {
	t.Setenv("MAD_ALLOW_NO_AUTH", "1")
	cfg := loadConfig()
	if cfg.MoveTimeout != 30*time.Second {
		t.Errorf("MoveTimeout = %v, want 30s", cfg.MoveTimeout)
	}
}

func TestMoveTimeoutConfigFromEnv(t *testing.T) {
	t.Setenv("SERVER_MOVE_TIMEOUT", "10s")
	t.Setenv("MAD_ALLOW_NO_AUTH", "1")
	cfg := loadConfig()
	if cfg.MoveTimeout != 10*time.Second {
		t.Errorf("MoveTimeout = %v, want 10s", cfg.MoveTimeout)
	}
}

func TestMoveTimeoutConfigInvalidEnv(t *testing.T) {
	t.Setenv("SERVER_MOVE_TIMEOUT", "invalid")
	t.Setenv("MAD_ALLOW_NO_AUTH", "1")
	cfg := loadConfig()
	if cfg.MoveTimeout != 30*time.Second {
		t.Errorf("MoveTimeout = %v, want 30s (fallback)", cfg.MoveTimeout)
	}
}
