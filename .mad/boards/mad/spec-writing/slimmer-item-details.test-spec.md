# Test Spec: Slimmer Item Details with Collapsible Sections

## 1. Behaviors That MUST Be Verified

### 1.1 FeatureSummary struct fields in hub.go

- **Behavior:** The `FeatureSummary` struct in `server/hub.go` contains fields for Plan, ImplSpec, TestSpec, and ImplNotes.
- **Success:** Each new field has the correct JSON tags: `plan`, `impl_spec`, `test_spec`, `impl_notes` with `omitempty` annotation.
- **Expected output:** The struct can be serialized and deserialized with these fields without error.

### 1.2 Pipeline server_client sends new fields to server

- **Behavior:** When the Python pipeline client pushes state to the server, it includes the plan, impl_spec, test_spec, and impl_notes fields for each feature.
- **Success:** The JSON sent to the server contains these four fields when they have non-empty values.
- **Expected output:** Fields with empty strings are omitted from the JSON (rely on omitempty).

### 1.3 Web UI displays collapsible sections in Item Details pane

- **Behavior:** When a feature is selected in the web UI, the details pane renders with collapsible sections.
- **Success criteria:**
  - Title and Description are always visible (not in collapsible).
  - History section is rendered and expanded by default.
  - Plan section is rendered and collapsed by default.
  - Implementation Spec section is rendered and collapsed by default.
  - Test Spec section is rendered and collapsed by default.
  - Implementation Notes section is rendered and collapsed by default.
  - Questions section is rendered and collapsed by default (unless stage is 'requested-input').

### 1.4 Questions section conditional rendering in web UI

- **Behavior:** The Questions section renders differently based on the feature's stage.
- **Success criteria:**
  - When stage equals 'requested-input', questions render inline (not in a collapsible section).
  - When stage is anything other than 'requested-input', questions render as a collapsible section, collapsed by default.

### 1.5 Empty sections display placeholder text

- **Behavior:** When a section (Plan, ImplSpec, TestSpec, ImplNotes, Questions) has no content, the section header still renders with the toggle button, but the content is collapsed.
- **Success:** Inside the collapsed section, the text "No [section] provided" appears as placeholder.
- **Expected output:** The placeholder text uses the appropriate section name (e.g., "No plan provided", "No implementation spec provided").

### 1.6 Web UI toggle functionality

- **Behavior:** Clicking on a collapsible section header toggles the section between expanded and collapsed states.
- **Success:** The section expands to show content when clicked, and collapses when clicked again.
- **Expected output:** The toggle icon rotates appropriately when the section expands and collapses.

### 1.7 Web UI accessibility attributes

- **Behavior:** Collapsible section toggle buttons have proper accessibility attributes.
- **Success criteria:**
  - Toggle buttons have `aria-expanded` attribute that reflects the current state (true/false).
  - Toggle buttons have `aria-controls` attribute linking to the content section ID.
  - Keyboard navigation works: pressing Enter or Space toggles the section.

### 1.8 Web UI long content handling

- **Behavior:** When collapsible section content is long, the container allows scrolling.
- **Success:** The collapsible content container has `max-height` set and `overflow-y: auto` for scrolling.
- **Expected output:** Smooth transition animation occurs when expanding/collapsing.

### 1.9 TUI collapsible sections rendering

- **Behavior:** The TUI renders Item Details with collapsible sections using Textual's Collapsible widget.
- **Success criteria:**
  - Title and Description are always visible (rendered outside collapsibles).
  - History section is expanded by default.
  - Plan, Implementation Spec, Test Spec, Implementation Notes, and Questions sections are collapsed by default.

### 1.10 TUI empty section handling

- **Behavior:** When a section has no content in TUI, the section header still displays with collapse icon.
- **Success:** Inside the collapsed section, the text "No [section] provided" displays.

### 1.11 State does not persist across sessions

- **Behavior:** After refreshing the page (web UI) or restarting the TUI, all collapsible sections return to their default states.
- **Success:** History is expanded by default, all other sections are collapsed by default on each new session.
- **Expected output:** No persistence mechanism stores the collapsed/expanded state.

### 1.12 Data attributes in client.html template

- **Behavior:** The HTML template for feature cards includes data attributes for the new fields.
- **Success:** The template has `data-plan`, `data-impl-spec`, `data-test-spec`, and `data-impl-notes` attributes on feature card elements.

---

## 2. Edge Cases

### 2.1 All sections empty

- A feature has no Title, Description, History, Plan, ImplSpec, TestSpec, ImplNotes, or Questions.
- The Item Details pane should show only the section headers with toggles, each displaying the "No [section] provided" placeholder.

### 2.2 Only Title and Description populated

- A feature has Title and Description but no other fields.
- Only Title and Description display (always visible). All collapsible sections show with toggles and "No [section] provided" placeholders.

### 2.3 Very long content in sections

- A section contains text that is thousands of characters long.
- The collapsible container scrolls internally without breaking the layout. The expand/collapse transition remains smooth.

### 2.4 Special characters in content

- Plan, ImplSpec, TestSpec, or ImplNotes contain HTML special characters (<, >, &, quotes).
- The web UI properly escapes these characters so they display as text, not rendered HTML.

### 2.5 Rapid toggle clicking

- A user clicks the toggle button rapidly multiple times in succession.
- The UI handles this gracefully without visual glitches or state corruption.

### 2.6 Questions with stage transition

- A feature starts with stage 'requested-input' (questions inline), then transitions to a different stage.
- The questions section should convert to a collapsible section, collapsed by default.

### 2.7 Concurrent state updates

- While a user has a section expanded, a state update arrives from the server (e.g., new plan content added).
- The UI updates the content without unexpectedly collapsing the section (or if it must collapse, does so gracefully).

### 2.8 TUI with all sections empty

- The TUI displays a feature with no content in any section.
- All collapsible sections render with headers and "No [section] provided" text inside.

### 2.9 Browser resize with long content

- The browser window is resized to a narrow width while a section with long content is expanded.
- The scroll behavior works correctly in the new dimensions.

### 2.10 Null/missing fields in JSON

- The server sends feature data with null values for plan, impl_spec, test_spec, or impl_notes.
- The web UI treats null as empty string and renders the "No [section] provided" placeholder.

---

## 3. What Constitutes Failure

### 3.1 Functional failures

- Title or Description are placed inside a collapsible section (they should always be visible).
- History is collapsed by default (should be expanded).
- Plan, ImplSpec, TestSpec, ImplNotes, or Questions are expanded by default (should be collapsed, except History).
- Clicking a toggle does not expand or collapse the section.
- The toggle icon does not rotate or change state visually.
- Empty sections show no content at all (should show placeholder).
- Questions section does not render inline when stage is 'requested-input'.

### 3.2 Data layer failures

- The FeatureSummary struct in hub.go is missing any of the four new fields.
- The Python server_client does not send plan, impl_spec, test_spec, impl_notes to the server.
- The web UI does not read the new data attributes from the HTML template.
- Data is lost during serialization/deserialization (field values become empty).

### 3.3 Accessibility failures

- Toggle buttons lack aria-expanded attribute.
- Toggle buttons lack aria-controls attribute.
- Keyboard navigation (Enter/Space) does not toggle sections.

### 3.4 TUI failures

- The TUI does not use collapsible widgets for sections.
- Title or Description are inside collapsible widgets.
- Sections are not collapsible at all.
- Empty sections do not show placeholder text.

### 3.5 Persistence failures

- Collapsed/expanded state persists after page refresh or TUI restart (should reset to defaults).

### 3.6 Visual/layout failures

- Long content causes the entire page to scroll instead of just the collapsible section.
- No smooth transition animation occurs when expanding/collapsing.
- Sections appear broken or unstyled when collapsed.

---

## 4. Out of Scope

- **Visual styling assertions:** Exact colors, fonts, border styles, spacing, or CSS animations (except as noted for transition behavior).
- **Performance testing:** Load time, render time, or scroll performance benchmarks.
- **Cross-browser testing:** Behavior differences across browsers, devices, or screen sizes beyond basic responsive behavior.
- **TUI framework internals:** Textual framework's internal rendering or event handling.
- **WebSocket reconnection:** Reconnection behavior during state updates.
- **Server-side validation:** Input validation for the new fields on the server.
- **File system operations:** How features store or retrieve data from disk.
- **Auto-mode interactions:** How auto-plan or auto-implement interacts with the collapsible UI.
- **Search or filter functionality:** How search/filter works with collapsed sections.
- **Multi-select behavior:** Behavior when multiple features are selected simultaneously.
- **API endpoint testing:** The new fields' presence in API responses (covered by 1.1-1.3).
- **Keyboard shortcuts in TUI:** Arrow key navigation for expand/collapse (noted as optional in plan).
