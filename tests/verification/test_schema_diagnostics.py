"""Actionable schema diagnostics, with a deterministic exact-RED entry point."""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys

from poise.application.tasks import validate_creation_intent
from poise.common import exact_keys as common_keys
from poise.modules.foundation.errors import DomainError, PoiseError
from poise.modules.tasks.allocation import materialize_contract
from poise.modules.verification.domain import exact_keys as domain_keys


ROOT = Path(__file__).resolve().parents[2]


def rejected(call, error_type):
    try:
        call()
    except error_type as error:
        assert type(error) is error_type, type(error).__name__
        return str(error)
    raise AssertionError("Invalid input was accepted")


def check_delta(message, missing, extra, location="request.task"):
    assert message.startswith(location + ":"), message
    assert f"отсутствуют {missing!r}" in message, message
    assert f"неизвестные поля {extra!r}" in message, message
    assert "PRIVATE_VALUE" not in message, message


def test_domain_missing():
    message = rejected(lambda: domain_keys({}, {"zeta", "alpha"}, "request.task"), DomainError)
    check_delta(message, ["alpha", "zeta"], [])


def test_domain_extra():
    value = {"kept": None, "zeta": "PRIVATE_VALUE", "alpha": "PRIVATE_VALUE"}
    before = deepcopy(value)
    message = rejected(lambda: domain_keys(value, {"kept"}, "request.task"), DomainError)
    check_delta(message, [], ["alpha", "zeta"])
    assert value == before


def test_domain_combined():
    value = {"kept": None, "omega": "PRIVATE_VALUE", "beta": "PRIVATE_VALUE"}
    message = rejected(
        lambda: domain_keys(value, {"zeta", "kept", "alpha"}, "request.task"), DomainError
    )
    check_delta(message, ["alpha", "zeta"], ["beta", "omega"])


def test_domain_wrong_type():
    for value in (None, [], "PRIVATE_VALUE", 1, False):
        message = rejected(lambda: domain_keys(value, {"kept"}, "request.task"), DomainError)
        assert message == "request.task: ожидается объект", message


def creation_fixture():
    process = json.loads((ROOT / "config/catalogue/process-templates/development.json").read_text())
    stages = [stage["id"] for stage in process["stages"]]
    task = {
        "sprint_id": None,
        "goal_type": "development",
        "goal": "Exercise automatic creation diagnostics.",
        "requirements": ["Keep automatic identity allocation."],
        "definition_of_done": ["The creation contract validates."],
        "methods": [],
        "method_inputs": [],
        "checks": {stage: [] for stage in stages},
        "artifact_requirements": [],
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "evidence_plan": {
            stage: {"subject_methods": {}, "arguments": [], "review_arguments": []}
            for stage in stages
        },
        "executable_obligations": ["requirements[0]"],
        "stage_contracts": [{
            "stage_id": stage["id"],
            "allowed_paths": list(stage["allowed_paths"]),
            "entry_requirements": [],
            "exit_requirements": [],
        } for stage in process["stages"]],
    }
    return {"request_id": "schema-diagnostic-creation", "task": task}, process


def test_automatic_missing_obligations():
    intent, process = creation_fixture()
    del intent["task"]["executable_obligations"]
    before = deepcopy(intent)
    message = rejected(lambda: validate_creation_intent(intent, process, []), DomainError)
    check_delta(message, ["executable_obligations"], [], "task")
    assert "'id'" not in message, message
    assert intent == before


def test_common_existing_diagnostics():
    message = rejected(
        lambda: common_keys({"extra": "PRIVATE_VALUE"}, {"zeta", "alpha"}, "request.task"),
        PoiseError,
    )
    check_delta(message, ["alpha", "zeta"], ["extra"])
    for value in (None, [], "PRIVATE_VALUE", 1, False):
        message = rejected(lambda: common_keys(value, {"kept"}, "request.task"), PoiseError)
        assert message == "request.task: ожидается объект", message


def test_valid_shapes_preserve_input():
    for validator in (common_keys, domain_keys):
        for value in ({}, {"kept": {"nested": [1, 2]}}):
            before = deepcopy(value)
            assert validator(value, set(value), "request.task") is None
            assert value == before


def test_valid_automatic_and_explicit_creation():
    intent, process = creation_fixture()
    before = deepcopy(intent)
    metadata = validate_creation_intent(intent, process, [])
    assert metadata["contract"]["id"] == intent["request_id"]
    contract, receipt = materialize_contract(intent, "0099")
    assert contract["id"] == "0099"
    assert receipt["request_id"] == intent["request_id"]
    assert validate_creation_intent(contract, process, [])["contract"] == contract
    assert intent == before
    assert "id" not in intent["task"]


def test_automatic_caller_id_still_forbidden():
    for identity in (None, "0099"):
        intent, process = creation_fixture()
        intent["task"]["id"] = identity
        before = deepcopy(intent)
        message = rejected(lambda: validate_creation_intent(intent, process, []), DomainError)
        assert "without id" in message, message
        assert intent == before


def test_documented_diagnostics_and_creation_fields():
    document = (ROOT / "docs/workflows/batch-work.md").read_text()
    section = document.split("## Автоматическое создание Task", 1)[1].split("## Verify", 1)[0]
    for marker in ("отсутствуют", "неизвестные поля", "ожидается объект", "без поля `id`"):
        assert marker in section, marker
    example = json.loads(re.search(r"```json\n(.*?)\n```", section, re.S).group(1))
    task = example["input"]["task"]["task"]
    assert "id" not in task
    assert task["method_inputs"] == []
    assert task["executable_obligations"] == ["requirements[0]"]


SUITES = {
    "regression": (
        test_domain_missing,
        test_domain_extra,
        test_domain_combined,
        test_domain_wrong_type,
        test_automatic_missing_obligations,
    ),
    "guard": (
        test_common_existing_diagnostics,
        test_valid_shapes_preserve_input,
        test_valid_automatic_and_explicit_creation,
        test_automatic_caller_id_still_forbidden,
    ),
    "docs": (test_documented_diagnostics_and_creation_fields,),
}


if __name__ == "__main__":
    # Print every assertion failure, including its actual diagnostic; unexpected
    # exceptions retain their full traceback and invalidate exact RED evidence.
    cases = SUITES[sys.argv[1]]
    failures = 0
    for case in cases:
        try:
            case()
        except AssertionError as error:
            failures += 1
            print(f"FAIL {case.__name__}: {error}")
    if failures:
        print(f"FAILED {failures}/{len(cases)}")
        sys.exit(1)
    print(f"PASSED {len(cases)}/{len(cases)}")
