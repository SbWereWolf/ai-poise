"""Verification-plan matrix contract tests for task 0053."""

from copy import deepcopy
import sys

import pytest

from conftest import verification_plan
from poise.modules.foundation.errors import DomainError
from poise.modules.verification.domain import CheckRegistry


STAGES = ("tests_written", "product_change", "docs_change", "final_check")


def method(identifier="CHECK", *, plan=...):
    value = {
        "id": identifier,
        "argv": [sys.executable, "-B", "-c", "print('OK')"],
        "cwd": ".",
        "environment": {},
        "source_under_test": {
            "kind": "repository",
            "bindings": [{"kind": "cwd", "path": "."}],
        },
        "expected_exit_code": 0,
        "stdout_contains": ["OK"],
        "stderr_contains": [],
    }
    if plan is not ...:
        value["verification_plan"] = plan
    return value


def registry(selected, scheduled=("product_change",)):
    checks = {stage: [] for stage in STAGES}
    for stage in scheduled:
        checks[stage].append(selected["id"])
    return CheckRegistry.from_task([selected], checks, STAGES)


def test_new_method_without_verification_plan_is_rejected():
    with pytest.raises(DomainError, match="verification_plan"):
        registry(method())


@pytest.mark.parametrize(
    ("mutate", "token"),
    [
        (lambda plan: plan.update(responsibility=""), "responsibility"),
        (lambda plan: plan.update(change_surface=[]), "change_surface"),
        (lambda plan: plan.update(green_stages=["unknown"]), "unknown"),
        (lambda plan: plan.update(green_stages=["product_change", "product_change"]), "green_stages"),
        (lambda plan: plan.update(red_stages=["tests_written"], red_failure=None), "red_failure"),
    ],
)
def test_verification_plan_requires_exact_actionable_fields(mutate, token):
    plan = verification_plan(
        "Verify behaviour.",
        ["src/**"],
        green_stages=["product_change"],
    )
    mutate(plan)
    with pytest.raises(DomainError, match=token):
        registry(method(plan=plan), tuple(
            stage for stage in dict.fromkeys(plan["red_stages"] + plan["green_stages"])
            if stage in STAGES
        ))


def test_schedule_must_equal_declared_red_and_green_stages():
    selected = method(plan=verification_plan(
        "Verify behaviour.",
        ["src/**"],
        green_stages=["product_change"],
    ))
    with pytest.raises(DomainError, match="CHECK|product_change|final_check|schedule|распис"):
        registry(selected, ("final_check",))


def test_verification_plan_rejects_unknown_fields():
    plan = verification_plan(
        "Verify behaviour.",
        ["src/**"],
        green_stages=["product_change"],
    )
    plan["implicit_default"] = True
    with pytest.raises(DomainError, match="точный набор полей|exact fields"):
        registry(method(plan=plan))


def test_same_command_cannot_be_duplicated_by_changing_plan_labels():
    first = method("FIRST", plan=verification_plan(
        "Verify behaviour.", ["src/**"], green_stages=["product_change"]
    ))
    second = deepcopy(first)
    second["id"] = "SECOND"
    second["verification_plan"]["responsibility"] = "Verify documentation."
    second["verification_plan"]["change_surface"] = ["docs/**"]
    with pytest.raises(DomainError, match="FIRST|SECOND|дубликат|duplicate"):
        CheckRegistry.from_task(
            [first, second],
            {
                "tests_written": [],
                "product_change": ["FIRST", "SECOND"],
                "docs_change": [],
                "final_check": [],
            },
            STAGES,
        )


def test_exact_red_failure_contract_is_distinct_from_contains_markers():
    selected = method(plan=verification_plan(
        "Prove the exact intended failure.",
        ["src/**"],
        red_stages=["tests_written"],
        red_failure={
            "exit_code": 1,
            "stdout_equals": "EXPECTED_FAILURE\n",
            "stderr_equals": "",
        },
    ))
    selected.update(expected_exit_code=1, stdout_contains=["EXPECTED_FAILURE"])
    assert registry(selected, ("tests_written",)).method_ids == ("CHECK",)
