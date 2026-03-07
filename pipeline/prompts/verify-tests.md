Verify tests for the following implemented feature.

Feature: {title}

Test Spec (behaviors that must be verified):
{test_spec}

Implementation Notes (what was built):
{impl_notes}

## Your Task

1. Run the full test suite relevant to this feature
2. Analyze the test results - do tests pass or fail?
3. Verify test coverage against the test spec - are all required behaviors tested?

IMPORTANT: Do NOT attempt to fix failing tests. Your role is to:
- RUN the tests
- INTERPRET the results
- Report the verdict based on what you find

If tests fail, report which tests failed and why. Do not try to fix the test code or implementation.
If tests pass and coverage is adequate, verdict is PASS.
If tests fail, verdict is FAIL with details about what failed.

Report your findings in this JSON format:
{
  "verdict": "PASS" or "FAIL",
  "test_results": {
    "passed": <number>,
    "failed": <number>,
    "errors": <number>
  },
  "feedback": "Details about test results, failures, or coverage gaps"
}

{checkpoint_instructions}
