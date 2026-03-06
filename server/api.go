package main

import (
	"embed"
	"encoding/json"
	"html/template"
	"io"
	"log"
	"net/http"
	"strings"

	"github.com/gorilla/websocket"
)

var stageOrder = []string{
	"ideas", "plan-inbox", "reviewing-plan", "requested-input", "approved", "spec-writing",
	"implementing", "testing", "review", "final-human-approval", "done", "rejected",
}

// StageGroup holds features for a single stage, preserving order.
type StageGroup struct {
	Stage    string
	Features []FeatureSummary
}

// groupFeaturesByStage returns features organized by stage in canonical order.
// Stages with zero features are included so the UI can show empty columns.
func groupFeaturesByStage(features []FeatureSummary) []StageGroup {
	byStage := make(map[string][]FeatureSummary)
	for _, f := range features {
		byStage[f.Stage] = append(byStage[f.Stage], f)
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

func checkAPIAuth(r *http.Request, apiKey string) bool {
	if apiKey == "" {
		return true
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
		return true
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
	}

	tmpl := template.Must(template.New("").Funcs(funcMap).ParseFS(templateFS, "templates/index.html"))

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
		authenticated := key != "" && (cfg.DashboardKey == "" || key == cfg.DashboardKey)
		clients := hub.ListClients()
		// Aggregate all features across all clients, stamping client ID
		var allFeatures []FeatureSummary
		for _, c := range clients {
			for _, f := range c.Features {
				f.ClientID = c.ClientID
				allFeatures = append(allFeatures, f)
			}
		}
		data := map[string]interface{}{
			"Clients":       clients,
			"Key":           key,
			"Authenticated": authenticated,
			"StageGroups":   groupFeaturesByStage(allFeatures),
		}
		w.Header().Set("Content-Type", "text/html")
		if err := tmpl.ExecuteTemplate(w, "index.html", data); err != nil {
			log.Printf("template error: %v", err)
			http.Error(w, "internal server error", 500)
		}
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
				if sub == "ideas" {
					serveCreateIdea(w, r, hub, cfg, clientID)
					return
				}
				// Match features/{fid}/answers or features/{fid}/start-agent
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
				}
			}
			serveClientJSON(w, r, hub, cfg, clientID)
			return
		}

		if !checkAPIAuth(r, cfg.APIKey) {
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
			}
			if err := tmpl.ExecuteTemplate(w, "client_rows", data); err != nil {
				log.Printf("template error: %v", err)
				http.Error(w, "internal server error", 500)
			}
			return
		}

		writeJSON(w, http.StatusOK, clients)
	}
	mux.HandleFunc("/api/clients", apiClientsHandler)
	mux.HandleFunc("/api/clients/", apiClientsHandler)

	// HTMX fragment: board view (all features grouped by stage)
	mux.HandleFunc("/api/board", func(w http.ResponseWriter, r *http.Request) {
		if !checkDashboardAuth(r, cfg.DashboardKey) {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		clients := hub.ListClients()
		var allFeatures []FeatureSummary
		for _, c := range clients {
			for _, f := range c.Features {
				f.ClientID = c.ClientID
				allFeatures = append(allFeatures, f)
			}
		}
		w.Header().Set("Content-Type", "text/html")
		data := map[string]interface{}{
			"StageGroups": groupFeaturesByStage(allFeatures),
			"Key":         r.URL.Query().Get("key"),
		}
		if err := tmpl.ExecuteTemplate(w, "board_fragment", data); err != nil {
			log.Printf("template error: %v", err)
			http.Error(w, "internal server error", 500)
		}
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
		authorized := false
		if cfg.APIKey == "" {
			authorized = true
		} else {
			if r.URL.Query().Get("api_key") == cfg.APIKey {
				authorized = true
			} else if checkAPIAuth(r, cfg.APIKey) {
				authorized = true
			}
		}
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
	if !checkDashboardAuth(r, cfg.DashboardKey) && !checkAPIAuth(r, cfg.APIKey) {
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
	if !checkDashboardAuth(r, cfg.DashboardKey) && !checkAPIAuth(r, cfg.APIKey) {
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

func serveStartAgent(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string, featureID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkDashboardAuth(r, cfg.DashboardKey) && !checkAPIAuth(r, cfg.APIKey) {
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

	// Check for sub-resource (HTMX fragment endpoints)
	if len(parts) == 2 {
		w.Header().Set("Content-Type", "text/html")
		switch parts[1] {
		case "features":
			fdata := map[string]interface{}{
				"StageGroups": groupFeaturesByStage(state.Features),
				"Key":         key,
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

	data := map[string]interface{}{
		"ClientID":    state.ClientID,
		"Features":    state.Features,
		"StageGroups": groupFeaturesByStage(state.Features),
		"Logs":        state.Logs,
		"LastSeen":    state.LastSeen,
		"Connected":   state.Connected,
		"PlanAgent":   state.PlanAgent,
		"ImplAgent":   state.ImplAgent,
		"AutoPlan":    state.AutoPlan,
		"AutoImpl":    state.AutoImpl,
		"Key":         key,
	}
	w.Header().Set("Content-Type", "text/html")
	if err := tmpl.ExecuteTemplate(w, "client.html", data); err != nil {
		log.Printf("template error: %v", err)
		http.Error(w, "internal server error", 500)
	}
}

func serveClientJSON(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if !checkAPIAuth(r, cfg.APIKey) && !checkDashboardAuth(r, cfg.DashboardKey) {
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
	Title       string `json:"title"`
	Board       string `json:"board"`
	Description string `json:"description"`
}

func serveCreateIdea(w http.ResponseWriter, r *http.Request, hub *Hub, cfg *Config, clientID string) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if !checkDashboardAuth(r, cfg.DashboardKey) && !checkAPIAuth(r, cfg.APIKey) {
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

	msg, _ := json.Marshal(map[string]interface{}{
		"type":        "create_idea",
		"title":       idea.Title,
		"board":       idea.Board,
		"description": idea.Description,
	})
	if err := hub.SendToClient(clientID, msg); err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
