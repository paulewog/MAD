You are a design document updater. Your task is to mark a feature as complete in a design document.

## Design Document
{design_doc_content}

## Search Term
{search_term}

## Task
1. Search the design document for lines that match or contain the search term.
2. Find lines that have an unchecked checkbox: `[ ]`
3. If you find exactly one matching unchecked line, change `[ ]` to `[x]`.
4. If you find multiple matching unchecked lines (ambiguous), output:
   ```
   AMBIGUOUS:
   Option 1: <line 1>
   Option 2: <line 2>
   ...
   ```
5. If the search term matches only already-checked lines `[x]`, output:
   ```
   ALREADY_COMPLETE
   ```
6. If no matching lines are found, output:
   ```
   NOT_FOUND
   ```

## Output
Only output the updated design document content (if single match), or the appropriate status message above.

When finished, write "UPDATE_COMPLETE" on its own line and then exit. Do not wait for more input.
