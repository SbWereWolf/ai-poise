from copy import deepcopy
from pathlib import Path

import pytest

from poise.common import PoiseError
from runtime_services.test_failed_check_rework import (
    _fail_current_stage,
    _rework,
    _scenario,
)


def test_verify_and_failed_rework_use_one_canonical_execution_identity_builder(
    project,
    monkeypatch,
):
    tools, context = _scenario(project)
    runtime = tools.runtime
    original = runtime._verification_execution
    calls = []

    def observe(data, tree, checks, worktree):
        invocations, execution_key = original(data, tree, checks, worktree)
        calls.append((tree, invocations, execution_key))
        return invocations, execution_key

    monkeypatch.setattr(runtime, "_verification_execution", observe)

    _fail_current_stage(tools, context)
    _rework(tools)

    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][2] == calls[1][2]
    assert calls[0][1] == calls[1][1]
    invocation = calls[0][1][0]
    assert invocation["source_provenance"]
    assert invocation["provenance_digest"]
    assert invocation["expectation_digest"]


def test_canonical_execution_identity_changes_for_every_bound_input(project):
    tools, _ = _scenario(project)
    runtime = tools.runtime
    data = runtime.current_task()
    worktree = Path(data["worktree"])
    tree = runtime._tree(worktree)
    checks = runtime._select_checks(data, runtime._changed(data, tree))

    _, exact = runtime._verification_execution(data, tree, checks, worktree)

    changed_method = deepcopy(checks)
    changed_method[0]["argv"] = [*changed_method[0]["argv"], "changed-method"]
    _, method_key = runtime._verification_execution(
        data,
        tree,
        changed_method,
        worktree,
    )

    changed_expectation = deepcopy(checks)
    changed_expectation[0]["expected_exit_code"] = 2
    _, expectation_key = runtime._verification_execution(
        data,
        tree,
        changed_expectation,
        worktree,
    )

    changed_provenance = deepcopy(checks)
    changed_provenance[0]["source_under_test"] = {
        "kind": "repository",
        "bindings": [
            {"kind": "cwd", "path": "."},
            {"kind": "environment", "name": "SOURCE_ROOT", "path": "src"},
        ],
    }
    _, provenance_key = runtime._verification_execution(
        data,
        tree,
        changed_provenance,
        worktree,
    )

    _, tree_key = runtime._verification_execution(
        data,
        f"{tree}-changed",
        checks,
        worktree,
    )

    assert len({exact, method_key, expectation_key, provenance_key, tree_key}) == 5


@pytest.mark.parametrize("changed_input", ["method", "expectation", "provenance"])
def test_failed_rework_rejects_each_changed_bound_method_input(
    project,
    changed_input,
    monkeypatch,
):
    tools, context = _scenario(project)
    _fail_current_stage(tools, context)
    runtime = tools.runtime
    current = runtime.current_task()
    worktree = Path(current["worktree"])
    tree = runtime._tree(worktree)
    selected = deepcopy(runtime._select_checks(current, runtime._changed(current, tree)))
    method = selected[0]

    if changed_input == "method":
        method["argv"] = [
            method["argv"][0],
            "-c",
            "print('CHANGED_METHOD'); raise SystemExit(1)",
        ]
    elif changed_input == "expectation":
        method["expected_exit_code"] = 2
        method["stdout_contains"] = ["EXPECTED_EXIT_TWO"]
    else:
        method["source_under_test"] = {
            "kind": "repository",
            "bindings": [
                {"kind": "cwd", "path": "."},
                {"kind": "environment", "name": "SOURCE_ROOT", "path": "src"},
            ],
        }

    before = deepcopy(runtime.current_task())
    monkeypatch.setattr(runtime, "_select_checks", lambda data, changed: selected)

    with pytest.raises(PoiseError, match="точного доступного failed check batch"):
        _rework(tools)

    assert runtime.current_task() == before
