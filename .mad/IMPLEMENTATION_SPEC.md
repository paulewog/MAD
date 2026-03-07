# Implementation Spec: Bug - cannot create new item from webui

## Bug 1: create_idea silently fails

### Root Cause
The `_handle_create_idea` callback in `tui.py` uses `call_from_thread()` but is invoked from within the Textual asyncio event loop (via `_handle_incoming` in the reconnect loop). `call_from_thread` is designed for cross-thread calls, not same-thread async calls.

### Task 1.1: Add await for on_idea_created callback in server_client.py
- **File**: `/home/paule/MAD/pipeline/server_client.py`
- **Line**: ~252
- **Change**: Add `await` when calling `self._on_idea_created` callback
- **Current code**:
  ```python
  self._on_idea_created(title, board, description)
  ```
- **New code**:
  ```python
  await self._on_idea_created(title, board, description)
  ```
- **Dependencies**: None

### Task 1.2: Make _handle_create_idea async in tui.py
- **File**: `/home/paule/MAD/pipeline/tui.py`
- **Lines**: ~1476-1493
- **Change**: 
  1. Change method signature from `def _handle_create_idea(self, title: str, board: str, description: str) -> None:` to `async def _handle_create_idea(self, title: str, board: str, description: str) -> None:`
  2. Remove the `call_from_thread` wrapper - directly execute the code inside `_do_create`
  3. Change `asyncio.create_task(...)` to `await ...` for consistency with `_handle_server_answers` pattern
- **Current code**:
  ```python
  def _handle_create_idea(self, title: str, board: str, description: str) -> None:
      def _do_create():
          try:
              feature = FeatureFile.create(board, title, description)
              logger.info(f"Created new idea: {feature.title} ({feature.id}) in board {board}")
              self._refresh_board(self.active_board)
              if self._server_client and self._server_client.connected:
                  features = self._load_features(self.active_board)
                  asyncio.create_task(self._server_client.push_state(
                      features, self._plan_agent_status, self._implement_agent_status,
                      auto_plan_enabled=self.auto_plan_enabled,
                      auto_impl_enabled=self.auto_implement_enabled,
                  ))
          except Exception as e:
              logger.warning(f"create_idea: failed to create idea: {e}")
      self.call_from_thread(_do_create)
  ```
- **New code**:
  ```python
  async def _handle_create_idea(self, title: str, board: str, description: str) -> None:
      try:
          feature = FeatureFile.create(board, title, description)
          logger.info(f"Created new idea: {feature.title} ({feature.id}) in board {board}")
          self._refresh_board(self.active_board)
          if self._server_client and self._server_client.connected:
              features = self._load_features(self.active_board)
              await self._server_client.push_state(
                  features, self._plan_agent_status, self._implement_agent_status,
                  auto_plan_enabled=self.auto_plan_enabled,
                  auto_impl_enabled=self.auto_implement_enabled,
              )
      except Exception as e:
          logger.warning(f"create_idea: failed to create idea: {e}")
  ```
- **Dependencies**: Task 1.1 must be completed first (awaits require callback to be async)

---

## Bug 2: Board field is free-text in client.html

### Root Cause
- The New Idea modal in `client.html:305` uses `<input type="text">` instead of a `<select>` dropdown
- `serveClientPage` in `api.go` doesn't pass a `Boards` list to the template

### Task 2.1: Add Boards to template data in api.go
- **File**: `/home/paule/MAD/server/api.go`
- **Line**: ~680 (in the `data` map in `serveClientPage`)
- **Change**: Add `"Boards": boardsFromClients(hub.ListClients())` to the template data
- **Current code** (around line 667-681):
  ```go
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
  }
  ```
- **New code**:
  ```go
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
      "Boards":        boardsFromClients(hub.ListClients()),
  }
  ```
- **Dependencies**: None

### Task 2.2: Replace input with select dropdown in client.html
- **File**: `/home/paule/MAD/server/templates/client.html`
- **Line**: ~305
- **Change**: Replace `<input type="text">` with `<select>` dropdown populated via `{{range .Boards}}`
- **Current code**:
  ```html
  <input type="text" id="new-idea-board" placeholder="e.g., default" style="width:100%;padding:0.5rem;background:#0d1117;border:1px solid #30363d;border-radius:4px;color:#c9d1d9;font-size:0.9rem;font-family:inherit">
  ```
- **New code**:
  ```html
  <select id="new-idea-board" style="width:100%;padding:0.5rem;background:#0d1117;border:1px solid #30363d;border-radius:4px;color:#c9d1d9;font-size:0.9rem;font-family:inherit">
      <option value="">Select a board...</option>
      {{range .Boards}}<option value="{{.}}">{{.}}</option>{{end}}
  </select>
  ```
- **Reference**: See `/home/paule/MAD/server/templates/index.html` lines 304-307 for the correct pattern
- **Dependencies**: Task 2.1 must be completed first (Boards data must be available in template)

---

## Implementation Order

1. **Task 2.1**: Add Boards to api.go (no dependencies)
2. **Task 2.2**: Replace input with select in client.html (depends on 2.1)
3. **Task 1.1**: Add await in server_client.py (no dependencies)
4. **Task 1.2**: Make _handle_create_idea async in tui.py (depends on 1.1)
