Implement the following feature.

Feature: {title}

Plan:
{plan}

Implementation Spec (tasks to complete):
{impl_spec}

Test Spec (behaviors your implementation must satisfy):
{test_spec}

IMPORTANT: After implementing, you MUST verify the code compiles and works:
1. Run any build commands (npm run build, cargo build, etc.)
2. Run the tests to make sure they pass
3. Fix any build errors or test failures before completing

Work through the implementation tasks systematically. After completing implementation, write a brief summary of what was done, what files were changed, and any decisions made.

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

When finished, write "IMPLEMENTATION_COMPLETE" on its own line and then exit. Do not wait for more input.
