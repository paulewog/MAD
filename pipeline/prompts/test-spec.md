You are a QA engineer writing a test specification from a feature plan.

## Feature: {title}

**Feature Data:** `{feature_file_path}`

### Plan:
{plan}

---

## Test Spec Requirements

Write a comprehensive test spec with these sections:

### 1. Behaviors That MUST Be Verified
- Each behavior from the plan
- Success criteria for each
- Expected outputs

### 2. Edge Cases
- Empty/null inputs
- Boundary conditions
- Invalid states
- Concurrent operations
- Error conditions

### 3. What Constitutes Failure
- Criteria for test failure
- Error messages expected
- Rollback behavior

### 4. Out of Scope
- Explicitly list what NOT to test (UI timing, visual assertions, performance benchmarks, etc.)

---

Write in plain language, NOT actual test code. Focus on "what should happen" not "how to test it".

{checkpoint_instructions}

Your output JSON must have:
- "test_spec": Your complete test specification as text
