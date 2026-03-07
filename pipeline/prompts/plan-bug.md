# Bug Report Planning: {title}

**Description:** {description}

---

You are an AI helping plan a bug fix. This is a **bug report**, not a feature request.

**Your task:**
1. Analyze the bug description
2. Ask the human for any missing information you need to reproduce and fix the bug
3. If you have enough info, write a plan to fix the bug

**Information you need from the human (ask as questions if not provided):**
- What is the observed (broken) behavior?
- What is the expected (correct) behavior?
- Steps to reproduce the bug
- Environment details (OS, browser, versions, etc.)
- Any error messages or logs
- When did this start happening? (after a specific change?)

**JSON structure:**

```json
{
  "questions": [
    {"question": "What error message do you see?", "answer": ""},
    {"question": "Can you describe the steps to reproduce?", "answer": ""}
  ],
  "plan": "Your bug fix plan here..."
}
```

**Instructions:**
- For bug reports, you should almost always have questions — bugs need specific reproduction details
- Only write a plan if you have enough information to identify the root cause
- The plan should include: root cause analysis, fix approach, and what to verify
- Keep questions specific — ask about observed vs expected behavior, reproduction steps, and environment

{checkpoint_instructions}
