"""Test-owned assertions for explicit field identity and positive guidance."""
import re


def assert_guidance(text, patterns, marker):
    for pattern in patterns:
        assert re.search(pattern, text, re.IGNORECASE), marker


def assert_diagnostic_text(text, case, patterns, field_marker, remedy_marker):
    actual_field = text.partition(":")[0].strip()
    expected = re.escape(case["field"])
    if case.get("allow_index", False):
        expected += r"(?:\[\d+\])?"
    assert re.fullmatch(expected, actual_field), field_marker
    for token in case["tokens"]:
        assert token in text, remedy_marker
    assert_guidance(text, patterns, remedy_marker)
