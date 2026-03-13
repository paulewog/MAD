package main

import (
	"encoding/json"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/google/uuid"
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
	Title                  string           `json:"title"`
	Stage                  string           `json:"stage"`
	Board                  string           `json:"board"`
	Created                string           `json:"created"`
	ID                     string           `json:"id"`
	Slug                   string           `json:"slug,omitempty"`
	Description            string           `json:"description,omitempty"`
	History                []HistoryEntry   `json:"history,omitempty"`
	Questions              []QuestionAnswer `json:"questions,omitempty"`
	AvailableActions       []string         `json:"available_actions,omitempty"`
	ClientID               string           `json:"client_id,omitempty"`
	Plan                   string           `json:"plan,omitempty"`
	PlanExplorationSummary string           `json:"plan_exploration_summary,omitempty"`
	ImplSpec               string           `json:"impl_spec,omitempty"`
	TestSpec               string           `json:"test_spec,omitempty"`
	ImplNotes              string           `json:"impl_notes,omitempty"`
	TestResults            interface{}      `json:"test_results,omitempty"`
	Checkpoint             interface{}      `json:"checkpoint,omitempty"`
	PlanReviews            []interface{}    `json:"plan_reviews,omitempty"`
	ImplReviews            []interface{}    `json:"impl_reviews,omitempty"`
	DoneScript             string           `json:"done_script,omitempty"`
	ItemType               string           `json:"item_type,omitempty"`
	Ideation               string           `json:"ideation,omitempty"`
	IdeationSummaries      []string         `json:"ideation_summaries,omitempty"`
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

type ScriptInfo struct {
	ID          string `json:"id"`
	Label       string `json:"label"`
	Description string `json:"description"`
	Confirm     bool   `json:"confirm"`
}

type ScriptState struct {
	ScriptID   string   `json:"script_id"`
	Running    bool     `json:"running"`
	Lines      []string `json:"lines"`
	StartedAt  string   `json:"started_at,omitempty"`
	FinishedAt string   `json:"finished_at,omitempty"`
	ExitCode   *int     `json:"exit_code,omitempty"`
}

type pendingRequest struct {
	result chan moveResult
}

type PhaseInfo struct {
	Key   string `json:"key"`
	Label string `json:"label"`
}

type PhaseAgentConfig struct {
	Agent string `json:"agent"`
	Model string `json:"model"`
}

type PipelineConfig struct {
	DefaultAgent    string                      `json:"default_agent"`
	AgentForPhase   map[string]PhaseAgentConfig `json:"agent_for_phase"`
	AvailableAgents []string                    `json:"available_agents"`
	Phases          []PhaseInfo                 `json:"phases"`
}

type moveResult struct {
	Success bool   `json:"success"`
	Error   string `json:"error,omitempty"`
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
	Scripts      []ScriptInfo        `json:"scripts,omitempty"`
	ScriptStatus *ScriptState        `json:"script_status,omitempty"`
	Config       *PipelineConfig     `json:"config,omitempty"`
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
	clients         map[string]*Client
	pendingRequests map[string]*pendingRequest
	clientRequests  map[string][]string
	register        chan *Client
	unregister      chan *Client
	stop            chan struct{}
	mu              sync.RWMutex
	config          *Config
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
		clients:         make(map[string]*Client),
		pendingRequests: make(map[string]*pendingRequest),
		clientRequests:  make(map[string][]string),
		register:        make(chan *Client),
		unregister:      make(chan *Client),
		stop:            make(chan struct{}),
		config:          cfg,
	}
}

func (h *Hub) Run() {
	for {
		select {
		case <-h.stop:
			h.mu.Lock()
			for _, c := range h.clients {
				c.closeChannel()
				c.conn.Close()
			}
			h.clients = make(map[string]*Client)
			h.mu.Unlock()
			return

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
			if reqIDs, ok := h.clientRequests[client.clientID]; ok {
				for _, rid := range reqIDs {
					if pr, ok := h.pendingRequests[rid]; ok {
						pr.result <- moveResult{Success: false, Error: "client disconnected"}
						delete(h.pendingRequests, rid)
					}
				}
				delete(h.clientRequests, client.clientID)
			}
			h.mu.Unlock()
		}
	}
}

// Stop shuts down the hub, closing all client connections.
func (h *Hub) Stop() {
	close(h.stop)
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

func (h *Hub) CreateMoveRequest(clientID string) (string, <-chan moveResult) {
	requestID := uuid.New().String()
	pr := &pendingRequest{result: make(chan moveResult, 1)}
	h.mu.Lock()
	h.pendingRequests[requestID] = pr
	h.clientRequests[clientID] = append(h.clientRequests[clientID], requestID)
	h.mu.Unlock()
	return requestID, pr.result
}

func (h *Hub) ResolveMoveRequest(requestID string, result moveResult) {
	h.mu.Lock()
	defer h.mu.Unlock()
	pr, ok := h.pendingRequests[requestID]
	if !ok {
		log.Printf("ResolveMoveRequest: unknown request_id %s, ignoring", requestID)
		return
	}
	pr.result <- result
	delete(h.pendingRequests, requestID)
	for cid, reqs := range h.clientRequests {
		for i, rid := range reqs {
			if rid == requestID {
				h.clientRequests[cid] = append(reqs[:i], reqs[i+1:]...)
				if len(h.clientRequests[cid]) == 0 {
					delete(h.clientRequests, cid)
				}
				return
			}
		}
	}
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
		if raw, ok := msg["scripts"]; ok {
			var scripts []ScriptInfo
			if err := json.Unmarshal(raw, &scripts); err == nil {
				client.state.Scripts = scripts
			}
		}
		if raw, ok := msg["script_status"]; ok {
			var ss ScriptState
			if err := json.Unmarshal(raw, &ss); err == nil {
				client.state.ScriptStatus = &ss
			} else {
				client.state.ScriptStatus = nil
			}
		} else {
			client.state.ScriptStatus = nil
		}
		if raw, ok := msg["config"]; ok {
			var cfg PipelineConfig
			if err := json.Unmarshal(raw, &cfg); err == nil {
				client.state.Config = &cfg
			}
		}
		client.state.LastSeen = time.Now()
		h.mu.Unlock()

	case "disconnect":
		h.unregister <- client

	case "move_result":
		var requestID string
		if raw, ok := msg["request_id"]; ok {
			json.Unmarshal(raw, &requestID)
		}
		var success bool
		if raw, ok := msg["success"]; ok {
			json.Unmarshal(raw, &success)
		}
		var errMsg string
		if raw, ok := msg["error"]; ok {
			json.Unmarshal(raw, &errMsg)
		}
		if requestID != "" {
			h.ResolveMoveRequest(requestID, moveResult{Success: success, Error: errMsg})
		} else {
			log.Printf("move_result from client %s missing request_id, ignoring", client.clientID)
		}

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
