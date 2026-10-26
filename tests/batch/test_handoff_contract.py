"""Unit subject: the agreed public handoff grammar, independent test-owned packets."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from batch.helpers import batch_config
from poise.modules.foundation.errors import DomainError
from poise.modules.work.domain import BUSINESS_INCOMPLETE_STATUSES, parse_request


def fixture(name):
    directory = Path(__file__).parents[1] / "runtime_services/fixtures/stage_result_handoff"
    return json.loads((directory / name).read_text(encoding="utf-8"))


def test_handoff_preserves_the_explicit_public_packet():
    packet = fixture("handoff-packet.json")
    parsed = parse_request(packet, batch_config())
    assert parsed == packet
    assert "target_stage" not in parsed["input"]


def test_handoff_accepts_an_explicit_nonempty_progression_target():
    packet = fixture("handoff-packet.json")
    packet["input"]["target_stage"] = "review"
    assert parse_request(packet, batch_config())["input"]["target_stage"] == "review"


@pytest.mark.parametrize("field", ["request_id", "task_id", "reason", "result", "artifacts"])
def test_handoff_requires_each_explicit_input_field(field):
    packet = fixture("handoff-packet.json")
    del packet["input"][field]
    with pytest.raises(DomainError) as error:
        parse_request(packet, batch_config())
    assert field in str(error.value)


@pytest.mark.parametrize("field", ["request_id", "task_id", "reason", "target_stage"])
@pytest.mark.parametrize("value", [None, "", "   ", False, 17])
def test_handoff_rejects_null_blank_or_nonstrings(field, value):
    packet = fixture("handoff-packet.json")
    packet["input"][field] = value
    with pytest.raises(DomainError) as error:
        parse_request(packet, batch_config())
    assert field in str(error.value)


@pytest.mark.parametrize("field,value", [("result", None), ("result", []),
                                         ("artifacts", None), ("artifacts", {})])
def test_handoff_requires_result_object_and_artifact_list(field, value):
    packet = fixture("handoff-packet.json")
    packet["input"][field] = value
    with pytest.raises(DomainError) as error:
        parse_request(packet, batch_config())
    assert field in str(error.value)


@pytest.mark.parametrize("field", ["actor", "session", "role", "expected_version", "result_path"])
def test_handoff_rejects_caller_overrides_and_legacy_aliases(field):
    packet = fixture("handoff-packet.json")
    packet["input"][field] = "untrusted-input"
    with pytest.raises(DomainError) as error:
        parse_request(packet, batch_config())
    assert field in str(error.value)


def test_handoff_parser_does_not_mutate_its_input():
    packet = fixture("handoff-packet.json")
    original = deepcopy(packet)
    parsed = parse_request(packet, batch_config())
    parsed["input"]["reason"] = "Only the returned packet changed."
    assert packet == original


def test_blocked_handoff_is_classified_as_business_incomplete():
    assert "handoff_blocked" in BUSINESS_INCOMPLETE_STATUSES
