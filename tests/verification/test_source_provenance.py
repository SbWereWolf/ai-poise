from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys

import pytest

from batch.helpers import request
from conftest import DeterministicClock, verification_plan, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.foundation.errors import DomainError
from poise.modules.verification.domain import CheckRegistry
from poise.runtime import Poise


STAGES = ("red", "green")


def repository_source(*bindings: dict) -> dict:
    return {"kind": "repository", "bindings": list(bindings)}


def cwd_binding(path: str = ".") -> dict:
    return {"kind": "cwd", "path": path}


def environment_binding(name: str = "PYTHONPATH", path: str = "src") -> dict:
    return {"kind": "environment", "name": name, "path": path}


def external_source(reason: str = "The command reads no repository source.") -> dict:
    return {"kind": "external", "reason": reason}


def method(
    identifier: str = "M",
    *,
    expected_exit_code: int = 0,
    stdout_contains: list[str] | None = None,
    source_under_test: dict | None = None,
    plan: dict | None | object = ...,
) -> dict:
    value = {
        "id": identifier,
        "argv": [sys.executable, "-B", "-c", "print('OK')"],
        "cwd": ".",
        "environment": {},
        "expected_exit_code": expected_exit_code,
        "stdout_contains": ["OK"] if stdout_contains is None else stdout_contains,
        "stderr_contains": [],
    }
    selected_stdout = value["stdout_contains"]
    if plan is ...:
        value["verification_plan"] = verification_plan(
            "Verify the declared repository behaviour.",
            ["tests/**"],
            red_stages=["red"] if expected_exit_code != 0 else [],
            green_stages=[] if expected_exit_code != 0 else ["green"],
            red_failure=(
                {
                    "exit_code": expected_exit_code,
                    "stdout_equals": (
                        selected_stdout[0] + "\n"
                        if len(selected_stdout) == 1
                        else ""
                    ),
                    "stderr_equals": "",
                }
                if expected_exit_code != 0
                else None
            ),
        )
    elif plan is not None:
        value["verification_plan"] = plan
    if source_under_test is not None:
        value["source_under_test"] = source_under_test
    return value


def registry(methods: list[dict], checks: dict | None = None) -> CheckRegistry:
    schedule = {"red": [], "green": []} if checks is None else checks
    selected = [deepcopy(method_value) for method_value in methods]
    for method_value in selected:
        plan = method_value.get("verification_plan")
        if plan is None:
            continue
        stages = [stage for stage in STAGES if method_value["id"] in schedule[stage]]
        is_red = method_value["expected_exit_code"] != 0
        plan["red_stages"] = stages if is_red else []
        plan["green_stages"] = [] if is_red else stages
    return CheckRegistry.from_task(selected, schedule, STAGES)


def task_tools(
    project,
    selected: dict,
    actor: str,
    *,
    task_id: str = "T1",
) -> tuple[WorkTools, dict]:
    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = []
    write_json(project["config_path"], cfg)
    task = deepcopy(project["task"])
    task["id"] = task_id
    selected = deepcopy(selected)
    if "verification_plan" in selected:
        planned = selected["verification_plan"]
        is_red = selected["expected_exit_code"] != 0
        planned["red_stages"] = ["tests"] if is_red else []
        planned["green_stages"] = [] if is_red else ["tests"]
    task["methods"] = [selected]
    task["method_inputs"] = [{
        "method_id": selected["id"],
        "repository_inputs": [],
        "future_outputs": [],
        "reference_profile": {
            "runner": "python",
            "parser": "inline-no-path-arguments",
            "version": 1,
        },
    }]
    task["checks"] = {
        stage["id"]: [selected["id"]] if stage["id"] == "tests" else []
        for stage in project["process"]["stages"]
    }
    write_json(project["task_path"], task)
    tools = WorkTools(Poise(project["config_path"], actor, DeterministicClock()))
    context = tools.invoke(
        request(
            "bootstrap",
            {
                "task": task,
                "decision": None,
                "feedback": None,
                "rework_stage": None,
            },
        )
    )
    return tools, context


def stage_result(context: dict, report: str = "Provenance scenario executed.") -> dict:
    result = deepcopy(context["result_template"])
    result["sections"]["report"] = report
    result["commit_message"] = "test: verify source provenance"
    return result


def test_new_method_requires_explicit_source_under_test():
    with pytest.raises(DomainError, match="source_under_test"):
        registry([method()], {"red": ["M"], "green": []})

    registry(
        [method(source_under_test=repository_source(cwd_binding()))],
        {"red": ["M"], "green": []},
    )
    registry(
        [method(source_under_test=external_source())],
        {"red": ["M"], "green": []},
    )


def test_verification_method_rejects_timeout_field():
    selected = method(source_under_test=repository_source(cwd_binding()))
    selected["timeout_seconds"] = 10

    with pytest.raises(DomainError, match="точный набор полей"):
        registry([selected], {"red": ["M"], "green": []})


def test_repository_source_rejects_escape_duplicate_and_environment_collision():
    invalid = [
        repository_source(cwd_binding("../outside")),
        repository_source(environment_binding(), environment_binding()),
    ]
    for source in invalid:
        with pytest.raises(DomainError, match="source_under_test|binding|repository"):
            registry(
                [method(source_under_test=source)],
                {"red": ["M"], "green": []},
            )

    colliding = method(source_under_test=repository_source(environment_binding()))
    colliding["environment"] = {"PYTHONPATH": "somewhere-else"}
    with pytest.raises(DomainError, match="environment|binding"):
        registry([colliding], {"red": ["M"], "green": []})


def test_external_source_requires_nonempty_reason():
    with pytest.raises(DomainError, match="reason"):
        registry(
            [method(source_under_test={"kind": "external", "reason": ""})],
            {"red": ["M"], "green": []},
        )


def test_semantic_duplicate_is_rejected_before_execution():
    first = method("FIRST", source_under_test=repository_source(cwd_binding()))
    second = {**first, "id": "SECOND"}
    with pytest.raises(DomainError, match="semantic duplicate|семантический дубликат"):
        registry(
            [first, second],
            {"red": ["FIRST"], "green": ["SECOND"]},
        )


def test_overlapping_conflict_is_rejected_before_execution():
    passing = method("PASS", source_under_test=repository_source(cwd_binding()))
    failing = method(
        "FAIL",
        expected_exit_code=1,
        stdout_contains=["INTENDED_FAILURE"],
        source_under_test=repository_source(cwd_binding()),
    )
    with pytest.raises(DomainError, match="conflict|конфликт"):
        registry(
            [passing, failing],
            {"red": ["PASS", "FAIL"], "green": []},
        )


def test_overlapping_methods_with_distinct_source_bindings_are_allowed():
    source_tree = method(
        "SOURCE_TREE",
        source_under_test=repository_source(environment_binding(path="src")),
    )
    library_tree = method(
        "LIBRARY_TREE",
        source_under_test=repository_source(environment_binding(path="library")),
    )

    registry(
        [source_tree, library_tree],
        {"red": ["SOURCE_TREE", "LIBRARY_TREE"], "green": []},
    )


def test_phase_disjoint_red_green_methods_are_allowed():
    passing = method("GREEN", source_under_test=repository_source(cwd_binding()))
    failing = method(
        "RED",
        expected_exit_code=1,
        stdout_contains=["INTENDED_FAILURE"],
        source_under_test=repository_source(cwd_binding()),
    )
    registry(
        [passing, failing],
        {"red": ["RED"], "green": ["GREEN"]},
    )


def test_nonzero_expectation_requires_failure_marker():
    unbound = method(
        expected_exit_code=1,
        stdout_contains=[],
        source_under_test=repository_source(cwd_binding()),
    )
    with pytest.raises(DomainError, match="failure|отказ|marker|признак"):
        registry([unbound], {"red": ["M"], "green": []})


def test_repository_binding_uses_current_worktree(project):
    selected = method(
        source_under_test=repository_source(environment_binding()),
    )
    selected["argv"] = [
        sys.executable,
        "-B",
        "-c",
        "import os; print(os.environ['PYTHONPATH'])",
    ]
    selected["stdout_contains"] = ["src"]
    tools, context = task_tools(project, selected, "provenance-binding")

    result = tools.invoke(
        request(
            "verify",
            {"result": stage_result(context), "artifacts": []},
        )
    )

    assert result["status"] == "verified"
    receipt = result["checks"][0]
    expected = str((Path(context["worktree"]) / "src").resolve())
    assert receipt["source_provenance"] == {
        "kind": "repository",
        "bindings": [
            {
                "kind": "environment",
                "name": "PYTHONPATH",
                "path": "src",
                "resolved_path": expected,
            }
        ],
    }
    assert receipt["provenance_digest"]
    assert expected in Path(receipt["stdout"]).read_text(encoding="utf-8")


def test_missing_repository_binding_does_not_start_command(project):
    counter = project["root"] / "provenance-command-started"
    selected = method(
        source_under_test=repository_source(
            environment_binding(path="missing-source-root")
        )
    )
    selected["argv"] = [
        sys.executable,
        "-B",
        "-c",
        f"from pathlib import Path; Path({str(counter)!r}).write_text('started')",
    ]
    selected["stdout_contains"] = []
    tools, context = task_tools(project, selected, "missing-provenance")

    with pytest.raises(PoiseError, match="provenance|source_under_test|source"):
        tools.invoke(
            request(
                "verify",
                {"result": stage_result(context), "artifacts": []},
            )
        )

    assert not counter.exists()


def test_repository_environment_binding_rejects_inherited_collision(tmp_path):
    from poise.runtime import resolve_source_under_test

    (tmp_path / "src").mkdir()
    selected = method(
        source_under_test=repository_source(
            environment_binding(name="PATH", path="src")
        )
    )
    environment = {"PATH": "/trusted/bin"}

    with pytest.raises(PoiseError, match="environment|collision|конфликт"):
        resolve_source_under_test(
            selected,
            worktree=tmp_path,
            cwd=tmp_path,
            environment=environment,
        )

    assert environment == {"PATH": "/trusted/bin"}


def test_red_receipt_records_expectation_and_provenance_identity(project):
    selected = method(
        expected_exit_code=1,
        stdout_contains=["INTENDED_FAILURE"],
        source_under_test=repository_source(cwd_binding()),
    )
    selected["argv"] = [
        sys.executable,
        "-B",
        "-c",
        "print('INTENDED_FAILURE'); raise SystemExit(1)",
    ]
    tools, context = task_tools(project, selected, "red-provenance")

    result = tools.invoke(
        request(
            "verify",
            {"result": stage_result(context), "artifacts": []},
        )
    )

    assert result["status"] == "verified"
    receipt = result["checks"][0]
    assert receipt["passed"] is True
    assert receipt["expectation_digest"]
    assert receipt["provenance_digest"]
    assert receipt["source_provenance"]["bindings"][0]["resolved_path"] == str(
        Path(context["worktree"]).resolve()
    )


def test_exact_red_predicate_accepts_declared_failure_set(project):
    selected = method(
        expected_exit_code=1,
        stdout_contains=["EXPECTED_FAILURE"],
        source_under_test=repository_source(cwd_binding()),
        plan=verification_plan(
            "Prove the exact intended regression failure.",
            ["src/**"],
            red_stages=["red"],
            red_failure={
                "exit_code": 1,
                "stdout_equals": "EXPECTED_FAILURE\n",
                "stderr_equals": "",
            },
        ),
    )
    selected["argv"] = [
        sys.executable,
        "-B",
        "-c",
        "print('EXPECTED_FAILURE'); raise SystemExit(1)",
    ]
    tools, context = task_tools(project, selected, "exact-red")

    result = tools.invoke(request("verify", {"result": stage_result(context), "artifacts": []}))

    assert result["status"] == "verified"
    assert result["checks"][0]["passed"] is True


def test_exact_red_predicate_rejects_additional_failure(project):
    selected = method(
        expected_exit_code=1,
        stdout_contains=["EXPECTED_FAILURE"],
        source_under_test=repository_source(cwd_binding()),
        plan=verification_plan(
            "Reject any failure outside the declared regression predicate.",
            ["src/**"],
            red_stages=["red"],
            red_failure={
                "exit_code": 1,
                "stdout_equals": "EXPECTED_FAILURE\n",
                "stderr_equals": "",
            },
        ),
    )
    selected["argv"] = [
        sys.executable,
        "-B",
        "-c",
        "print('EXPECTED_FAILURE'); print('UNRELATED_FAILURE'); raise SystemExit(1)",
    ]
    tools, context = task_tools(project, selected, "exact-red-extra")

    result = tools.invoke(request("verify", {"result": stage_result(context), "artifacts": []}))

    assert result["status"] == "checks_failed"
    assert result["checks"][0]["passed"] is False


def test_receipt_identities_change_with_expectation_and_worktree(project):
    def execute(selected: dict, actor: str, task_id: str) -> dict:
        tools, context = task_tools(
            project,
            selected,
            actor,
            task_id=task_id,
        )
        result = tools.invoke(
            request(
                "verify",
                {"result": stage_result(context), "artifacts": []},
            )
        )
        assert result["status"] == "verified"
        return result["checks"][0]

    shared_expectation_argv = [
        sys.executable,
        "-B",
        "-c",
        "print('FIRST_FAILURE SECOND_FAILURE'); raise SystemExit(1)",
    ]
    first = method(
        expected_exit_code=1,
        stdout_contains=["FIRST_FAILURE"],
        source_under_test=external_source("No repository source is loaded."),
    )
    first["argv"] = shared_expectation_argv
    second = method(
        expected_exit_code=1,
        stdout_contains=["SECOND_FAILURE"],
        source_under_test=external_source("No repository source is loaded."),
    )
    for selected in (first, second):
        selected["verification_plan"]["red_failure"]["stdout_equals"] = (
            "FIRST_FAILURE SECOND_FAILURE\n"
        )
    second["argv"] = shared_expectation_argv
    first_receipt = execute(
        first,
        "identity-first",
        "EXPECTATION-FIRST",
    )
    second_receipt = execute(
        second,
        "identity-second",
        "EXPECTATION-SECOND",
    )

    assert first["id"] == second["id"]
    assert first["argv"] == second["argv"]
    assert first["source_under_test"] == second["source_under_test"]
    assert first_receipt["expectation_digest"] != second_receipt["expectation_digest"]
    assert first_receipt["provenance_digest"] == second_receipt["provenance_digest"]
    assert first_receipt["source_provenance"] == second_receipt["source_provenance"]

    repository_method = method(
        expected_exit_code=1,
        stdout_contains=["SOURCE_FAILURE"],
        source_under_test=repository_source(cwd_binding()),
    )
    repository_method["argv"] = [
        sys.executable,
        "-B",
        "-c",
        "print('SOURCE_FAILURE'); raise SystemExit(1)",
    ]
    first_source_receipt = execute(
        repository_method,
        "source-first",
        "SOURCE-FIRST",
    )
    second_source_receipt = execute(
        repository_method,
        "source-second",
        "SOURCE-SECOND",
    )

    assert (
        first_source_receipt["expectation_digest"]
        == second_source_receipt["expectation_digest"]
    )
    assert (
        first_source_receipt["provenance_digest"]
        != second_source_receipt["provenance_digest"]
    )
    assert (
        first_source_receipt["source_provenance"]
        != second_source_receipt["source_provenance"]
    )


def test_legacy_snapshot_is_readable_but_not_executable(tmp_path):
    legacy = method()
    legacy.pop("verification_plan")
    item = {"method": legacy, "stages": ["red"]}

    restored = CheckRegistry.from_items([item], STAGES)

    assert restored.entries[0].to_dict() == item
    from poise.runtime import resolve_source_under_test

    with pytest.raises(PoiseError, match="provenance|source_under_test|source"):
        resolve_source_under_test(
            legacy,
            worktree=tmp_path,
            cwd=tmp_path,
            environment={},
        )


def test_unrelated_nonzero_exit_does_not_satisfy_red(project):
    selected = method(
        expected_exit_code=1,
        stdout_contains=["INTENDED_FAILURE"],
        source_under_test=repository_source(cwd_binding()),
    )
    selected["argv"] = [
        sys.executable,
        "-B",
        "-c",
        "print('UNRELATED_FAILURE'); raise SystemExit(1)",
    ]
    tools, context = task_tools(project, selected, "unrelated-red")

    result = tools.invoke(
        request(
            "verify",
            {"result": stage_result(context), "artifacts": []},
        )
    )

    assert result["status"] == "checks_failed"
    assert result["checks"][0]["actual_exit_code"] == 1
    assert result["checks"][0]["passed"] is False


def test_example_task_methods_declare_source_provenance(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]

    def assert_declared(methods: list[dict]) -> None:
        assert methods
        for selected in methods:
            assert "source_under_test" in selected, selected["id"]

    for name in (
        "documentation-task.example.json",
        "evidence-task.example.json",
        "task.example.json",
    ):
        example = json.loads((root / "examples" / name).read_text(encoding="utf-8"))
        assert_declared(example["methods"])

    monkeypatch.syspath_prepend(str(root / "examples"))
    demo = importlib.import_module("demo")
    home = demo.create(tmp_path / "generated-demo")
    generated = json.loads((home / "task.json").read_text(encoding="utf-8"))
    assert_declared(generated["methods"])

    actions_demo = importlib.import_module("actions_demo")
    assert_declared([actions_demo.method("CHECK", "print('checked')")])

    sprint_demo = importlib.import_module("sprint_demo")
    assert_declared(
        sprint_demo.task("CHECK", "development", "print('checked')")["methods"]
    )
