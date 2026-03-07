# MAD Application Analysis

## Overview

MAD (Machine-Assisted Development) is a pipeline system for managing feature development through multiple stages. It consists of:

1. **Python Pipeline** (`pipeline/`) - Core logic for feature management and AI agent orchestration
2. **Go Server** (`server/`) - Web server with WebSocket support for real-time dashboard
3. **Web UI** - HTML/JS dashboard using HTMX for partial page updates

## Architecture

### Python Pipeline Components

| File | Purpose |
|------|---------|
| `state.py` | FeatureFile class - JSON-based feature storage |
| `phases.py` | Phase execution logic (planning, implementing, review, etc.) |
| `runner.py` | Agent runner - executes AI agents for each phase |
| `config.py` | Configuration loader |
| `server_client.py` | WebSocket client - pushes state to Go server |
| `tui.py` | Textual-based terminal UI |

### Go Server Components

| File | Purpose |
|------|---------|
| `api.go` | HTTP routes and handlers |
| `hub.go` | WebSocket hub and client state management |
| `config.go` | Server configuration |

### Data Flow

1. **Feature Storage**: Features stored as JSON files in `.mad/boards/<board>/<stage>/`
2. **TUI**: Loads features via `FeatureFile.list_all()`, displays in kanban-style UI
3. **Server Push**: TUI sends state via WebSocket (`server_client.py` → `hub.go`)
4. **Dashboard**: Server aggregates features from all clients, serves via HTTP

### Stage Flow

```
ideas → plan-inbox → reviewing-plan → approved → spec-writing → implementing → testing → review → final-human-approval → done
         ↑                                                                                                   ↓
         ←←←←←←←←← (rejected or feedback from review) ←←←←←←←←←←←←←←←←←←←←←←←←←←←
```

## Key Data Structures

### FeatureFile (Python)
- `id`, `title`, `board`, `current_stage` (derived from path)
- `description`, `plan`, `impl_spec`, `test_spec`, `impl_notes`
- `history`, `questions`

### FeatureSummary (Go)
```go
type FeatureSummary struct {
    Title            string
    Stage            string
    Board            string
    Created          string
    ID               string
    Description      string
    History          []HistoryEntry
    Questions        []QuestionAnswer
    AvailableActions []string
    ClientID         string
    Plan             string
    ImplSpec         string
    TestSpec         string
    ImplNotes        string
}
```

### StageGroup (Go)
```go
type StageGroup struct {
    Stage    string
    Features []FeatureSummary
}
```

## WebSocket Protocol

### Client → Server Messages

1. **register**: Initial registration with client_id
2. **state_update**: Push current feature state
   - `features`: List of FeatureSummary
   - `logs`: Log entries
   - `plan_agent`, `impl_agent`: Agent status
   - `auto_plan`, `auto_impl`: Auto-mode flags
   - `stage_actions`: Available actions per stage

### Server → Client Messages

1. **answer_questions**: Web UI submitted answers
2. **set_auto_mode**: Toggle auto-plan/auto-impl
3. **start_agent**: Trigger plan/implement from web UI
4. **create_idea**: Create new idea from web UI
5. **move_feature**: Move feature to different stage

## API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/` | Main dashboard (HTML) |
| `/clients/{id}` | Client-specific view |
| `/api/clients` | List all clients (JSON) |
| `/api/board` | Aggregate board view (HTML fragment) |
| `/ws` | WebSocket endpoint |

## Phase Configuration (PHASE_CONFIG)

| Phase | Runner Phase | History Tag | Template |
|-------|--------------|-------------|----------|
| planning | planning | PLANNING | plan-headless.md |
| reviewing_plan | reviewing_plan | PLAN_REVIEW | review-plan.md |
| spec_impl | spec_writing | SPEC_WRITING | impl-spec.md |
| spec_test | spec_writing | SPEC_WRITING | test-spec.md |
| implementing | implementing | IMPLEMENTING | implement.md |
| fix_feedback | fix_feedback | FIX_FEEDBACK | fix-feedback.md |
| writing_tests | testing | TESTING | write-tests.md |
| review_impl | review | REVIEW | review-impl.md |

## Templates

Templates are stored in `pipeline/prompts/` and include placeholders:
- `{title}`, `{plan}`, `{description}`
- `{feature_slug}`, `{feature_id}`, `{phase}`
- `{checkpoint_instructions}` (injected from `_checkpoint-instructions.md`)

## Testing

Tests are located in `pipeline/test_*.py`:
- `test_server_client.py` - WebSocket client tests
- `test_start_agent_e2e.py` - End-to-end agent execution tests
- `test_auto_mode.py` - Auto-mode tests
- `test_create_idea.py` - Idea creation tests
- `test_consolidate_phase_execution.py` - Phase consolidation tests

## Issue Investigation Notes

The current issue involves:
1. Aggregate board view not aggregating properly
2. Client views showing only single phase (ideas)
3. Phases showing item counts but no items rendered

**Likely root causes to investigate:**
1. WebSocket message format mismatch between Python client and Go server
2. FeatureSummary JSON field mapping issue in hub.go handleMessage
3. groupFeaturesByStage() not correctly grouping by stage
4. Template rendering issue in board_fragment or feature_stages

**Key files to check:**
- `server/hub.go:228-279` - state_update message handling
- `server/api.go:325-350` - /api/board endpoint
- `server/api.go:55-80` - groupFeaturesByStage function
- `server/templates/index.html:637-658` - board_fragment template
- `server/templates/client.html:780-803` - feature_stages template
- `pipeline/server_client.py:116-195` - push_state method
