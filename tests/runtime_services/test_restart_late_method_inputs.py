"""Historical creation inputs do not become declarations for late registry methods."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from batch.helpers import request
from poise.application.tasks import TaskCommands
from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
from poise.modules.foundation.errors import DomainError, PoiseError, VersionConflict
from runtime_services.test_restart_registry_preservation import (
    assert_exact_delta, persisted, projection, ready_task, restart_task, setup_current,
    verification_calls,
)
from tasks.test_legacy_restart_requirements import _new_revised_context
from verification.test_current_registry_mutation import public_tools


DATA = json.loads((Path(__file__).parent / "fixtures/restart_late_method_inputs.json").read_text())


def replace_late(project, tools, before, newborn, changed_context):
    methods = [deepcopy(entry["method"]) for entry in before["current"]]
    methods[1]["argv"] = DATA["bash_prefix"] + before["current"][1]["method"]["argv"]
    patch = {"methods": methods}
    if changed_context:
        patch.update(_new_revised_context(tools, project, DATA["changed_requirement"]))
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "request_id": "replace-late-inputs",
        "expected_revision": newborn["revision"], "patch": patch, "remove": [],
    }))
    return edited


@pytest.mark.parametrize("changed_context", [False, True], ids=["frozen-context", "reagreed-context"])
def test_late_bash_replacement_preserves_registry_and_creation_inputs(project, changed_context):
    tools, before = setup_current(project, initially_empty=True, allow_requirements_edit=True)
    newborn = restart_task(tools, "T1")
    assert newborn["draft"]["methods"] == []
    assert newborn["draft"]["method_inputs"] == []
    edited = replace_late(project, tools, before, newborn, changed_context)
    assert ready_task(tools, "T1", edited["revision"])["status"] == "available"
    after = projection(tools)
    expected_registration = deepcopy(before["current"][1])
    expected_registration["method"]["argv"] = DATA["bash_prefix"] + before["current"][1]["method"]["argv"]
    assert_exact_delta(after, before, [before["current"][0], expected_registration], [{
        "kind": "replace", "method_id": "GREEN", "registration": expected_registration,
    }])
    record = tools.runtime.task_queries.record("T1")
    assert record["contract"]["method_inputs"] == []
    assert after["current"][1]["method"]["argv"][:4] == ["bash", "-c", "exec \"$@\"", "late-method"]
    assert after["current"][1]["method"]["argv"][4:] == before["current"][1]["method"]["argv"]
    if changed_context:
        assert record["contract"]["requirements"] == [DATA["changed_requirement"]]


@pytest.mark.parametrize("invalid", ["profile", "path"])
def test_restarted_original_inputs_remain_strict_and_atomic(project, invalid):
    tools, before = setup_current(project)
    newborn = restart_task(tools, "T1")
    inputs = [deepcopy(value) for value in project["task"]["method_inputs"]
              if value["method_id"] in ("RED", "GREEN")]
    if invalid == "profile":
        inputs[0]["reference_profile"] = deepcopy(DATA["unsupported_profile"])
        diagnostic = "method RED: unsupported reference_profile"
    else:
        inputs[0]["repository_inputs"] = ["../outside.py"]
        diagnostic = "method RED: invalid repository path"
    edited = tools.invoke(request("task", {
        "action": "edit", "task_id": "T1", "request_id": "invalid-original-inputs",
        "expected_revision": newborn["revision"], "patch": {
            "methods": [entry["method"] for entry in before["current"]], "method_inputs": inputs,
        }, "remove": [],
    }))
    rows = persisted(tools, "T1")
    public_before = projection(tools)
    with pytest.raises(DomainError, match=diagnostic):
        ready_task(tools, "T1", edited["revision"])
    assert persisted(tools, "T1") == rows
    assert projection(tools) == public_before


def test_late_replacement_rollback_reload_and_retry_preserve_state(project, monkeypatch):
    tools, before = setup_current(project, initially_empty=True)
    newborn = restart_task(tools, "T1")
    edited = replace_late(project, tools, before, newborn, False)
    rows = persisted(tools, "T1")
    public_before = projection(tools)
    requirements_before = tools.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}])
    promote = SqliteTaskRepository.promote_prepared

    def interrupted(repository, *args, **kwargs):
        promote(repository, *args, **kwargs)
        raise PoiseError("Late replacement interrupted before commit")

    with monkeypatch.context() as fault:
        fault.setattr(SqliteTaskRepository, "promote_prepared", interrupted)
        with pytest.raises(PoiseError, match="Late replacement interrupted before commit"):
            ready_task(tools, "T1", edited["revision"])
    reloaded = public_tools(project, "registry-executor")
    assert persisted(reloaded, "T1") == rows
    assert projection(reloaded) == public_before
    assert reloaded.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}]) == requirements_before
    assert ready_task(reloaded, "T1", edited["revision"])["status"] == "available"
    assert projection(reloaded)["revision"] == 2
    assert projection(reloaded)["current"][1]["method"]["argv"][:4] == ["bash", "-c", "exec \"$@\"", "late-method"]


def test_completed_late_ready_replay_preserves_later_work_after_reload(project, monkeypatch, verification_calls):
    tools, before = setup_current(project, initially_empty=True)
    newborn = restart_task(tools, "T1")
    edited = replace_late(project, tools, before, newborn, False)
    calls_before = list(verification_calls)
    bash_calls = []
    popen = subprocess.Popen

    def observe(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and list(argv[:4]) == ["bash", "-c", "exec \"$@\"", "late-method"]:
            bash_calls.append(list(argv))
        return popen(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", observe)
    ready = ready_task(tools, "T1", edited["revision"])
    reloaded = public_tools(project, "registry-executor")
    resumed = reloaded.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert resumed["status"] == "active"
    later = persisted(reloaded, "T1")
    registry = projection(reloaded)
    replay = ready_task(reloaded, "T1", edited["revision"])
    assert replay["replayed"] is True
    assert {key: value for key, value in replay.items() if key not in ("interaction", "replayed")} == {
        key: value for key, value in ready.items() if key not in ("interaction", "replayed")
    }
    assert persisted(reloaded, "T1") == later
    assert projection(reloaded) == registry
    assert verification_calls == calls_before
    assert bash_calls == []


def test_late_ready_conflict_preserves_interleaved_edit_and_allows_new_intent(project, monkeypatch):
    tools, before = setup_current(project, initially_empty=True)
    newborn = restart_task(tools, "T1")
    edited = replace_late(project, tools, before, newborn, False)
    prepare = TaskCommands.prepare_newborn_ready
    interleaved = []

    def edit_after_preparation(commands, *args, **kwargs):
        prepared = prepare(commands, *args, **kwargs)
        methods = deepcopy(edited["draft"]["methods"])
        methods[1]["verification_plan"]["responsibility"] = "Validate the second explicitly authorised intent."
        changed = tools.invoke(request("task", {
            "action": "edit", "task_id": "T1", "request_id": "interleaved-late-edit",
            "expected_revision": edited["revision"], "patch": {"methods": methods}, "remove": [],
        }))
        interleaved.append((changed["revision"], persisted(tools, "T1")))
        return prepared

    with monkeypatch.context() as conflict:
        conflict.setattr(TaskCommands, "prepare_newborn_ready", edit_after_preparation)
        with pytest.raises(VersionConflict):
            ready_task(tools, "T1", edited["revision"])
    assert len(interleaved) == 1
    assert persisted(tools, "T1") == interleaved[0][1]
    assert ready_task(tools, "T1", interleaved[0][0], "ready-after-conflict")["status"] == "available"
    assert projection(tools)["revision"] == 2
