# Ideation: {title}

**Description:** {description}

**Feature Data:** `{feature_file_path}`

{previous_ideation}

{ideation_prompt_section}

---

## Task

You are participating in an ideation session to refine and critique this idea.

**Important:** The goal is a good, practical approach — not a perfect one. Favor simplicity and pragmatism over theoretical completeness. Do not expand scope beyond what was asked for. Do not add features, edge cases, or abstractions that aren't clearly needed. A focused solution that works is better than an exhaustive one that never ships.

## Your Role

{role_instruction}

## Previous Discussion

{discussion_summary}

## Full Transcripts

{full_transcripts}

## Output

Write a JSON object with a "summary" field containing a brief summary (2-3 sentences) of your contribution to the discussion.

```json
{
  "summary": "Your brief summary here..."
}
```
