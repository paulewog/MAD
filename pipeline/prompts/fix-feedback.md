You are an AI coding agent tasked with fixing specific issues found in a code review.

## Feature: {title}

### Original Plan:
{plan}

### Implementation Spec:
{impl_spec}

### Test Spec:
{test_spec}

### Current Implementation:
{impl_notes}

---

## Review Feedback (Issues to Fix):

{feedback}

---

## Your Task

Fix ONLY the issues listed in the review feedback above. Do NOT make other changes or add new features - focus entirely on addressing each point in the feedback.

For each issue:
1. Understand what the reviewer identified as wrong
2. Fix the implementation OR fix the tests as appropriate
3. Make sure your fix actually addresses the root cause

If the feedback mentions test gaps, add or fix tests.
If the feedback mentions implementation bugs, fix the implementation code.

{checkpoint_instructions}

Your output JSON must have:
- "summary": A brief plain-text summary of what was fixed and how
- "files_changed": A list of file paths that were created or modified
