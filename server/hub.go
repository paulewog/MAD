package main

import (
	"encoding/json"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	maxMessageSize = 10 * 1024 * 1024 // 10MB
	writeWait      = 10 * time.Second
	pongWait       = 60 * time.Second
	pingPeriod     = (pongWait * 9) / 10
)

type HistoryEntry struct {
	Timestamp string `json:"ts"`
	Stage     string `json:"stage"`
	Note      string `json:"note"`
}

type QuestionAnswer struct {
	Question string `json:"question"`
	Answer   string `json:"answer"`
}

type FeatureSummary struct {
	Title            string           `json:"title"`
	Stage            string           `json:"stage"`
	Board            string           `json:"board"`
	Created          string           `json:"created"`
	ID               string           `json:"id"`
	Description      string           `json:"description,omitempty"`
	History          []HistoryEntry   `json:"history,omitempty"`
	Questions        []QuestionAnswer `json:"questions,omitempty"`
	AvailableActions []string         `json:"available_actions,omitempty"`
	ClientID         string           `json:"client_id,omitempty"`
	Plan             string           `json:"plan,omitempty"`
	ImplSpec         string           `json:"impl_spec,omitempty"`
	TestSpec         string           `json:"test_spec,omitempty"`
	ImplNotes        string           `json:"impl_notes,omitempty"`
	TestResults      interface{}      `json:"test_results,omitempty"`
	Checkpoint       interface{}      `json:"checkpoint,omitempty"`
}

type LogEntry struct {
	Timestamp string `json:"timestamp"`
	Phase     string `json:"phase"`
	Output    string `json:"output"`
	ClientID  string `json:"client_id"`
}

type AgentState struct {
	Running bool   `json:"running"`
	Phase   string `json:"phase"`
	Feature string `json:"feature"`
	Agent   string `json:"agent"`
}

type ClientState struct {
	ClientID     string              `json:"client_id"`
	Features     []FeatureSummary    `json:"features"`
	Logs         []LogEntry          `json:"logs"`
	LastSeen     time.Time           `json:"last_seen"`
	Connected    bool                `json:"connected"`
	PlanAgent    AgentState          `json:"plan_agent"`
	ImplAgent    AgentState          `json:"impl_agent"`
	AutoPlan     bool                `json:"auto_plan"`
	AutoImpl     bool                `json:"auto_impl"`
	StageActions map[string][]string `json:"stage_actions,omitempty"`
}

type Client struct {
	hub       *Hub
	conn      *websocket.Conn
	send      chan []byte
	state     *ClientState
	clientID  string
	closeSend sync.Once
}

type Hub struct {
	clients    map[string]*Client
	register   chan *Client
	unregister chan *Client
	mu         sync.RWMutex
	config     *Config
}

func (c *Client) closeChannel() {
	c.closeSend.Do(func() { close(c.send) })
}

func (c *Client) safeSend(msg []byte) {
	defer func() { recover() }()
	c.send <- msg
}

func NewHub(cfg *Config) *Hub {
	return &Hub{
		clients:    make(map[string]*Client),
		register:   make(chan *Client),
		unregister: make(chan *Client),
		config:     cfg,
	}
}

func (h *Hub) Run() {
	for {
		select {
		case client := <-h.register:
			h.mu.Lock()
			if existing, ok := h.clients[client.clientID]; ok {
				existing.state.Connected = false
				existing.closeChannel()
				existing.conn.Close()
			}
			h.clients[client.clientID] = client
			client.state.Connected = true
			client.state.LastSeen = time.Now()
			h.mu.Unlock()

		case client := <-h.unregister:
			h.mu.Lock()
			if c, ok := h.clients[client.clientID]; ok && c == client {
				c.state.Connected = false
				c.closeChannel()
				delete(h.clients, client.clientID)
			}
			h.mu.Unlock()
		}
	}
}

func (h *Hub) ListClients() []ClientState {
	h.mu.RLock()
	defer h.mu.RUnlock()
	result := make([]ClientState, 0, len(h.clients))
	for _, c := range h.clients {
		result = append(result, *c.state)
	}
	return result
}

func (h *Hub) GetClient(id string) (*ClientState, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	c, ok := h.clients[id]
	if !ok {
		return nil, false
	}
	copy := *c.state
	return &copy, true
}

func (h *Hub) SendToClient(clientID string, msg []byte) error {
	h.mu.RLock()
	defer h.mu.RUnlock()
	c, ok := h.clients[clientID]
	if !ok {
		return fmt.Errorf("client %q not found", clientID)
	}
	if !c.state.Connected {
		return fmt.Errorf("client %q not connected", clientID)
	}
	c.safeSend(msg)
	return nil
}

func (h *Hub) handleMessage(client *Client, message []byte) {
	var msg map[string]json.RawMessage
	if err := json.Unmarshal(message, &msg); err != nil {
		log.Printf("discarding malformed message from client %s", client.clientID)
		return
	}

	var msgType string
	if raw, ok := msg["type"]; ok {
		if err := json.Unmarshal(raw, &msgType); err != nil {
			log.Printf("discarding malformed message from client %s", client.clientID)
			return
		}
	}

	switch msgType {
	case "register":
		var apiKey string
		if raw, ok := msg["api_key"]; ok {
			json.Unmarshal(raw, &apiKey)
		}
		if h.config.APIKey != "" && apiKey != h.config.APIKey {
			resp, _ := json.Marshal(map[string]string{"type": "error", "message": "unauthorized"})
			client.safeSend(resp)
			// Close after sending error
			go func() {
				time.Sleep(100 * time.Millisecond)
				client.conn.Close()
			}()
			return
		}
		var clientID string
		if raw, ok := msg["client_id"]; ok {
			json.Unmarshal(raw, &clientID)
		}
		if clientID != "" {
			oldID := client.clientID
			client.clientID = clientID
			client.state.ClientID = clientID

			// Re-register with new ID if it changed
			h.mu.Lock()
			if oldID != clientID {
				delete(h.clients, oldID)
				if existing, ok := h.clients[clientID]; ok && existing != client {
					existing.state.Connected = false
					existing.closeChannel()
					existing.conn.Close()
				}
				h.clients[clientID] = client
			}
			h.mu.Unlock()
		}
		resp, _ := json.Marshal(map[string]string{"type": "ack"})
		client.safeSend(resp)

	case "state_update":
		h.mu.Lock()
		if raw, ok := msg["features"]; ok {
			var features []FeatureSummary
			if err := json.Unmarshal(raw, &features); err == nil {
				client.state.Features = features
			}
		}
		if raw, ok := msg["logs"]; ok {
			var logs []LogEntry
			if err := json.Unmarshal(raw, &logs); err == nil {
				for i := range logs {
					logs[i].ClientID = client.clientID
				}
				client.state.Logs = append(client.state.Logs, logs...)
				if len(client.state.Logs) > h.config.MaxLogLines {
					client.state.Logs = client.state.Logs[len(client.state.Logs)-h.config.MaxLogLines:]
				}
			}
		}
		if raw, ok := msg["plan_agent"]; ok {
			var as AgentState
			if err := json.Unmarshal(raw, &as); err == nil {
				client.state.PlanAgent = as
			}
		}
		if raw, ok := msg["impl_agent"]; ok {
			var as AgentState
			if err := json.Unmarshal(raw, &as); err == nil {
				client.state.ImplAgent = as
			}
		}
		if raw, ok := msg["auto_plan"]; ok {
			var v bool
			if err := json.Unmarshal(raw, &v); err == nil {
				client.state.AutoPlan = v
			}
		}
		if raw, ok := msg["auto_impl"]; ok {
			var v bool
			if err := json.Unmarshal(raw, &v); err == nil {
				client.state.AutoImpl = v
			}
		}
		if raw, ok := msg["stage_actions"]; ok {
			var sa map[string][]string
			if err := json.Unmarshal(raw, &sa); err == nil {
				client.state.StageActions = sa
			}
		}
		client.state.LastSeen = time.Now()
		h.mu.Unlock()

	case "disconnect":
		h.unregister <- client

	default:
		log.Printf("discarding malformed message from client %s", client.clientID)
	}
}

func (c *Client) writePump() {
	ticker := time.NewTicker(pingPeriod)
	defer func() {
		ticker.Stop()
		c.conn.Close()
	}()
	for {
		select {
		case message, ok := <-c.send:
			c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if !ok {
				c.conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}
			if err := c.conn.WriteMessage(websocket.TextMessage, message); err != nil {
				return
			}
		case <-ticker.C:
			c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if err := c.conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}

func (c *Client) readPump() {
	defer func() {
		c.hub.unregister <- c
		c.conn.Close()
	}()
	c.conn.SetReadLimit(maxMessageSize)
	c.conn.SetReadDeadline(time.Now().Add(pongWait))
	c.conn.SetPongHandler(func(string) error {
		c.conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})
	for {
		_, message, err := c.conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
				log.Printf("websocket error from client %s: %v", c.clientID, err)
			}
			return
		}
		c.hub.handleMessage(c, message)
	}
}
