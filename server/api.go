package main

import (
	"bytes"
	"embed"
	"encoding/json"
	"html/template"
	"io"
	"log"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

var stageOrder = []string{
	"ideas",
	"ideating",
	"plan-inbox",
	"reviewing-plan",
	"requested-input",
	"awaiting-human-approval",
	"approved",
	"spec-writing",
	"implementing",
	"testing",
	"review",
	"final-human-approval",
	"done",
	"rejected",
}

func isValidStage(stage string) bool {
	for _, s := range stageOrder {
		if s == stage {
			return true
		}
	}
	return false
}

func boardsFromClients(clients []ClientState) []string {
	seen := map[string]bool{}
	var boards []string
	for _, c := range clients {
		for _, f := range c.Features {
			if f.Board != "" && !seen[f.Board] {
				seen[f.Board] = true
				boards = append(boards, f.Board)
			}
		}
	}
	if len(boards) == 0 {
		boards = append(boards, "mad")
	}
	sort.Strings(boards)
	return boards
}

// StageGroup holds features for a single stage, preserving order.
type StageGroup struct {
	Stage    string
	Features []FeatureSummary
}

func lastModifiedTime(f FeatureSummary) string {
	if len(f.History) > 0 {
		return f.History[len(f.History)-1].Timestamp
	}
	return f.Created
}

// groupFeaturesByStage returns features organized by stage in canonical order.
// Stages with zero features are included so the UI can show empty columns.
func groupFeaturesByStage(features []FeatureSummary) []StageGroup {
	byStage := make(map[string][]FeatureSummary)
	for _, f := range features {
		byStage[f.Stage] = append(byStage[f.Stage], f)
	}
	for _, feats := range byStage {
		sort.Slice(feats, func(i, j int) bool {
			return lastModifiedTime(feats[i]) > lastModifiedTime(feats[j])
		})
	}
	groups := make([]StageGroup, 0, len(stageOrder))
	for _, s := range stageOrder {
		groups = append(groups, StageGroup{Stage: s, Features: byStage[s]})
	}
	// Include any stages not in the canonical list (defensive)
	for stage, feats := range byStage {
		found := false
		for _, s := range stageOrder {
			if s == stage {
				found = true
				break
			}
		}
		if !found {
			groups = append(groups, StageGroup{Stage: stage, Features: feats})
		}
	}
	return groups
}

//go:embed templates
var templateFS embed.FS

//go:embed static
var staticFS embed.FS

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// checkAnyAuth returns true if the request is authenticated by either API key or dashboard key.
// Used for endpoints that can be accessed by both API clients and dashboard users.
func checkAnyAuth(r *http.Request, cfg *Config) bool {
	// If unauthenticated access is explicitly allowed, permit all
	if cfg.AllowUnauthenticatedAccess {
		return true
	}
	// Check API key (only if configured)
	if cfg.APIKey != "" && checkAPIAuth(r, cfg.APIKey) {
		return true
	}
	// Check dashboard key (only if configured)
	if cfg.DashboardKey != "" && checkDashboardAuth(r, cfg.DashboardKey) {
		return true
	}
	return false
}

func checkAPIAuth(r *http.Request, apiKey string) bool {
	if apiKey == "" {
		return false
	}
	// Check ?key= query parameter
	if r.URL.Query().Get("key") == apiKey {
		return true
	}
	// Check Authorization: Bearer <key>
	if auth := r.Header.Get("Authorization"); auth != "" {
		if strings.HasPrefix(auth, "Bearer ") && strings.TrimPrefix(auth, "Bearer ") == apiKey {
			return true
		}
	}
	// Check X-API-Key header
	if r.Header.Get("X-API-Key") == apiKey {
		return true
	}
	return false
}

func checkDashboardAuth(r *http.Request, dashboardKey string) bool {
	if dashboardKey == "" {
		return false
	}
	return r.URL.Query().Get("key") == dashboardKey
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func registerRoutes(mux *http.ServeMux, hub *Hub, cfg *Config) {
	funcMap := template.FuncMap{
		"groupByStage": groupFeaturesByStage,
		"stageLabel": func(s string) string {
			return strings.ReplaceAll(s, "-", " ")
		},
		"toJSON": func(v interface{}) string {
			b, _ := json.Marshal(v)
			return string(b)
		},
		"uniqueBoards": func(features []FeatureSummary) string {
			seen := map[string]bool{}
			var boards []string
			for _, f := range features {
				if f.Board != "" && !seen[f.Board] {
					seen[f.Board] = true
					boards = append(boards, f.Board)
				}
			}
			return strings.Join(boards, ", ")
		},
		"sort": func(slice interface{}, field string) interface{} {
			// Sort clients by ClientID field
			clients, ok := slice.([]ClientState)
			if !ok {
				return slice
			}
			sorted := make([]ClientState, len(clients))
			copy(sorted, clients)
			sort.Slice(sorted, func(i, j int) bool {
				return sorted[i].ClientID < sorted[j].ClientID
			})
			return sorted
		},
	}

	tmpl := template.Must(template.New("").Funcs(funcMap).ParseFS(templateFS, "templates/index.html", "templates/client.html"))

	// Static files
	mux.Handle("/static/", http.FileServer(http.FS(staticFS)))

	// Dashboard page
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		// Only handle exact "/" path; let other handlers catch their paths
		path := r.URL.Path
		if path != "/" {
			// Check if it matches /clients/{id}
			if strings.HasPrefix(path, "/clients/") {
				serveClientPage(w, r, hub, tmpl, cfg)
				return
			}
			http.NotFound(w, r)
			return
		}
		key := r.URL.Query().Get("key")
		authenticated := cfg.DashboardKey == "" || (key != "" && key == cfg.DashboardKey)

		showKeyModal := !authenticated && cfg.DashboardKey != ""

		var clients []ClientState
		var allFeatures []FeatureSummary

		if authenticated {
			clients = hub.ListClients()
			// Aggregate all features across all clients, stamping client ID
			for _, c := range clients {
				for _, f := range c.Features {
					f.ClientID = c.ClientID
					allFeatures = append(allFeatures, f)
				}
			}
		}

		activeSlugs := map[string]bool{}
		for _, c := range clients {
			if c.PlanAgent.Running && c.PlanAgent.Feature != "" {
				activeSlugs[c.PlanAgent.Feature] = true
			}
			if c.ImplAgent.Running && c.ImplAgent.Feature != "" {
				activeSlugs[c.ImplAgent.Feature] = true
			}
		}

		data := map[string]interface{}{
			"Clients":       clients,
			"Key":           key,
			"Authenticated": authenticated,
			"ShowKeyModal":  showKeyModal,
			"StageGroups":   groupFeaturesByStage(allFeatures),
			"Boards":        boardsFromClients(clients),
			"ActiveSlugs":   activeSlugs,
		}
		var buf bytes.Buffer
		if err := tmpl.ExecuteTemplate(&buf, "index.html", data); err != nil {
			log.Printf("template error: %v", err)
			http.Error(w, "internal server error", 500)
			return
		}
		w.Header().Set("Content-Type", "text/html")
		buf.WriteTo(w)
	})

	// API: list clients (and sub-paths /api/clients/{id}, /api/clients/{id}/logs)
	apiClientsHandler := func(w http.ResponseWriter, r *http.Request) {
		// Check if this is /api/clients/{id} or /api/clients/{id}/logs
		trimmed := strings.TrimPrefix(r.URL.Path, "/api/clients")
		trimmed = strings.TrimPrefix(trimmed, "/")
		if trimmed != "" {
			parts := strings.SplitN(trimmed, "/", 2)
			clientID := parts[0]
			if len(parts) == 2 {
				sub := parts[1]
				if sub == "logs" {
					serveClientLogs(w, r, hub, cfg, clientID)
					return
				}
				if sub == "auto-mode" {
					serveAutoMode(w, r, hub, cfg, clientID)
					return
				}
				if sub == "set-agent-for-phase" {
					serveSetAgentForPhase(w, r, hub, cfg, clientID)
					return
				}
				if sub == "config" {
					serveClientConfig(w, r, hub, cfg, clientID)
					return
				}
				if sub == "run-script" {
					serveRunScript(w, r, hub, cfg, clientID)
					return
				}
				if sub == "restart" {
					serveRestartTUI(w, r, hub, cfg, clientID)
					return
				}
				if sub == "ideas" {
					serveCreateIdea(w, r, hub, cfg, clientID)
					return
				}
				if sub == "scripts" {
					serveScripts(w, r, hub, cfg, clientID)
					return
				}
				// Match features/{fid}/answers, features/{fid}/start-agent, or features/{fid}/move
				if strings.HasPrefix(sub, "features/") {
					featureParts := strings.SplitN(strings.TrimPrefix(sub, "features/"), "/", 2)
					featureID := featureParts[0]
					if len(featureParts) == 2 && featureParts[1] == "answers" && featureID != "" {
						serveSubmitAnswers(w, r, hub, cfg, clientID, featureID)
						return
					}
					if len(featureParts) == 2 && featureParts[1] == "start-agent" && featureID != "" {
						serveStartAgent(w, r, hub, cfg, clientID, featureID)
						return
					}
					if len(featureParts) == 2 && featureParts[1] == "move" && featureID != "" {
						serveMoveFeature(w, r, hub, cfg, clientID, featureID)
						return
					}
					if len(featureParts) == 2 && featureParts[1] == "edit-description" && featureID != "" {
						serveEditDescription(w, r, hub, cfg, clientID, featureID)
						return
					}
					if len(featureParts) == 2 && featureParts[1] == "edit-done-script" && featureID != "" {
						serveEditDoneScript(w, r, hub, cfg, clientID, featureID)
						return
					}
					if len(featureParts) == 2 && featureParts[1] == "edit-title" && featureID != "" {
						serveEditTitle(w, r, hub, cfg, clientID, featureID)
						return
					}
					if len(featureParts) == 2 && featureParts[1] == "edit-type" && featureID != "" {
						serveEditItemType(w, r, hub, cfg, clientID, featureID)
						return
					}
					if len(featureParts) == 2 && featureParts[1] == "edit-ideation-prompt" && featureID != "" {
						serveEditIdeationPrompt(w, r, hub, cfg, clientID, featureID)
						return
					}
					if len(featureParts) == 2 && featureParts[1] == "edit-depends-on" && featureID != "" {
						serveEditDependsOn(w, r, hub, cfg, clientID, featureID)
						return
					}
				}
			}
			serveClientJSON(w, r, hub, cfg, clientID)
			return
		}

		// If HTMX request, require dashboard auth
		if r.Header.Get("HX-Request") != "" {
			if !checkDashboardAuth(r, cfg.DashboardKey) {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
		} else if !checkAnyAuth(r, cfg) {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}

		clients := hub.ListClients()
		key := r.URL.Query().Get("key")

		// If HTMX request, return HTML fragment
		if r.Header.Get("HX-Request") != "" {
			w.Header().Set("Content-Type", "text/html")
			data := map[string]interface{}{
				"Clients": clients,
				"Key":     key,
				"Boards":  boardsFromClients(clients),
			}
			if err := tmpl.ExecuteTemplate(w, "client_cards", data); err != nil {
				log.Printf("template error: %v", err)
				http.Error(w, "internal server error", 500)
			}
			return
		}

		writeJSON(w, http.StatusOK, clients)
	}
	mux.HandleFunc("/api/clients", apiClientsHandler)
	mux.HandleFunc("/api/clients/", apiClientsHandler)

	// Key validation endpoint
	mux.HandleFunc("/api/auth/validate", func(w http.ResponseWriter, r *http.Request) {
		key := r.URL.Query().Get("key")
		if cfg.DashboardKey == "" {
			writeJSON(w, http.StatusOK, map[string]bool{"valid": true})
			return
		}
		if key != "" && key == cfg.DashboardKey {
			writeJSON(w, http.StatusOK, map[string]bool{"valid": true})
			return
		}
		writeJSON(w, http.StatusUnauthorized, map[string]bool{"valid": false})
	})

	// HTMX fragment: board view (all features grouped by stage)
	mux.HandleFunc("/api/board", func(w http.ResponseWriter, r *http.Request) {
		if !checkDashboardAuth(r, cfg.DashboardKey) {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		clients := hub.ListClients()
		log.Printf("/api/board: got %d clients", len(clients))
		var allFeatures []FeatureSummary
		for _, c := range clients {
			log.Printf("  client %s: %d features", c.ClientID, len(c.Features))
			for _, f := range c.Features {
				f.ClientID = c.ClientID
				allFeatures = append(allFeatures, f)
			}
		}
		log.Printf("/api/board: total %d features across all clients", len(allFeatures))
		activeSlugs := map[string]bool{}
		for _, c := range clients {
			if c.PlanAgent.Running && c.PlanAgent.Feature != "" {
				activeSlugs[c.PlanAgent.Feature] = true
			}
			if c.ImplAgent.Running && c.ImplAgent.Feature != "" {
				activeSlugs[c.ImplAgent.Feature] = true
			}
		}
		data := map[string]interface{}{
			"StageGroups": groupFeaturesByStage(allFeatures),
			"Key":         r.URL.Query().Get("key"),
			"ActiveSlugs": activeSlugs,
		}
		var buf bytes.Buffer
		if err := tmpl.ExecuteTemplate(&buf, "board_fragment", data); err != nil {
			log.Printf("template error: %v", err)
			http.Error(w, "internal server error", 500)
			return
		}
		w.Header().Set("Content-Type", "text/html")
		buf.WriteTo(w)
	})

	// Favicon
	mux.HandleFunc("/favicon.svg", func(w http.ResponseWriter, r *http.Request) {
		data, err := staticFS.ReadFile("static/favicon.svg")
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "image/svg+xml")
		w.Header().Set("Cache-Control", "public, max-age=86400")
		w.Write(data)
	})

	// Service worker (served from root for scope)
	mux.HandleFunc("/sw.js", func(w http.ResponseWriter, r *http.Request) {
		data, err := staticFS.ReadFile("static/sw.js")
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/javascript")
		w.Header().Set("Service-Worker-Allowed", "/")
		w.Header().Set("Cache-Control", "no-cache")
		w.Write(data)
	})

	// WebSocket endpoint
	mux.HandleFunc("/ws", func(w http.ResponseWriter, r *http.Request) {
		// Auth via query param or header
		authorized := checkAnyAuth(r, cfg)
		if !authorized {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}

		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			log.Printf("websocket upgrade error: %v", err)
			return
		}

		client := &Client{
			hub:      hub,
			conn:     conn,
			send:     make(chan []byte, 256),
			clientID: "unknown",
			state: &ClientState{
				ClientID:  "unknown",
				Features:  []FeatureSummary{},
				Logs:      []LogEntry{},
				Connected: true,
			},
		}

		hub.register <- client
		go client.writePump()
		go client.readPump()
	})
}

func serveSubmitAnswers(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	// Parse request body
	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}
	var req struct {
		Answers []QuestionAnswer `json:"answers"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	// Validate client exists and is connected
	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	// Validate feature exists and is in requested-input stage
	var found bool
	for _, f := range state.Features {
		if f.ID == featureID {
			found = true
			if f.Stage != "requested-input" {
				writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "feature not in requested-input stage"})
				return
			}
			break
		}
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}

	// Send answer_questions message to client via WebSocket
	msg, _ := json.Marshal(map[string]interface{}{
		"type":       "answer_questions",
		"feature_id": featureID,
		"answers":    req.Answers,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func serveAutoMode(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}
	var req struct {
		Mode    string `json:"mode"`
		Enabled bool   `json:"enabled"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}
	if req.Mode != "plan" && req.Mode != "impl" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid mode"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":    "set_auto_mode",
		"mode":    req.Mode,
		"enabled": req.Enabled,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func serveSetAgentForPhase(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}
	var req struct {
		Phase string `json:"phase"`
		Agent string `json:"agent"`
		Model string `json:"model"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}
	if req.Phase == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "phase is required"})
		return
	}
	if req.Agent == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "agent is required"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	// Validate phase against client's config
	if state.Config != nil && len(state.Config.Phases) > 0 {
		validPhase := false
		for _, p := range state.Config.Phases {
			if p.Key == req.Phase {
				validPhase = true
				break
			}
		}
		if !validPhase {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid phase"})
			return
		}
	} else {
		// No config yet - can't validate phase
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "client config not available"})
		return
	}

	// Validate agent against client's available agents (allow "default")
	if req.Agent != "default" && state.Config != nil && len(state.Config.AvailableAgents) > 0 {
		validAgent := false
		for _, a := range state.Config.AvailableAgents {
			if a == req.Agent {
				validAgent = true
				break
			}
		}
		if !validAgent {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid agent"})
			return
		}
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":  "set_agent_for_phase",
		"phase": req.Phase,
		"agent": req.Agent,
		"model": req.Model,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func serveRunScript(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}
	var req struct {
		ScriptID string `json:"script_id"`
		Context  string `json:"context"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}
	if req.ScriptID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "script_id required"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":      "run_script",
		"script_id": req.ScriptID,
		"context":   req.Context,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func serveStartAgent(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}
	var req struct {
		Action string `json:"action"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}
	if req.Action != "plan" && req.Action != "implement" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid action, must be 'plan' or 'implement'"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	// Find feature and validate stage
	var feature *FeatureSummary
	for i := range state.Features {
		if state.Features[i].ID == featureID {
			feature = &state.Features[i]
			break
		}
	}
	if feature == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}

	// Check stage is allowed for this action
	allowedStages, hasAction := state.StageActions[req.Action]
	if !hasAction {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "stage actions not available"})
		return
	}
	stageAllowed := false
	for _, s := range allowedStages {
		if s == feature.Stage {
			stageAllowed = true
			break
		}
	}
	if !stageAllowed {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "feature stage '" + feature.Stage + "' not valid for action '" + req.Action + "'"})
		return
	}

	// Check agent not busy
	if req.Action == "plan" && state.PlanAgent.Running {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "plan agent is already running"})
		return
	}
	if req.Action == "implement" && state.ImplAgent.Running {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "implement agent is already running"})
		return
	}

	// Send start_agent message to client via WebSocket
	msg, _ := json.Marshal(map[string]interface{}{
		"type":       "start_agent",
		"feature_id": featureID,
		"action":     req.Action,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func serveClientPage(w http.ResponseWriter, r *http.Request, hub *Hub, tmpl *template.Template, cfg *Config) {
	path := strings.TrimPrefix(r.URL.Path, "/clients/")
	path = strings.TrimSuffix(path, "/")
	parts := strings.SplitN(path, "/", 2)
	clientID := parts[0]
	if clientID == "" {
		http.NotFound(w, r)
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		http.NotFound(w, r)
		return
	}

	key := r.URL.Query().Get("key")
	authenticated := cfg.DashboardKey == "" || (key != "" && key == cfg.DashboardKey)

	showKeyModal := !authenticated && cfg.DashboardKey != ""

	if !authenticated && cfg.DashboardKey != "" {
		state = &ClientState{}
	}

	// Check for sub-resource (HTMX fragment endpoints)
	if len(parts) == 2 {
		if !authenticated && cfg.DashboardKey != "" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "text/html")
		switch parts[1] {
		case "features":
			activeSlugs := map[string]bool{}
			if state.PlanAgent.Running && state.PlanAgent.Feature != "" {
				activeSlugs[state.PlanAgent.Feature] = true
			}
			if state.ImplAgent.Running && state.ImplAgent.Feature != "" {
				activeSlugs[state.ImplAgent.Feature] = true
			}
			fdata := map[string]interface{}{
				"StageGroups": groupFeaturesByStage(state.Features),
				"Key":         key,
				"ActiveSlugs": activeSlugs,
			}
			if err := tmpl.ExecuteTemplate(w, "feature_stages", fdata); err != nil {
				log.Printf("template error: %v", err)
				http.Error(w, "internal server error", 500)
			}
			return
		case "logs":
			if err := tmpl.ExecuteTemplate(w, "log_entries", state.Logs); err != nil {
				log.Printf("template error: %v", err)
				http.Error(w, "internal server error", 500)
			}
			return
		}
		http.NotFound(w, r)
		return
	}

	activeSlugs := map[string]bool{}
	if state.PlanAgent.Running && state.PlanAgent.Feature != "" {
		activeSlugs[state.PlanAgent.Feature] = true
	}
	if state.ImplAgent.Running && state.ImplAgent.Feature != "" {
		activeSlugs[state.ImplAgent.Feature] = true
	}

	data := map[string]interface{}{
		"ClientID":      state.ClientID,
		"Features":      state.Features,
		"StageGroups":   groupFeaturesByStage(state.Features),
		"Logs":          state.Logs,
		"LastSeen":      state.LastSeen,
		"Connected":     state.Connected,
		"PlanAgent":     state.PlanAgent,
		"ImplAgent":     state.ImplAgent,
		"AutoPlan":      state.AutoPlan,
		"AutoImpl":      state.AutoImpl,
		"Key":           key,
		"Authenticated": authenticated,
		"ShowKeyModal":  showKeyModal,
		"Boards":        boardsFromClients([]ClientState{*state}),
		"Config":        state.Config,
		"ActiveSlugs":   activeSlugs,
	}
	w.Header().Set("Content-Type", "text/html")
	if err := tmpl.ExecuteTemplate(w, "client.html", data); err != nil {
		log.Printf("template error: %v", err)
		http.Error(w, "internal server error", 500)
	}
}

func serveClientConfig(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}
	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if state.Config == nil {
		writeJSON(w, http.StatusOK, map[string]interface{}{})
		return
	}
	writeJSON(w, http.StatusOK, state.Config)
}

func serveClientJSON(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}
	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	writeJSON(w, http.StatusOK, state)
}

func serveClientLogs(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if !checkAPIAuth(r, cfg.APIKey) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}
	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	writeJSON(w, http.StatusOK, state.Logs)
}

type createIdeaData struct {
	Title                 string `json:"title"`
	Board                 string `json:"board"`
	Description           string `json:"description"`
	Type                  string `json:"type"`
	DoneScript            string `json:"done_script"`
	RequiresHumanApproval bool   `json:"requires_human_approval"`
}

func serveCreateIdea(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}

	var idea createIdeaData
	if err := json.Unmarshal(body, &idea); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	if strings.TrimSpace(idea.Title) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "title is required"})
		return
	}
	if strings.TrimSpace(idea.Board) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "board is required"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	ideaType := idea.Type
	if ideaType == "" {
		ideaType = "feature"
	}
	msg, _ := json.Marshal(map[string]interface{}{
		"type":                    "create_idea",
		"title":                   idea.Title,
		"board":                   idea.Board,
		"description":             idea.Description,
		"item_type":               ideaType,
		"done_script":             idea.DoneScript,
		"requires_human_approval": idea.RequiresHumanApproval,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func serveScripts(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}

	writeJSON(w, http.StatusOK, state.Scripts)
}

type moveFeatureData struct {
	TargetStage string `json:"target_stage"`
	Reason      string `json:"reason,omitempty"`
}

func serveMoveFeature(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}

	var req moveFeatureData
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	if req.TargetStage == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "target_stage is required"})
		return
	}

	if !isValidStage(req.TargetStage) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid target stage"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	var found bool
	for _, f := range state.Features {
		if f.ID == featureID {
			found = true
			break
		}
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}

	requestID, resultCh := hub.CreateMoveRequest(clientID)

	payload := map[string]interface{}{
		"type":         "move_feature",
		"feature_id":   featureID,
		"target_stage": req.TargetStage,
		"request_id":   requestID,
	}
	if req.Reason != "" {
		payload["reason"] = req.Reason
	}
	msg, _ := json.Marshal(payload)
	if err := hub.SendToClient(clientID, msg); err != nil {
		hub.ResolveMoveRequest(requestID, moveResult{Success: false, Error: err.Error()})
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	select {
	case result := <-resultCh:
		if result.Success {
			writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
		} else {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": result.Error})
		}
	case <-time.After(hub.config.MoveTimeout):
		hub.ResolveMoveRequest(requestID, moveResult{Success: false, Error: "timeout"})
		writeJSON(w, http.StatusGatewayTimeout, map[string]string{"error": "move timed out"})
	}
}

func serveEditDescription(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}

	var req struct {
		Description string `json:"description"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	var feature *FeatureSummary
	var found bool
	for _, f := range state.Features {
		if f.ID == featureID {
			feature = &f
			found = true
			break
		}
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}
	if feature.Stage != "ideas" {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "can only edit description in ideas stage"})
		return
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":        "edit_description",
		"feature_id":  featureID,
		"description": req.Description,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

type editDoneScriptRequest struct {
	DoneScript string `json:"done_script"`
}

func serveEditDoneScript(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}

	var req editDoneScriptRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	var found bool
	for _, f := range state.Features {
		if f.ID == featureID {
			found = true
			break
		}
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":        "edit_done_script",
		"feature_id":  featureID,
		"done_script": req.DoneScript,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

type editIdeationPromptRequest struct {
	IdeationPrompt string `json:"ideation_prompt"`
}

func serveEditIdeationPrompt(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}

	var req editIdeationPromptRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	var found bool
	for _, f := range state.Features {
		if f.ID == featureID {
			found = true
			break
		}
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":            "edit_ideation_prompt",
		"feature_id":      featureID,
		"ideation_prompt": req.IdeationPrompt,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

type editTitleRequest struct {
	Title string `json:"title"`
}

func serveEditTitle(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}

	var req editTitleRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	var feature *FeatureSummary
	var found bool
	for _, f := range state.Features {
		if f.ID == featureID {
			feature = &f
			found = true
			break
		}
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}
	if feature.Stage != "ideas" {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "can only edit title in ideas stage"})
		return
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":       "edit_title",
		"feature_id": featureID,
		"title":      req.Title,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

type editItemTypeRequest struct {
	ItemType string `json:"type"`
}

func serveEditItemType(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}

	var req editItemTypeRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	if req.ItemType != "feature" && req.ItemType != "bug" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "type must be 'feature' or 'bug'"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	var featureItemType *FeatureSummary
	var found bool
	for _, f := range state.Features {
		if f.ID == featureID {
			featureItemType = &f
			found = true
			break
		}
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}
	if featureItemType.Stage != "ideas" {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "can only edit type in ideas stage"})
		return
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":       "edit_item_type",
		"feature_id": featureID,
		"item_type":  req.ItemType,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func serveEditDependsOn(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "failed to read body"})
		return
	}

	var req struct {
		DependsOn []string `json:"depends_on"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	state, ok := hub.GetClient(clientID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found"})
		return
	}
	if !state.Connected {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "client not connected"})
		return
	}

	var found bool
	for _, f := range state.Features {
		if f.ID == featureID {
			found = true
			break
		}
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "feature not found"})
		return
	}

	msg, _ := json.Marshal(map[string]interface{}{
		"type":       "edit_depends_on",
		"feature_id": featureID,
		"depends_on": req.DependsOn,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func serveRestartTUI(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if !checkAnyAuth(r, cfg) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}

	msg, _ := json.Marshal(map[string]string{"type": "restart_tui"})
	err := hub.SendToClient(clientID, msg)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "client not found or disconnected"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "restart signal sent"})
}
