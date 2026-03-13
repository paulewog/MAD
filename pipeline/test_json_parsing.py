"""Tests for robust JSON parsing functions in phases.py"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.phases import (
    _parse_json_output,
    _parse_verdict,
    _extract_json_robust,
)


class TestParseCleanJson:
    def test_parse_clean_json(self):
        """Clean JSON input parses correctly via _parse_json_output"""
        result = _parse_json_output('{"plan": "do stuff"}', 'plan')
        assert result == "do stuff"

    def test_parse_nested_clean_json(self):
        """Nested JSON parses correctly"""
        result = _parse_json_output('{"outer": {"inner": "value"}}', 'outer')
        assert result == {"inner": "value"}


class TestJson5RelaxedParsing:
    def test_parse_json_with_trailing_comma(self):
        """json5 fallback handles trailing comma"""
        result = _parse_json_output('{"plan": "my plan",}', 'plan')
        assert result == "my plan"

    def test_parse_json_with_comment(self):
        """json5 handles comments in JSON"""
        result = _parse_json_output('{/* comment */ "plan": "value"}', 'plan')
        assert result == "value"

    def test_parse_json_with_single_quotes(self):
        """json5 handles single-quoted strings"""
        result = _parse_json_output("{'plan': 'single quotes'}", 'plan')
        assert result == "single quotes"


class TestParseEmbeddedInText:
    def test_parse_json_embedded_in_text(self):
        """JSON block surrounded by markdown/prose is extracted"""
        text = 'Here is my output:\n```json\n{"plan": "the plan"}\n```\nDone!'
        result = _parse_json_output(text, 'plan')
        assert result == "the plan"

    def test_parse_json_with_prose_before(self):
        """JSON with prose before it"""
        text = 'Let me show you the plan:\n{"plan": "first plan"}'
        result = _parse_json_output(text, 'plan')
        assert result == "first plan"

    def test_parse_json_with_prose_after(self):
        """JSON with prose after it"""
        text = '{"plan": "my plan"}\nThis is some explanation.'
        result = _parse_json_output(text, 'plan')
        assert result == "my plan"


class TestParseDeeplyNestedJson:
    def test_parse_deeply_nested_json(self):
        """3+ levels of nesting parse correctly"""
        text = '{"result": {"inner": {"deep": "value"}}, "plan": "nested plan"}'
        result = _parse_json_output(text, 'plan')
        assert result == "nested plan"

    def test_parse_deeply_nested_embedded(self):
        """Deeply nested JSON embedded in text"""
        text = 'Here is the result: {"a": {"b": {"c": {"d": "deep"}}}}'
        result = _extract_json_robust(text)
        assert len(result) == 1
        parsed = __import__('json').loads(result[0])
        assert parsed["a"]["b"]["c"]["d"] == "deep"


class TestParseJsonWithEscapedBraces:
    def test_parse_json_with_escaped_braces(self):
        """Strings containing escaped braces don't break extraction"""
        result = _parse_json_output('{"plan": "use dict = {key: val}"}', 'plan')
        assert result == "use dict = {key: val}"

    def test_parse_json_with_escaped_backslash_braces(self):
        """Escaped backslash-braces are handled - JSON string escape sequences are preserved"""
        result = _parse_json_output('{"plan": "regex: /\\\\{.*\\\\}/g"}', 'plan')
        assert result == r"regex: /\{.*\}/g"


class TestParseMultipleJsonBlocks:
    def test_parse_multiple_json_blocks(self):
        """Last valid block with the target field is used"""
        text = '{"plan": "draft"} some text {"plan": "final"}'
        result = _parse_json_output(text, 'plan')
        assert result == "final"

    def test_parse_multiple_blocks_with_some_invalid(self):
        """Multiple blocks where some are invalid"""
        text = '{"invalid": } {"plan": "valid"}'
        result = _parse_json_output(text, 'plan')
        assert result == "valid"


class TestParseEmptyAndNone:
    def test_parse_empty_string(self):
        """Empty string returns empty string"""
        result = _parse_json_output("", "plan")
        assert result == ""

    def test_parse_none(self):
        """None returns empty string"""
        result = _parse_json_output(None, "plan")
        assert result == ""

    def test_parse_whitespace_only(self):
        """Whitespace only returns empty string"""
        result = _parse_json_output("   ", "plan")
        assert result == ""


class TestParseVerdictClean:
    def test_parse_verdict_clean(self):
        """_parse_verdict with clean JSON"""
        output = '{"verdict": "PASS", "summary": "All good", "feedback": ""}'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "PASS"
        assert summary == "All good"
        assert feedback == ""

    def test_parse_verdict_case_insensitive(self):
        """Verdict is case insensitive for PASS"""
        output = '{"verdict": "pass", "summary": "ok", "feedback": ""}'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "PASS"

    def test_parse_verdict_approved_keyword(self):
        """APPROVED keyword should not trigger PASS"""
        output = '{"verdict": "APPROVED", "summary": "Approved", "feedback": ""}'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "FAIL"


class TestParseVerdictEmbedded:
    def test_parse_verdict_embedded(self):
        """Verdict JSON embedded in prose"""
        output = 'After review:\n{"verdict": "FAIL", "summary": "Issues found", "feedback": "Fix X"}\nEnd.'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "FAIL"
        assert summary == "Issues found"
        assert feedback == "Fix X"

    def test_parse_verdict_with_markdown(self):
        """Verdict JSON in markdown code block"""
        output = '```json\n{"verdict": "PASS", "summary": "Good", "feedback": ""}\n```'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "PASS"
        assert summary == "Good"


class TestParseVerdictDeeplyNested:
    def test_parse_verdict_deeply_nested(self):
        """Verdict with nested test_results"""
        output = '{"tests_pass": false, "test_results": {"passed": 5, "failed": 2, "errors": 0}}'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "FAIL"
        assert "2" in summary

    def test_parse_verdict_tests_pass_true(self):
        """Verdict with tests_pass: true"""
        output = '{"tests_pass": true, "test_results": {"passed": 10, "failed": 0}}'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "PASS"


class TestParseVerdictTextFallback:
    def test_parse_verdict_text_fallback(self):
        """_parse_verdict falls back to text parsing"""
        output = '**VERDICT**: PASS\n**SUMMARY**: Looks good\n**FEEDBACK**: None'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "PASS"
        assert summary == "Looks good"
        assert feedback == "None"

    def test_parse_verdict_text_no_json(self):
        """Text fallback with no JSON at all"""
        output = 'The code looks good to me. I think it should pass.'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "FAIL"
        assert "code looks good" in summary.lower()


class TestParseVerdictPlanShapedOutput:
    def test_parse_verdict_plan_shaped_rejection(self):
        """Plan-shaped output is rejected"""
        output = '{"plan": "some plan"}'
        verdict, summary, feedback = _parse_verdict(output)
        assert verdict == "FAIL"
        assert "plan instead of a review" in feedback.lower()


class TestSchemaValidation:
    def test_schema_validation_warning(self):
        """Schema mismatch logs a warning but still returns data"""
        schema = {
            "type": "object",
            "required": ["implementation_spec"],
            "properties": {
                "implementation_spec": {"type": "string"},
            },
        }
        result = _parse_json_output('{"wrong_field": "value"}', 'wrong_field', schema)
        assert result == "value"

    def test_schema_validation_passes(self):
        """Schema validation passes for valid output"""
        schema = {
            "type": "object",
            "properties": {
                "plan": {"type": "string"},
            },
        }
        result = _parse_json_output('{"plan": "my plan"}', 'plan', schema)
        assert result == "my plan"


class TestExtractJsonRobust:
    def test_extract_json_robust_balanced_braces(self):
        """Brace-counting handles complex nesting"""
        text = '{"a": {"b": {"c": 1}}}'
        result = _extract_json_robust(text)
        assert len(result) == 1

    def test_extract_json_robust_multiple_blocks(self):
        """Multiple JSON blocks are all extracted"""
        text = '{"first": 1} some text {"second": 2}'
        result = _extract_json_robust(text)
        assert len(result) == 2

    def test_extract_json_robust_no_json(self):
        """No JSON returns empty list"""
        result = _extract_json_robust("just plain text")
        assert result == []

    def test_extract_json_robust_escaped_quotes(self):
        """Escaped quotes in strings are handled"""
        text = '{"text": "has \\"quotes\\" inside"}'
        result = _extract_json_robust(text)
        assert len(result) == 1
        parsed = __import__('json').loads(result[0])
        assert parsed["text"] == 'has "quotes" inside'


class TestFieldNotPresent:
    def test_field_not_present(self):
        """Missing field returns empty string"""
        result = _parse_json_output('{"other": "value"}', 'plan')
        assert result == ""

    def test_field_empty_string(self):
        """Empty string field returns empty string"""
        result = _parse_json_output('{"plan": ""}', 'plan')
        assert result == ""

    def test_field_null(self):
        """Null field returns empty string"""
        result = _parse_json_output('{"plan": null}', 'plan')
        assert result == ""


class TestMalformedJson:
    def test_truncated_json(self):
        """Truncated JSON doesn't crash"""
        result = _parse_json_output('{"plan": "hello', 'plan')
        assert result == ""

    def test_non_json_text(self):
        """Non-JSON text returns empty string"""
        result = _parse_json_output('This is just a paragraph of text', 'plan')
        assert result == ""

    def test_json_array(self):
        """JSON array (not object) returns empty string for field"""
        result = _parse_json_output('[1, 2, 3]', 'plan')
        assert result == ""


class TestUnicodeAndSpecialChars:
    def test_unicode_in_json(self):
        """Unicode values parse correctly"""
        result = _parse_json_output('{"plan": "Support für Ümlauts"}', 'plan')
        assert result == "Support für Ümlauts"

    def test_newlines_in_string(self):
        """Newlines in string values parse correctly"""
        result = _parse_json_output('{"plan": "line1\\nline2"}', 'plan')
        assert result == "line1\nline2"


class TestSummaryTruncation:
    def test_summary_truncation(self):
        """Summary longer than 500 chars is truncated"""
        long_summary = "x" * 600
        output = f'{{"verdict": "PASS", "summary": "{long_summary}", "feedback": ""}}'
        verdict, summary, feedback = _parse_verdict(output)
        assert len(summary) == 500
        assert summary.endswith("...")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
