"""A recorded schedule is historical context, never current stage permission."""
from copy import deepcopy

import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.verification.domain import CheckRegistry
from verification.test_current_registry_mutation import registration


def recorded_state():
    return {
        "revision": 1,
        "history": [{"revision": 0, "entries": [registration("OLD", "historical", stage="retired")]}],
        "requests": [{"request_id": "original-request", "digest": "original-digest", "revision": 1}],
        "executable_obligations": ["requirements[0]", "definition_of_done[0]"],
    }


def empty_current():
    return CheckRegistry.from_items([], ("current", "inspection")).with_executable_obligations(
        ("requirements[0]", "definition_of_done[0]"), ("inspection",),
        ("requirements[0]", "definition_of_done[0]"),
    )


def test_retired_snapshot_roundtrip_preserves_every_recorded_field():
    raw = recorded_state()
    before = deepcopy(raw)
    restored = empty_current().restore_state(raw)
    assert raw == before
    assert restored.to_state() == before
    assert restored.stages == ("current", "inspection")
    assert restored.entries == ()
    with pytest.raises(DomainError, match="GREEN executable-test"):
        restored.validate_inspection_exit()
    with pytest.raises(DomainError, match="неизвестные этапы"):
        CheckRegistry.from_items(before["history"][0]["entries"], restored.stages)


@pytest.mark.parametrize("damage, message", [
    ("duplicate-method", "Повтор идентификатора метода"),
    ("schedule-mismatch", "не совпадает с расписанием"),
    ("malformed-stage", "Метод имеет неизвестные/повторные этапы"),
    ("extra-method-field", "undocumented"),
])
def test_retired_snapshot_corruption_is_rejected_without_input_mutation(damage, message):
    raw = recorded_state()
    entries = raw["history"][0]["entries"]
    if damage == "duplicate-method":
        entries.append(deepcopy(entries[0]))
    elif damage == "schedule-mismatch":
        entries[0]["stages"] = ["another-recorded-stage"]
    elif damage == "malformed-stage":
        entries[0]["stages"] = [True]
    else:
        entries[0]["method"]["undocumented"] = "forbidden"
    before = deepcopy(raw)
    # Recognize both schedules so each assertion reaches its own corruption guard.
    current = CheckRegistry.from_items([], ("retired", "another-recorded-stage"))
    with pytest.raises(DomainError, match=message):
        current.restore_state(raw)
    assert raw == before


def legacy_entry(stages):
    return {
        "method": {
            "id": "LEGACY",
            "argv": ["python", "-B", "-c", "print('historical')"],
            "cwd": ".",
            "environment": {},
            "expected_exit_code": 0,
            "stdout_contains": ["historical"],
            "stderr_contains": [],
        },
        "stages": stages,
    }


@pytest.mark.parametrize("stages", [["retired"], []])
def test_legacy_snapshot_preserves_absent_optional_fields_and_empty_schedule(stages):
    raw = recorded_state()
    raw["history"][0]["entries"] = [legacy_entry(stages)]
    before = deepcopy(raw)
    restored = empty_current().restore_state(raw)
    assert raw == before
    assert restored.to_state() == before
    assert restored.entries == ()
    assert restored.stages == ("current", "inspection")


@pytest.mark.parametrize("stages", [[""], [True], "retired", None])
def test_legacy_historical_schedule_cannot_authorize_invalid_stage_names(stages):
    raw = recorded_state()
    raw["history"][0]["entries"] = [legacy_entry(stages)]
    before = deepcopy(raw)
    with pytest.raises(DomainError, match="этап|stages"):
        empty_current().restore_state(raw)
    assert raw == before
