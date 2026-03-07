You are an implementer breaking down a feature plan into concrete coding tasks.

Feature: {title}

Plan:
{plan}

Write a detailed implementation spec — a list of specific coding tasks.
For each task: what to create or modify, the approach, and dependencies on other tasks.
Be concrete. No preamble.

{checkpoint_instructions}

## Output Format

Write your response as JSON with exactly these fields:
- "implementation_spec": The detailed implementation spec as text
- "test_spec": The test specification as text

Example:
```json
{
  "implementation_spec": "1. Add function to audio.ts\n2. Update imports in farmDog.ts...",
  "test_spec": "Verify the dog moves toward the hen during search..."
}
```

Write ONLY valid JSON to the output file. Nothing else.

When finished, write DONE on its own line and then exit.
