# MAD Pipeline Phase Refactor Plan

## Overview

The pipeline is a multi-agent system with these phases:

```
ideas -> plan-inbox -> [planning] -> reviewing-plan -> [plan-review] -> approved
  -> spec-writing -> implementing -> testing -> review -> final-human-approval -> done
                          ^                                |
                          +---- (on FAIL) ----------------+
```

Each phase calls `runner.headless()` which launches an AI agent subprocess. The current system has several inconsistencies in output format, completion signaling, and phase responsibilities.

## New Consistent Output Model

Every phase must:
1. Write JSON output to: `.tmp/{slug}.output.json`
2. Write a marker file: `.tmp/{slug}.complete` with contents `PHASE_COMPLETE: optional message`

The runner watches for the marker file instead of parsing stdout for "DONE".

---

## Step 1: Define Output Schemas in PHASE_CONFIG

**File: `pipeline/phases.py`**

Add `output_schema` to each entry in `PHASE_CONFIG`. Remove `done_marker` from all entries.

```python
PHASE_CONFIG = {
    "planning": {
        "runner_phase": "planning",
        "history_tag": "PLANNING",
        "status_label": "planning",
        "template": "plan-headless.md",
        "output_schema": {
            "questions": "list[dict] | null",
            "plan": "string | null",
        },
    },
    "reviewing_plan": {
        "runner_phase": "reviewing_plan",
        "history_tag": "PLAN_REVIEW",
        "status_label": "reviewing-plan",
        "template": "review-plan.md",
        "output_schema": {
            "verdict": "string",       # PASS or FAIL
            "feedback": "string | null",
        },
    },
    "spec_impl": {
        "runner_phase": "spec_writing",
        "history_tag": "SPEC_WRITING",
        "status_label": "spec: implementation",
        "template": "impl-spec.md",
        "output_schema": {
            "implementation_spec": "string",
        },
    },
    "spec_test": {
        "runner_phase": "spec_writing",
        "history_tag": "SPEC_WRITING",
        "status_label": "spec: tests",
        "template": "test-spec.md",
        "output_schema": {
            "test_spec": "string",
        },
    },
    "implementing": {
        "runner_phase": "implementing",
        "history_tag": "IMPLEMENTING",
        "status_label": "implementing",
        "template": "implement.md",
        "output_schema": {
            "summary": "string",
            "files_changed": "list[string]",
        },
    },
    "fix_feedback": {
        "runner_phase": "fix_feedback",
        "history_tag": "FIX_FEEDBACK",
        "status_label": "fixing feedback",
        "template": "fix-feedback.md",
        "output_schema": {
            "summary": "string",
            "files_changed": "list[string]",
        },
    },
    "testing": {
        "runner_phase": "testing",
        "history_tag": "TESTING",
        "status_label": "verifying tests",
        "template": "verify-tests.md",    # renamed from write-tests.md
        "output_schema": {
            "verdict": "string",           # PASS or FAIL
            "test_results": "dict",
            "feedback": "string | null",
        },
    },
    "review_impl": {
        "runner_phase": "review",
        "history_tag": "REVIEW",
        "status_label": "review",
        "template": "review-impl.md",
        "output_schema": {
            "verdict": "string",
            "feedback": "string | null",
        },
    },
}
```

---

## Step 2: Refactor the Runner for Marker Files

**File: `pipeline/runner.py`**

### 2a. Add `_build_completion_block()` method

```python
def _build_completion_block(self, output_path: Path, marker_path: Path, schema: dict) -> str:
    schema_desc = "\n".join(f'  - "{k}": {v}' for k, v in schema.items())
    return f"""

## Output and Completion

When you have finished all work:

1. Write your output as a single valid JSON object to:
   {output_path}

   Required fields:
{schema_desc}

2. Create this marker file:
   {marker_path}

   Write exactly: PHASE_COMPLETE

3. Exit immediately after creating the marker file. Do not wait for input.
"""
```

### 2b. Add `phase_key` parameter to `headless()`

When `phase_key` is provided:
- Compute `output_path = tmp_dir / f"{item_name}.output.json"`
- Compute `marker_path = tmp_dir / f"{item_name}.complete"`
- Delete any existing marker file before starting
- Append `_build_completion_block()` to prompt
- After process ends, check `marker_path.exists()` instead of scanning stdout for DONE

### 2c. Remove inline output instructions

Remove the block currently appended at lines 232-236:
```python
# REMOVE THIS:
full_prompt += f"\n\nIMPORTANT: Write your complete output as JSON to this file: {output_path}\n"
full_prompt += "Write ONLY valid JSON to this file. Nothing else.\n"
full_prompt += "After writing the file, write DONE on its own line to signal completion.\n"
```

### 2d. Replace DONE stdout detection

Replace `got_completion_marker = any("DONE" in line.upper() for line in output_lines)` with marker file check:
```python
got_completion_marker = marker_path.exists() and "PHASE_COMPLETE" in marker_path.read_text()
```

### 2e. Fix print() on line 453

Change `print(f"[runner] Log file closed", file=__import__('sys').stderr)` to `logger.info("[runner] Log file closed")`.

---

## Step 3: Strip Output Instructions from All Prompts

Remove all "Write ONLY valid JSON", "write DONE on its own line", "TESTDONE", and "Output Format" sections from individual prompt templates. The runner now handles this uniformly.

### plan-headless.md
- Remove "IMPORTANT - Output Rules" section (lines 27-31)
- Remove "Start now and output only valid JSON with your plan." (line 39)
- Keep the JSON format example (questions/plan structure) since it is phase-specific
- Remove `{checkpoint_instructions}` placeholder (runner handles completion)

### review-plan.md
- Remove "Output Format" section (lines 29-42)
- Remove "Write ONLY valid JSON to the output file."
- Remove "When finished, write DONE on its own line and then exit."

### impl-spec.md
- Remove "Output Format" section (lines 16-28)
- Remove `test_spec` from schema (only produce `implementation_spec`)
- Remove "Write ONLY valid JSON" and "When finished, write DONE"

### test-spec.md
- Remove "Output Format" section (lines 40-54)
- Remove "Write ONLY valid JSON"
- Remove "When finished, write TESTDONE on its own line and then exit."

### implement.md
- Remove `{checkpoint_instructions}` placeholder
- Keep build/test verification instructions (those are actual task instructions)

### write-tests.md -> verify-tests.md
- Complete rewrite (see Step 4)

### fix-feedback.md
- Remove `{checkpoint_instructions}` placeholder
- Remove "Output a summary of what you fixed."

### review-impl.md
- Remove "Output Format" section (lines 44-61)
- Remove "Write ONLY valid JSON"
- Remove "When finished, write DONE on its own line and then exit."
- Add instruction: "You MUST read the actual source files mentioned. Do not trust the implementation summary alone."

---

## Step 4: Fix Test Duplication

Current flow: `implementing` (writes code AND tests) -> `testing` (writes tests AGAIN) -> `review`

New flow: `implementing` (writes code AND tests) -> `testing` (VERIFIES tests pass) -> `review`

### 4a. implement.md stays as-is
It already instructs the agent to implement AND write tests AND verify they pass.

### 4b. Rename write-tests.md to verify-tests.md

New content for `verify-tests.md`:
```markdown
Verify tests for the following implemented feature.

Feature: {title}

Test Spec (behaviors that must be verified):
{test_spec}

Implementation Notes (what was built):
{impl_notes}

## Your Task

1. Run the full test suite relevant to this feature
2. Check that all tests pass
3. Verify test coverage against the test spec above - are all required behaviors tested?
4. If any tests fail, fix them
5. If the test spec lists behaviors that have no tests, write tests for them

Report your findings. If all tests pass and coverage is adequate, verdict is PASS.
If there are failures you could not fix, verdict is FAIL with details.
```

### 4c. Rename run_writing_tests() to run_verify_tests()

Return `(verdict, feedback)` tuple instead of void. Parse the JSON output for verdict/feedback.

### 4d. Update pipeline orchestration

```python
for attempt in range(1, max_review_attempts + 1):
    if attempt == 1:
        run_implementing(feature, runner, status=status)
    else:
        feedback = _get_latest_feedback(feature)
        run_fix_feedback(feature, runner, feedback, status=status)

    # Verify tests pass
    test_verdict, test_feedback = run_verify_tests(feature, runner, status=status)
    if test_verdict != "PASS":
        # Tests failed, loop back to fix
        continue

    # Code review
    verdict, feedback = run_review_impl(feature, runner, status=status)
    if verdict == "PASS":
        break
```

---

## Step 5: Fix All Identified Bugs

### 5a. Plan review retry + questions bug

**File: `pipeline/phases.py`**, `_run_pipeline_impl()`, ~line 830

Current code unconditionally moves to `reviewing-plan` after calling `run_planning()` during a retry, even if planning produced questions.

Fix:
```python
plan_ok = run_planning(feature, runner, status=status)
if not plan_ok:
    # Questions raised during re-planning, cannot continue review loop
    return
# Plan produced - run_planning already moved to reviewing-plan, no need to move again
```

### 5b. impl-spec.md asks for both impl_spec AND test_spec

Remove `test_spec` field from `impl-spec.md` output schema. The impl-spec prompt should only produce `implementation_spec`. The test spec is generated separately by `test-spec.md`.

### 5c. impl_notes overwritten by run_fix_feedback

**File: `pipeline/phases.py`**, `run_fix_feedback()`, ~line 671

Current: `feature.set_impl_notes(output.strip())` replaces everything.

Fix: Append instead of overwrite:
```python
existing_notes = feature.impl_notes or ""
fix_summary = output.strip()
updated = f"{existing_notes}\n\n### Fix Feedback\n\n{fix_summary}"
feature.set_impl_notes(updated)
```

### 5d. Debug print() statements in run_planning

Remove all `print(..., file=sys.stderr)` and `sys.stderr.flush()` calls from `run_planning()` (lines 402, 403, 438, 439, 441-443).

### 5e. Inconsistent _git_commit and _delete_checkpoint calls

Every phase that completes successfully should call both. Currently missing from:
- `run_planning()` JSON success path (has neither)
- `run_plan_review()` (has neither)
- `run_spec_writing()` (missing `_git_commit`)
- `run_fix_feedback()` (has neither)
- `run_writing_tests()` / `run_verify_tests()` (missing `_git_commit`)
- `run_review_impl()` (missing `_git_commit`)

### 5f. Refactor all phases to use _run_phase

Currently `run_planning()`, `run_plan_review()`, and `run_review_impl()` call `runner.headless()` directly with their own ad-hoc error handling. Refactor to use `_run_phase()` for consistency.

### 5g. _parse_json_output uses greedy regex

Current: `re.search(r'\{[\s\S]*\}', output)` matches first `{` to last `}`.

Since output now comes from `.output.json` (pure JSON), try `json.loads()` first:
```python
def _parse_json_output(output: str, field: str):
    try:
        result = json.loads(output.strip())
        if field in result and result[field]:
            return result[field]
    except json.JSONDecodeError:
        pass
    # Fallback: regex as last resort
    ...
```

Same fix for `_parse_verdict()`.

### 5h. Review agent reviews impl_notes not actual code

Add to `review-impl.md`:
```
**IMPORTANT**: The "Implementation" section above is the implementer's self-reported summary.
You MUST read the actual source files mentioned to verify the implementation.
Do not trust the summary alone.
```

---

## Step 6: Refactor _checkpoint-instructions.md

**File: `pipeline/prompts/_checkpoint-instructions.md`**

Remove `{done_marker}` line. Completion signaling is now handled by the runner's `_build_completion_block()`.

New content:
```markdown
## Checkpoint Instructions
After completing each meaningful unit of work, write a checkpoint file to:
  .mad/checkpoints/{feature_slug}.checkpoint.json

Use this exact JSON format:
{
  "feature_slug": "{feature_slug}",
  "feature_id": "{feature_id}",
  "phase": "{phase}",
  "last_checkpoint": "<current ISO timestamp>",
  "completed_steps": ["list of what you've done so far"],
  "next_step": "what you plan to do next",
  "notes": "any context needed to resume",
  "files_modified": ["list of files you've changed"]
}

Overwrite the file each time (not append). Create .mad/checkpoints/ if needed.
If writing the checkpoint fails, just continue working.
```

Update `_build_prompt()` in `phases.py` to remove the `done_marker` substitution logic.

---

## Implementation Sequence

Changes must be made in this order to avoid breaking the system mid-refactor:

### Phase A: Non-breaking foundation
1. Add `output_schema` to `PHASE_CONFIG` (additive)
2. Add `_build_completion_block()` to runner
3. Fix debug print statements in `run_planning()`
4. Fix `_parse_json_output()` greedy regex

### Phase B: Runner refactor
1. Add `phase_key` parameter to `runner.headless()` (optional, backward compat)
2. Implement marker file detection alongside existing DONE detection
3. Update `_run_phase()` to pass phase_key
4. Update `_build_prompt()` to stop substituting `{done_marker}`
5. Update `_checkpoint-instructions.md`

### Phase C: Prompt cleanup
1. Strip output format sections from all prompt templates
2. Verify each phase still produces valid output

### Phase D: Bug fixes
1. Fix plan review retry + questions bug
2. Fix impl-spec.md dual output
3. Fix impl_notes overwrite in run_fix_feedback
4. Fix inconsistent _git_commit/_delete_checkpoint
5. Refactor remaining phases to use _run_phase
6. Add code-reading instruction to review-impl.md

### Phase E: Test duplication fix
1. Rename `write-tests.md` to `verify-tests.md` and rewrite
2. Rename `run_writing_tests()` to `run_verify_tests()` and change return type
3. Update pipeline orchestration for test verification as verdict
4. Update `pipeline.py` imports
