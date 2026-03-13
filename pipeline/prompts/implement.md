Implement the following feature.

Feature: {title}

**Feature Data:** `{feature_file_path}`

Plan:
{plan}

Implementation Spec (tasks to complete):
{impl_spec}

Test Spec (behaviors your implementation must satisfy):
{test_spec}

## Modifying Feature Files

IMPORTANT: Use the `pipeline edit-feature` CLI to modify feature JSON files instead of direct JSON editing:

- Set a field: `pipeline edit-feature <slug> set-field <field_name> <value>`
- Set multi-line content from stdin: `echo "content" | pipeline edit-feature <slug> set-field <field_name> --stdin`
- Set content from file: `pipeline edit-feature <slug> set-field <field_name> --file /path/to/content.md`
- Set impl_notes: `pipeline edit-feature <slug> set-field impl_notes "Implementation notes..."`
- Set test results: `pipeline edit-feature <slug> set-test-results '{"passed": 3, "failed": 0}'`
- Read a field: `pipeline edit-feature <slug> get-field <field_name>`

Available fields: title, description, plan, impl_spec, test_spec, impl_notes, type, design_ref, done_script, questions

This prevents JSON corruption and ensures proper validation.

IMPORTANT: After implementing, you MUST verify the code compiles and works:
1. Run any build commands (npm run build, cargo build, etc.)
2. Run the tests to make sure they pass (always use timeouts, e.g. `go test -timeout 30s`, `pytest --timeout=30`, `npx jest --forceExit`)
3. Fix any build errors or test failures before completing

Work through the implementation tasks systematically. After completing implementation, write a brief summary of what was done, what files were changed, and any decisions made.

{checkpoint_instructions}

Your output JSON must have:
- "summary": A brief plain-text summary of what was done and any decisions made
- "files_changed": A list of file paths that were created or modified
