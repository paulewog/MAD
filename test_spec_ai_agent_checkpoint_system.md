# Test Specification: AI Agent Checkpoint System

## Overview

This document specifies the test requirements for the AI Agent Checkpoint System feature. The system allows AI agents to periodically save their progress to checkpoint files so that if they hang, get killed, or hit usage limits, a subsequent agent session can resume where they left off. The feature spans prompt instructions, checkpoint file handling in the runner, cleanup on phase completion and failure, server state updates, and web UI indicators.

---

## 1. Behaviors That MUST Be Verified

### 1.1 Checkpoint Directory Creation

**Behavior:** The `.mad/checkpoints/` directory is created alongside existing `.mad/boards/` and `.mad/logs/` directories.

**Success Criteria:**
- Directory path `.mad/checkpoints/` exists or is created when needed
- Directory is created with appropriate permissions for file writing

**Expected Outputs:**
- Checkpoint files can be written to `.mad/checkpoints/` directory

### 1.2 Checkpoint File Format

**Behavior:** Checkpoint files follow the specified JSON schema with required and optional fields.

**Success Criteria:**
- `feature_slug` field is present and non-empty string
- `phase` field is present and non-empty string
- `last_checkpoint` field contains valid ISO timestamp (when present)
- `completed_steps` field is an array of strings (when present)
- `next_step` is a string (when present)
- `notes` is a string (when present)
- `files_modified` is an array of strings (when present)

**Expected Outputs:**
- Valid JSON file with correct structure
- Optional fields use defaults when missing

### 1.3 Agent Prompts Include Checkpoint Instructions

**Behavior:** Prompt templates for relevant phases include checkpoint writing instructions.

**Success Criteria:**
- `implement.md` prompt contains checkpoint instruction block
- `write-tests.md` prompt contains checkpoint instruction block
- `plan-headless.md` prompt contains checkpoint instruction block
- `impl-spec.md` prompt contains checkpoint instruction block
- `test-spec.md` prompt contains checkpoint instruction block

**Expected Outputs:**
- Each prompt includes instruction to write checkpoint after completing meaningful work
- Each prompt specifies correct feature slug and feature ID

### 1.4 Feature Slug and ID Passed to Templates

**Behavior:** Prompt templates receive correct feature_slug and feature_id values.

**Success Criteria:**
- `run_implementing()` passes feature.slug and feature.id to template
- `run_planning()` passes feature.slug and feature.id to template
- `run_spec_writing()` passes feature.slug and feature.id to template
- `run_writing_tests()` passes feature.slug and feature.id to template
- `run_fix_feedback()` passes feature.slug and feature.id to template

**Expected Outputs:**
- Templates can use these values in checkpoint instruction block

### 1.5 Runner Reads Checkpoint on Resume

**Behavior:** When a checkpoint exists, the runner reads it and injects resume context into the agent prompt.

**Success Criteria:**
- `_read_checkpoint()` method exists in runner.py
- Method is called in `headless()` after building full_prompt
- Checkpoint file is read from `.mad/checkpoints/<feature-slug>.checkpoint.json`
- Resume context is appended to the agent prompt
- Method returns None when checkpoint does not exist

**Expected Outputs:**
- Agent receives context about previous progress
- Resume context includes completed steps, next step, notes, and modified files

### 1.6 Checkpoint Validation

**Behavior:** Runner validates checkpoint file for required fields before using it.

**Success Criteria:**
- Missing `feature_slug` causes checkpoint to be skipped
- Missing `phase` causes checkpoint to be skipped
- Invalid JSON causes checkpoint to be skipped
- Warning is logged when checkpoint is invalid

**Expected Outputs:**
- Valid checkpoints are used for resume context
- Invalid checkpoints are logged and ignored gracefully

### 1.7 Resume Context Format

**Behavior:** Resume context is formatted as readable text for the agent.

**Success Criteria:**
- Context includes "Resuming from checkpoint" header
- Completed steps are listed
- Next step is stated
- Notes are included when present
- Files modified are listed when present
- Instruction to not redo completed work is included

**Expected Outputs:**
- Agent can understand what was done and what to do next

### 1.8 Checkpoint Deletion on Phase Completion

**Behavior:** Checkpoint files are deleted when a phase completes successfully.

**Success Criteria:**
- `_delete_checkpoint()` helper function exists in phases.py
- Called at end of `run_planning()` after moving to next stage
- Called at end of `run_spec_writing()` after moving to next stage
- Called at end of `run_implementing()` after moving to next stage
- Called at end of `run_writing_tests()` after moving to next stage
- Called at end of `run_review_impl()` after moving to next stage

**Expected Outputs:**
- No stale checkpoint remains after successful phase completion

### 1.9 Checkpoint Deletion on Failure

**Behavior:** Checkpoint files are deleted when errors occur in phase execution.

**Success Criteria:**
- `_delete_checkpoint()` called in exception handling blocks
- Called in `run_pipeline()` except blocks
- Called in `run_pipeline_from_implementing()` except blocks

**Expected Outputs:**
- Failed phases clean up their checkpoints
- Next agent session does not resume from failed phase

### 1.10 Server State Includes Checkpoint Status

**Behavior:** Server state updates include checkpoint information for each feature.

**Success Criteria:**
- `push_state()` or state update mechanism checks for checkpoint files
- Each feature in state includes checkpoint field when present
- Checkpoint field contains: exists (boolean), last_checkpoint (timestamp), completed_steps_count (number), next_step (string)
- Reading checkpoint files is wrapped in try/except

**Expected Outputs:**
- Server can report which features have active checkpoints

### 1.11 Web UI Displays Checkpoint Indicator

**Behavior:** Web UI shows checkpoint status for features that have active checkpoints.

**Success Criteria:**
- `index.html` template shows checkpoint indicator
- `client.html` template shows checkpoint indicator
- Indicator displays last checkpoint time
- Indicator displays next step summary

**Expected Outputs:**
- Users can see which features have checkpoint resume data available

### 1.12 Best-Effort Checkpoint Writes

**Behavior:** Checkpoint write failures do not block agent work.

**Success Criteria:**
- Agents instructed to continue if checkpoint write fails
- Checkpoint failures do not raise exceptions in agent workflow
- Agents log or handle write failures gracefully

**Expected Outputs:**
- Agent work proceeds even when checkpoint writes fail

---

## 2. Edge Cases

### 2.1 Missing Optional Checkpoint Fields

**Scenario:** Checkpoint file exists but omits optional fields like completed_steps, notes, or files_modified.

**Expected Behavior:** System uses default empty values (empty list for arrays, empty string for strings) and continues normally.

**Test:** Create checkpoint with only required fields (feature_slug, phase), verify runner handles gracefully.

### 2.2 Corrupted JSON in Checkpoint File

**Scenario:** Checkpoint file contains invalid JSON.

**Expected Behavior:** Runner logs warning, skips invalid checkpoint, returns None for resume context. Agent starts fresh without checkpoint.

**Test:** Write malformed JSON to checkpoint file, verify runner logs warning and continues.

### 2.3 Empty Checkpoint File

**Scenario:** Checkpoint file exists but is empty (0 bytes).

**Expected Behavior:** Treated as invalid JSON, logged and skipped.

**Test:** Create empty checkpoint file, verify runner handles gracefully.

### 2.4 Checkpoint with Non-Array Fields

**Scenario:** completed_steps or files_modified fields contain non-array values (string, number, object).

**Expected Behavior:** Type validation uses .get() with defaults, treating unexpected types as empty collections.

**Test:** Write checkpoint with completed_steps as string instead of array, verify runner handles.

### 2.5 Invalid Timestamp Format

**Scenario:** last_checkpoint field contains non-ISO format string.

**Expected Behavior:** Field is treated as string, no validation required. Display shows whatever is stored.

**Test:** Write checkpoint with invalid timestamp format, verify it passes through.

### 2.6 Checkpoint for Non-Existent Feature

**Scenario:** Checkpoint file exists but refers to a feature that no longer exists in the system.

**Expected Behavior:** System handles gracefully - checkpoint may be orphaned. Cleanup on feature deletion not required for this feature.

**Test:** Create checkpoint for non-existent feature, verify system doesn't crash.

### 2.7 Checkpoint Directory Permissions Denied

**Scenario:** User running agent lacks write permissions to .mad/checkpoints/ directory.

**Expected Behavior:** Agent continues working per best-effort instruction. Checkpoint not written but work proceeds.

**Test:** Set directory to read-only, verify agent completes work without checkpoint.

### 2.8 Disk Full During Checkpoint Write

**Scenario:** Disk is full when agent attempts to write checkpoint.

**Expected Behavior:** Write fails, agent continues work per best-effort instruction. No exception raised.

**Test:** Simulate disk full, verify agent continues without checkpoint.

### 2.9 Concurrent Checkpoint Access

**Scenario:** Two agents somehow attempt to access checkpoint for same feature simultaneously.

**Expected Behavior:** Pipeline lock prevents concurrent access per feature. Only one agent processes a feature at a time.

**Test:** Verify pipeline lock semantics prevent concurrent feature processing.

### 2.10 Very Large Checkpoint Data

**Scenario:** completed_steps array contains hundreds of entries, or notes field is extremely long.

**Expected Behavior:** System handles large data gracefully. Performance may degrade but no crashes.

**Test:** Write checkpoint with very large completed_steps array (100+ items), verify read works.

### 2.11 Special Characters in Optional Fields

**Scenario:** notes or next_step contain special characters, quotes, or JSON-like content.

**Expected Behavior:** Stored and retrieved as plain strings. No injection or parsing issues.

**Test:** Write checkpoint with quotes, newlines, and special characters in notes, verify round-trip.

### 2.12 Runner Called with Default Feature Slug

**Scenario:** headless() is called with item_name as 'default' or None.

**Expected Behavior:** _read_checkpoint() returns None, no checkpoint attempt made.

**Test:** Call headless with default feature, verify no checkpoint path errors.

### 2.13 Phase Value Mismatch

**Scenario:** Checkpoint phase differs from current running phase.

**Expected Behavior:** Resume context includes checkpoint phase information. Agent decides how to handle.

**Test:** Create checkpoint with phase="spec-test", run implementing phase, verify resume includes phase info.

### 2.14 Feature Slug with Hyphens

**Scenario:** Feature slug contains hyphens (e.g., "ai-agent-checkpoint-system").

**Expected Behavior:** Hyphens are valid in filenames, checkpoint file created with hyphenated name.

**Test:** Verify checkpoint file naming uses slug directly including hyphens.

---

## 3. What Constitutes Failure

### 3.1 Success Criteria for Test Failure

A test fails when any of the following occurs:

**Functional Failures:**
- Checkpoint directory not created when needed
- Checkpoint files not written in correct JSON format
- Runner fails to read existing checkpoint
- Resume context not injected into agent prompt
- Checkpoints not deleted on phase completion
- Checkpoints not deleted on phase failure
- Server state missing checkpoint information
- Web UI not displaying checkpoint indicators

**Validation Failures:**
- Required fields (feature_slug, phase) not validated
- Invalid JSON causes crash instead of graceful handling
- Missing required fields not detected and skipped

**Data Integrity Failures:**
- Checkpoint data corrupted during write/read
- Optional fields cause exceptions when missing
- Checkpoint data lost between phases incorrectly

**Error Handling Failures:**
- Checkpoint write failures crash agent workflow
- Corrupted checkpoints cause runner to crash
- Missing checkpoint directory causes unhandled exception

### 3.2 Expected Error Messages

| Scenario | Expected Behavior |
|----------|------------------|
| Missing required feature_slug | Warning logged, checkpoint skipped |
| Missing required phase | Warning logged, checkpoint skipped |
| Invalid JSON in checkpoint | Warning logged, checkpoint skipped |
| Cannot read checkpoint file | Warning logged, continue without checkpoint |
| Directory creation fails | Agent continues without checkpoint |

### 3.3 Rollback Behavior

**Scenario:** Operation fails partway through checkpoint operations.

**Expected Behavior:**
- If checkpoint write fails, agent continues without checkpoint (best-effort)
- If checkpoint read fails, agent starts fresh without resume context
- No partial checkpoint files left behind
- Failed writes do not corrupt existing checkpoints
- Overwrite mode ensures only one checkpoint exists per feature

**No automatic recovery needed** - checkpoint system is best-effort by design. Failures should not block agent work.

---

## 4. Out of Scope

The following are explicitly NOT tested in this specification:

### 4.1 Agent Prompt Content
- The actual content of agent responses
- Whether agents correctly follow checkpoint instructions
- How agents determine "meaningful units of work"
- Agent decision-making about what to include in completed_steps

### 4.2 Timing and Performance
- How frequently agents write checkpoints
- Time taken to read/write checkpoint files
- Performance impact on agent response time
- Checkpoint file size limits

### 4.3 Persistence and Storage
- Long-term checkpoint file retention
- Checkpoint backup strategies
- Disk space management
- File system-specific behaviors

### 4.4 Cross-Feature Interactions
- Checkpoints for multiple features simultaneously
- Checkpoint interactions between different boards
- Migration of checkpoints between environments

### 4.5 UI/Visual Details
- Exact visual styling of checkpoint indicators
- Animation or transition effects
- Responsive design on different screen sizes
- Browser-specific rendering

### 4.6 Security
- Checkpoint file access permissions
- Authentication for checkpoint operations
- Input sanitization in checkpoint content
- SQL injection (not applicable - no database)

### 4.7 Network Conditions
- WebSocket message delivery timing
- Server-client synchronization
- Network failure handling during state updates

### 4.8 Logging and Monitoring
- Log message formats
- Metrics collection for checkpoint operations
- Alerting on checkpoint failures

### 4.9 Manual Intervention
- User ability to delete checkpoints manually
- User ability to view checkpoint contents
- User override of checkpoint behavior

### 4.10 Historical Data
- Checkpoint history or versioning
- Audit trail of checkpoint changes
- Comparison of checkpoints over time

---

## Test Summary

This specification covers the checkpoint system from prompt instructions through file operations, runner logic, cleanup handling, server state updates, and web UI display. All required behaviors from the feature plan are verified, with edge cases covering malformed data, missing fields, and failure scenarios. The out-of-scope items focus on aspects that should be tested separately or are not relevant to this feature.

**Key Verification Points:**
1. Checkpoint directory exists or is created
2. Checkpoint files follow JSON schema with required and optional fields
3. All relevant prompt templates include checkpoint instructions
4. Runner reads and validates checkpoint files
5. Resume context is properly formatted and injected
6. Checkpoints deleted on successful phase completion
7. Checkpoints deleted on phase failure
8. Server state includes checkpoint status
9. Web UI displays checkpoint indicators
10. Checkpoint failures are handled gracefully without blocking work
