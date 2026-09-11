from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

from batch.helpers import request
from conftest import write_json
from harness.application.work import WorkTools
from harness.common import HarnessError
from harness.modules.foundation.errors import DomainError
from harness.modules.verification.domain import CheckRegistry
from harness.runtime import Harness


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
) -> dict:
    value = {
        "id": identifier,
        "argv": [sys.executable, "-B", "-c", "print('OK')"],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 10,
        "expected_exit_code": expected_exit_code,
        "stdout_contains": ["OK"] if stdout_contains is None else stdout_contains,
        "stderr_contains": [],
    }
    if source_under_test is not None:
        value["source_under_test"] = source_under_test
    return value


def registry(methods: list[dict], checks: dict | None = None) -> CheckRegistry:
    schedule = {"red": [], "green": []} if checks is None else checks
    return CheckRegistry.from_task(methods, schedule, STAGES)


def task_tools(project, selected: dict, actor: str) -> tuple[WorkTools, dict]:
    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = []
    write_json(project["config_path"], cfg)
    task = deepcopy(project["task"])
    task["methods"] = [selected]
    task["checks"] = {
        stage["id"]: [selected["id"]] if stage["id"] == "tests" else []
        for stage in project["process"]["stages"]
    }
    write_json(project["task_path"], task)
    tools = WorkTools(Harness(project["config_path"], actor))
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

    with pytest.raises(HarnessError, match="provenance|source_under_test|source"):
        tools.invoke(
            request(
                "verify",
                {"result": stage_result(context), "artifacts": []},
            )
        )

    assert not counter.exists()


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
