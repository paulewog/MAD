# Ideation Synthesis

**Feature Data:** `{feature_file_path}`

## Summaries from Discussion

{summaries}

---

## Cycle Info

This is cycle {cycle_number}. Total rounds completed so far: {total_rounds_completed}.
{previous_verdicts_section}

---

## Task

Synthesize the above discussion into a coherent Ideation that captures:

- The core idea/problem being solved
- Key considerations and tradeoffs
- Alternative approaches considered
- Risks and uncertainties

## Convergence Assessment

After writing the synthesis, assess whether the discussion should continue:

1. List any points from the latest cycle that were NOT present in previous cycles.
2. If fewer than ~20% of points are genuinely new (not rewordings or sub-splits of existing points), this is likely a **stall**.
3. If agents are largely agreeing on approach with only minor refinements, this is likely **consensus**.
4. Only report **progress** if there are substantive new arguments, alternative approaches, or meaningful critiques that previous cycles did not raise.
5. If the discussion is expanding scope beyond the original request (adding features, edge cases, or abstractions not asked for), that is a **stall** — scope creep is not progress.

Be conservative: when in doubt between progress and stall, choose stall. The goal is a good, practical solution — not a perfect one. Avoid wasting rounds on circular discussion or diminishing-returns refinement.

## Output

Write a JSON object with these fields:

```json
{
  "Ideation": "Your synthesized ideation here (2-4 paragraphs)...",
  "verdict": "consensus | stall | progress",
  "verdict_reason": "Brief explanation of why you chose this verdict (1-2 sentences)"
}
```
