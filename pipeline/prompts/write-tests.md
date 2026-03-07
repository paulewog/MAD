Write tests for the following implemented feature.

Feature: {title}

Test Spec (behaviors to verify):
{test_spec}

Implementation Notes (what was built):
{impl_notes}

IMPORTANT: After writing tests, you MUST verify:
1. The tests compile without errors
2. All tests pass
3. Fix any test failures before completing

Write actual test code following the project's existing testing patterns. Cover all behaviors in the test spec. Include edge cases. No preamble — just write the tests.

## Checkpoint Instructions
After completing each meaningful unit of work, write a checkpoint file to:
  .mad/checkpoints/<feature-slug>.checkpoint.json

The feature slug for this task is: {feature_slug}

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

Overwrite the file each time (not append). Create the .mad/checkpoints/ directory if it doesn't exist.
If writing the checkpoint fails for any reason (permissions, disk space), just continue working — checkpoints are best-effort and should never block your actual work.

When finished, write "TESTS_COMPLETE" on its own line and then exit. Do not wait for more input.
