# MAD Refactoring Tasks

## Bug vs Feature Workflow (USER REQUESTED)
**Status:** DONE

Added `type` field ("feature" or "bug") to FeatureFile, `plan-bug.md` prompt,
`--type` CLI flag, and Go/WebSocket plumbing. `run_planning()` selects template based on type.

---

## High Priority

### 1. Fix Config Loading at Module Import
**Status:** DONE — Lazy `_ensure_logging()` in runner.py and phases.py

### 2. Add Config Validation
**Status:** DONE — `_load()` in config.py now catches JSONDecodeError and FileNotFoundError

---

## Completed / Not Issues

### Race Condition in Go Hub
**Status:** NOT A BUG - Go uses actor pattern, mutex held intentionally

The conn.Close() call while holding the mutex is correct in Go's single-goroutine Run() loop pattern. The mutex protects concurrent readers, not the Run() goroutine itself.

### Stage Definitions
**Status:** ACCEPTABLE - Different definitions serve different purposes

- `STAGES` = all valid stages (used for validation/listing)
- `HUMAN_STAGES` = UI concept (which stages need human attention)  
- `stageOrder` in Go = Go server needs its own copy (different language)

These don't need to be consolidated.

### PIPELINE_STAGES Dead Code
**Status:** REMOVED - Claude just removed this

The buggy dead code with duplicate "reviewing-plan" has been removed.

### Large Files
**Status:** NOT ACTIONABLE - File size alone isn't a problem

The files are large because they do a lot. Splitting without specific goals would scatter related code.

---

## Code Quality Checklist

- [x] Remove dead code: PIPELINE_STAGES (done)
- [x] Config created lazily, not at module import (done)
- [x] Config loading validates JSON and gives clear errors (done)
- [ ] No bare `except:` or `recover()` without logging
- [ ] All exceptions properly handled and propagated

---

## Notes

- All concrete tasks completed
- Bug vs Feature workflow implemented (type field, plan-bug.md, CLI --type flag)
- Test after each change
