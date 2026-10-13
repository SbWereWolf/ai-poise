"""Current registry selection at integration, not the frozen creation contract."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sqlite3
from pathlib import Path
import sys

import pytest

from conftest import git
from poise.modules.foundation.errors import PoiseError

from .helpers import integration_input, prepare_completed_task, request, source_change


def current_method(method_id="CURRENT", marker="current-registry", *, fail_on_drift=False):
    guard = "assert not Path('integration-block.txt').exists(), 'current registry guard';" if fail_on_drift else ""
    return {
        "id": method_id,
        "argv": [sys.executable, "-B", "-c", f"from pathlib import Path;{guard}print({marker!r})"],
        "cwd": ".", "environment": {},
        "source_under_test": {"kind": "repository", "bindings": [{"kind": "cwd", "path": "."}]},
        "verification_plan": {
            "responsibility": "Run the current accepted source guard at integration.",
            "change_surface": ["src/**"], "red_stages": [],
            "green_stages": ["implementation"], "red_failure": None,
        },
        "expected_exit_code": 0, "stdout_contains": [marker], "stderr_contains": [],
    }


def registration(method, *, stages=("implementation",)):
    return {"method": method, "stages": list(stages), "evidence_kind": "executable_test", "covers": ["requirements[0]"]}


def registry_change(*operations):
    return {"request_id": "current-integration-registry", "expected_revision": 0,
            "operations": list(operations), "executable_obligations": []}


def put(kind, method, *, stages=("implementation",)):
    return {"kind": kind, "method_id": method["id"],
            "registration": registration(method, stages=stages)}


def completed(project, *, initial=(), operations=()):
    return prepare_completed_task(
        project, source_change, methods=initial, checks=[method["id"] for method in initial],
        terminal_stage=True,
        registry_change=registry_change(*operations),
    )


def creation_contract(project):
    database = (project["root"] / project["cfg"]["paths"]["state"]
                / project["cfg"]["paths"]["database"])
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT metadata FROM tasks WHERE id=?", ("T1",)).fetchone()
    return json.loads(row[0])["contract"]


def assert_raw_check(check, method, marker):
    assert check["method"] == method["id"]
    assert check["argv"] == method["argv"]
    assert check["passed"] is True
    stdout, stderr = Path(check["stdout"]).read_bytes(), Path(check["stderr"]).read_bytes()
    assert stdout == (marker + "\n").encode()
    assert stderr == b""
    assert hashlib.sha256(stdout).hexdigest() == check["stdout_digest"]
    assert hashlib.sha256(stderr).hexdigest() == check["stderr_digest"]


@pytest.mark.parametrize("change_kind", ["add", "replace", "remove"])
def test_completed_integration_uses_current_registry_not_creation_methods(project, change_kind):
    old = current_method("CHECK", "obsolete-creation")
    new = current_method("CHECK", "current-definition")
    initial = [] if change_kind == "add" else [old]
    operations = ([{"kind": "remove", "method_id": "CHECK"}] if change_kind == "remove"
                  else [put(change_kind, new)])
    tools, worktree, accepted = completed(project, initial=initial, operations=operations)
    historical = creation_contract(project)
    registry = tools.runtime.task_queries.verification_registry("T1")
    assert historical["methods"] == initial
    assert registry["revision"] == 1
    expected = [] if change_kind == "remove" else [new]
    assert [entry["method"] for entry in registry["current"]] == expected

    integrated = tools.invoke(request("integrate", integration_input(project, accepted)))

    assert integrated["status"] == "integrated"
    assert [item["method"] for item in integrated["checks"]] == [m["id"] for m in expected]
    if expected:
        assert_raw_check(integrated["checks"][0], new, "current-definition")
    assert not worktree.exists()
    assert git(project["app"], "rev-parse", "HEAD") == integrated["target_after"]
    assert creation_contract(project) == historical
    assert tools.runtime.task_queries.verification_registry("T1") == registry


def test_newly_registered_failing_guard_blocks_target_publication_and_cleanup(project):
    method = current_method(fail_on_drift=True)
    tools, worktree, accepted = completed(project, operations=[put("add", method)])
    target_file = project["app"] / "integration-block.txt"
    target_file.write_text("Current target must be rejected by the current guard.\n")
    git(project["app"], "add", target_file.name)
    git(project["app"], "commit", "-m", "test: incompatible integration target")
    target = git(project["app"], "rev-parse", "HEAD")

    result = tools.invoke(request("integrate", integration_input(project, accepted)))

    assert result["status"] == "blocked", "A current registry failure must block publication."
    assert result["phase"] == "checks_failed"
    assert [check["method"] for check in result["checks"]] == ["CURRENT"]
    assert result["checks"][0]["passed"] is False
    assert b"current registry guard" in Path(result["checks"][0]["stderr"]).read_bytes()
    assert git(project["app"], "rev-parse", "HEAD") == target
    assert worktree.exists()
    assert result["accepted_commit"] == accepted
    assert tools.runtime.task_queries.record("T1")["last_report"]["commit"] == accepted


def test_unavailable_current_registry_is_not_replaced_by_empty_historical_methods(project, monkeypatch):
    tools, worktree, accepted = completed(project, operations=[put("add", current_method())])
    target = git(project["app"], "rev-parse", "HEAD")

    def unavailable(_task_id):
        raise PoiseError("current registry unavailable")

    monkeypatch.setattr(tools.runtime.task_queries, "verification_registry", unavailable)
    with pytest.raises(PoiseError, match="current registry unavailable"):
        tools.invoke(request("integrate", integration_input(project, accepted)))
    assert git(project["app"], "rev-parse", "HEAD") == target
    assert worktree.exists()


def test_completed_integration_replay_keeps_actual_receipts_without_new_registry_reads(project, monkeypatch):
    method = current_method()
    tools, _worktree, accepted = completed(project, operations=[put("add", method)])
    payload = integration_input(project, accepted)
    integrated = tools.invoke(request("integrate", payload))
    assert len(integrated["checks"]) == 1
    assert_raw_check(integrated["checks"][0], method, "current-registry")

    def forbidden(_task_id):
        raise AssertionError("Terminal replay must not create fresh verification.")

    monkeypatch.setattr(tools.runtime.task_queries, "verification_registry", forbidden)
    replay = tools.invoke(request("integrate", payload))
    assert replay["status"] == "integrated"
    assert replay["replayed"] is True
    assert replay["checks"] == integrated["checks"]
    assert replay["publication"] == integrated["publication"]
