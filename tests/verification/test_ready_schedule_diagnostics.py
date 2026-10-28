"""Field-specific schedule diagnostics with independent test-owned inputs."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.verification.domain import CheckRegistry
from verification.ready_schedule_oracle import assert_diagnostic_text, assert_guidance


DATA = json.loads((Path(__file__).parent / "fixtures/ready_schedule_cases.json").read_text())
STAGES = tuple(DATA["stages"])


def assert_diagnostic(error, case):
    text = str(error)
    assert isinstance(error, DomainError), "SCHEDULE_FIELD_DIAGNOSTIC_MISSING"
    assert_diagnostic_text(text, case, DATA["guidance_patterns"],
                           "SCHEDULE_FIELD_DIAGNOSTIC_MISSING", "SCHEDULE_REMEDY_DIAGNOSTIC_MISSING")


@pytest.mark.parametrize("case", DATA["diagnostics"], ids=lambda case: case["id"])
def test_diagnostics_identify_field_and_remedy(case):
    methods = deepcopy(case.get("methods", []))
    checks = deepcopy(case.get("checks", DATA["empty_schedule"]))
    before = deepcopy((methods, checks))
    try:
        CheckRegistry.from_task(methods, checks, tuple(case.get("stages", DATA["stages"])))
    except Exception as error:
        observed = error
    else:
        pytest.fail("SCHEDULE_INVALID_INPUT_ACCEPTED")
    assert (methods, checks) == before
    assert_diagnostic(observed, case)


def test_diagnostics_report_one_real_field_for_simultaneous_faults():
    messages = []
    for _ in range(2):
        with pytest.raises(DomainError) as caught:
            CheckRegistry.from_task(None, None, STAGES)
        text = str(caught.value)
        assert text.partition(":")[0].strip() in ("methods", "checks"), "SCHEDULE_FIELD_DIAGNOSTIC_MISSING"
        assert_guidance(text, DATA["guidance_patterns"], "SCHEDULE_REMEDY_DIAGNOSTIC_MISSING")
        messages.append(text)
    assert messages[0] == messages[1]


def test_diagnostics_key_order_does_not_change_mismatch():
    messages = []
    case = {"field": "checks", "tokens": ["отсутствуют: ['compile']", "лишние: ['extra']"]}
    for checks in ({"inspect": [], "extra": []}, {"extra": [], "inspect": []}):
        with pytest.raises(DomainError) as caught:
            CheckRegistry.from_task([], checks, STAGES)
        assert_diagnostic(caught.value, case)
        messages.append(str(caught.value))
    assert messages[0] == messages[1]


@pytest.mark.parametrize("reversed_keys", [False, True])
def test_preservation_empty_methods_and_each_empty_stage_are_accepted(reversed_keys):
    checks = deepcopy(DATA["empty_schedule"])
    if reversed_keys:
        checks = dict(reversed(list(checks.items())))
    before = deepcopy(checks)
    registry = CheckRegistry.from_task([], checks, STAGES)
    assert registry.entries == ()
    assert registry.stages == ("compile", "inspect")
    assert checks == before


def test_preservation_nonempty_method_keeps_exact_identity_and_schedule():
    selected = deepcopy(DATA["method"])
    checks = deepcopy(DATA["nonempty_schedule"])
    before = deepcopy((selected, checks))
    registry = CheckRegistry.from_task([selected], checks, STAGES)
    assert registry.method_ids == ("CHECK",)
    assert registry.entries[0].stages == ("inspect",)
    assert (selected, checks) == before


def test_preservation_multiple_methods_keep_empty_neighbor_stage():
    first = deepcopy(DATA["method"])
    second = deepcopy(DATA["method"])
    second.update(id="OTHER", argv=["python", "-B", "-c", "print('OTHER')"], stdout_contains=["OTHER"])
    checks = {"compile": [], "inspect": ["OTHER", "CHECK"]}
    registry = CheckRegistry.from_task([first, second], checks, STAGES)
    assert registry.method_ids == ("CHECK", "OTHER")
    assert tuple(item.stages for item in registry.entries) == (("inspect",), ("inspect",))


def test_preservation_unknown_method_stays_rejected():
    with pytest.raises(DomainError) as caught:
        CheckRegistry.from_task([], DATA["unknown_schedule"], STAGES)
    assert str(caught.value) == "Расписание ссылается на неизвестные методы: ['UNKNOWN']"


def test_preservation_duplicate_method_stays_rejected():
    with pytest.raises(DomainError) as caught:
        CheckRegistry.from_task([deepcopy(DATA["method"]), deepcopy(DATA["method"])],
                                DATA["nonempty_schedule"], STAGES)
    assert str(caught.value) == "Повтор идентификатора метода"


def test_preservation_method_plan_schedule_mismatch_stays_rejected():
    selected = deepcopy(DATA["method"])
    selected["verification_plan"]["green_stages"] = ["compile"]
    with pytest.raises(DomainError, match="verification_plan schedule"):
        CheckRegistry.from_task([selected], DATA["nonempty_schedule"], STAGES)


def test_preservation_invalid_source_contract_stays_rejected():
    selected = deepcopy(DATA["method"])
    selected["source_under_test"] = None
    with pytest.raises(DomainError, match="source_under_test"):
        CheckRegistry.from_task([selected], DATA["nonempty_schedule"], STAGES)
