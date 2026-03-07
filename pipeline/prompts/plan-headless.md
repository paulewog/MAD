# Design Planning: {title}

**Description:** {description}

---

You are an AI helping plan this feature. Generate a plan based on the description.

**Your task:**
1. Analyze the feature description
2. Identify any unclear points or areas needing clarification
3. If you have questions for the human, list them in a JSON array under "questions"
4. If you have enough info, write a complete plan under "plan"

**JSON structure:**

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
- The plan should be detailed and actionable

{checkpoint_instructions}
