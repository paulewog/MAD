# Design Planning: {title}

**Description:** {description}

---

You are an AI helping plan this feature. Generate a plan based on the description.

**Your task:**
1. Analyze the feature description
2. Identify any unclear points or areas needing clarification
3. If you have questions for the human, list them in a JSON array under "questions"
4. If you have enough info, write a complete plan under "plan"

**Output format (JSON):**

```json
{
  "questions": [
    {"question": "What color should the UI element be?", "answer": ""},
    {"question": "Should this work offline?", "answer": ""}
  ],
  "plan": "Your detailed plan here..."
}
```

**Instructions:**
- Only include "questions" if you genuinely need human input to proceed
- If "questions" is empty or missing, the plan will be considered complete
- Keep questions specific and actionable
- Write a complete, detailed plan in the "plan" field

Start now and output only valid JSON.

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

When finished, write "PLAN_COMPLETE" on its own line and then exit. Do not wait for more input.
