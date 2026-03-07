# MAD Refactoring Tasks

## Bug vs Feature Workflow (USER REQUESTED)

### Problem
When an "idea" is actually a "bug report," the AI agents incorrectly try to find root cause instead of gathering information from the human.

### Solution
1. Add `type` field to idea creation: `"feature"` (default) or `"bug"`
2. Create `plan-bug.md` prompt template that emphasizes information gathering
3. Modify `run_planning()` in `phases.py` to use different prompt based on idea type
4. Bug prompts should ask about: observed behavior, reproduction steps, expected behavior, environment

---

## High Priority

### 1. Fix Config Loading at Module Import
**Files:** 
- `pipeline/runner.py:20`
- `pipeline/phases.py:21`

**Issue:** `Config()` created at module import time, causing side effects before CLI runs

**Fix:** Lazy initialization - only create Config when first needed

```python
# Instead of:
_config = Config()
_log_dir = ...

# Use:
_config = None

def _get_config():
    global _config
    if _config is None:
        _config = Config()
    return _config
```

### 2. Add Config Validation
**File:** `pipeline/config.py:156-158`
**Issue:** No validation if config.json is corrupted - crashes with unhelpful traceback
**Fix:** Add try/except for JSON parsing

```python
def _load(self) -> dict:
    try:
        with open(self._path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid config.json: {e}")
```

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
- [ ] Config created lazily, not at module import
- [ ] Config loading validates JSON and gives clear errors
- [ ] No bare `except:` or `recover()` without logging
- [ ] All exceptions properly handled and propagated

---

## Notes

- Only 2 concrete tasks remain: lazy config init and config validation
- Bug vs Feature workflow is a user-requested feature (keep in tasks)
- Test after each change
