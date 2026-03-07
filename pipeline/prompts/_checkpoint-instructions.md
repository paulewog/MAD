## Checkpoint Instructions
After completing each meaningful unit of work, write a checkpoint file to:
  .mad/checkpoints/{feature_slug}.checkpoint.json

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

Overwrite the file each time (not append). Create .mad/checkpoints/ if needed.
If writing the checkpoint fails, just continue working.
