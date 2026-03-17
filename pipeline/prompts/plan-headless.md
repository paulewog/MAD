# Design Planning: {title}

**Description:** {description}

{previous_plan}

---

## Source Priority

**IMPORTANT:** The feature **description** above is the authoritative source of truth. Ideation summaries below provide useful background context and exploration, but if there is any conflict or discrepancy between the description and the ideation content, **always defer to the description**. The user may have refined their requirements after ideation completed.

You are an AI helping plan this feature. Generate a plan based on the description.

## Feature Context

**Ideation (synthesized):**
{ideation_synthesis}

**Full Feature Data:** The complete feature file is at:
`{feature_file_path}`

Use the Read tool to examine this file directly to access:
- `ideation_summaries` - list of synthesized ideation rounds from debate
- `plan` - existing plan (if re-planning)
- `description` - the feature description
- Any other fields as needed

## Modifying Feature Files

IMPORTANT: Use the `pipeline edit-feature` CLI to modify feature JSON files instead of direct JSON editing:

- Set a field: `pipeline edit-feature <slug> set-field <field_name> <value>`
- Set multi-line content from stdin: `echo "content" | pipeline edit-feature <slug> set-field <field_name> --stdin`
- Set content from file: `pipeline edit-feature <slug> set-field <field_name> --file /path/to/content.md`
- Set test results: `pipeline edit-feature <slug> set-test-results '{"passed": 3, "failed": 0}'`
- Read a field: `pipeline edit-feature <slug> get-field <field_name>`

Available fields: title, description, plan, impl_spec, test_spec, impl_notes, type, design_ref, done_script, questions

This prevents JSON corruption and ensures proper validation.

**Your task:**
1. Analyze the feature description
2. **Explore the codebase** to understand the existing architecture and patterns
3. Identify any unclear points or areas needing clarification that cannot be determined from exploration
4. If you have questions for the human, list them in a JSON array under "questions"
5. If you have enough info, write a complete **design** plan under "plan"

## Plan Content Guidelines

Your plan should focus on **high-level design thinking**, not implementation details. Structure your plan around:

- **What**: Clear description of the feature/change in plain language
- **Why**: User value and business justification
- **Impact**: How this affects other system components, what might break
- **Risks**: Technical uncertainties, potential problems
- **Scope**: What's included and explicitly what's NOT included

## Explicitly Exclude from Plans

Do NOT include in your plan:
- Specific file names or paths
- Method/function names
- Line numbers or code snippets
- API endpoint details
- Database schema changes
- Step-by-step implementation approaches

The implementation details belong in the impl_spec phase, not here.

**JSON structure:**

```json
{
  "exploration_summary": "Brief summary of codebase exploration: files examined, patterns found, key insights...",
  "questions": [
    {"question": "What color should the UI element be?", "answer": ""},
    {"question": "Should this work offline?", "answer": ""}
  ],
  "plan": "Your detailed plan here..."
}
```

**MANDATORY EXPLORATION REQUIREMENT:**
You MUST explore the codebase *before* asking any questions. Your response must include an "exploration_summary" field in the JSON output that documents:
- Files you examined
- Code patterns you found
- Key architecture insights
- Any assumptions made

This exploration_summary is required for validation.

**EXPLORATION CHECKLIST — Complete ALL before asking questions:**
- Search for similar existing features or patterns in the codebase
- Read key architecture files to understand current structure
- Identify integration points and existing APIs
- Understand current conventions and patterns used throughout the project

Your response must reference specific findings from each checklist item.

**PHASE 1: MANDATORY EXPLORATION (Complete first)**
Use Read/Grep/Glob tools to investigate. Document the specific files and code patterns you found. Identify what can be determined from code vs. what requires user input.

**PHASE 2: TARGETED QUESTIONS (Only if needed)**
Only ask questions that cannot be answered by code exploration:
- Business requirements and user preferences not evident in code
- Scope boundaries — what is in scope vs. out of scope
- Specific user-facing behavior expectations beyond existing patterns
- Priority or ordering preferences if multiple approaches are possible

Each question must explain why it cannot be answered through code exploration.

{checkpoint_instructions}
