"""Resolved Task write scopes must govern creation and later rework."""

from copy import deepcopy
from pathlib import Path

import pytest

from poise.common import PoiseError
from poise.application.work import WorkTools

from batch.helpers import request, result, text_artifact, verify
from conftest import WorkPoise as Poise
from ddd.test_creation_method_preflight import (
    MISSING_TEST,
    _external_snapshot,
    _bootstrap,
    _configure,
    _inline_inputs,
    _inline_method,
    _inputs,
    _intent,
    _stage,
    _task,
)
from runtime_services.test_failed_check_rework import _fail_current_stage, _rework, _scenario
from tasks.test_newborn_lifecycle import create, edit, task_action


def _resolved_scope_case(project):
    process = _configure(project, [
        _stage("writer", allowed_paths=("docs/**",), target="green"),
        _stage("green", allowed_paths=(), target=None),
    ])
    task = _task(project, process, method_inputs=[])
    task["methods"] = [_inline_method(
        "RESOLVED_SCOPE_GREEN",
        responsibility="Verify the resolved documentation write scope.",
        surface=("docs/**",),
        green_stages=("green",),
    )]
    task["method_inputs"] = [_inline_inputs("RESOLVED_SCOPE_GREEN")]
    task["checks"] = {"writer": [], "green": ["RESOLVED_SCOPE_GREEN"]}
    task["stage_contracts"][0]["allowed_paths"] = ["src/**"]
    return process, task


def test_creation_rejects_change_surface_outside_resolved_stage_contract(project):
    _, task = _resolved_scope_case(project)
    tools = WorkTools(Poise(project["config_path"], "resolved-scope"))

    before = _external_snapshot(tools.runtime)
    try:
        _bootstrap(tools, _intent(task, "resolved-scope-rejected"))
    except PoiseError as error:
        assert "RESOLVED_SCOPE_GREEN" in str(error)
        assert "allowed_paths" in str(error)
    else:
        raise AssertionError("creation accepted a write surface frozen by the Task contract")
    assert _external_snapshot(tools.runtime) == before


def test_creation_rejects_future_output_outside_resolved_stage_contract(project):
    process = _configure(project, [
        _stage("producer", allowed_paths=("tests/**",), target="green"),
        _stage("green", allowed_paths=(), target=None),
    ])
    task = _task(
        project,
        process,
        method_inputs=_inputs(future=((MISSING_TEST, "producer"),)),
    )
    task["methods"][0]["verification_plan"]["green_stages"] = ["green"]
    task["checks"] = {"producer": [], "green": ["CHECK"]}
    task["stage_contracts"][0]["allowed_paths"] = ["docs/**"]
    tools = WorkTools(Poise(project["config_path"], "resolved-future-output"))

    before = _external_snapshot(tools.runtime)
    try:
        _bootstrap(tools, _intent(task, "resolved-future-output-rejected"))
    except PoiseError as error:
        assert MISSING_TEST in str(error)
        assert "allowed_paths" in str(error)
    else:
        raise AssertionError("creation accepted a future output frozen by the Task contract")
    assert _external_snapshot(tools.runtime) == before


def test_newborn_ready_rejects_frozen_future_output_then_accepts_planned_scope_revision(project):
    process = _configure(project, [
        _stage("producer", allowed_paths=("tests/**",), target="green"),
        _stage("green", allowed_paths=(), target=None),
    ])
    task = _task(
        project,
        process,
        method_inputs=_inputs(future=((MISSING_TEST, "producer"),)),
    )
    task["methods"][0]["verification_plan"]["green_stages"] = ["green"]
    task["checks"] = {"producer": [], "green": ["CHECK"]}
    task["stage_contracts"][0]["allowed_paths"] = ["docs/**"]
    tools = WorkTools(Poise(project["config_path"], "newborn-resolved-scope"))

    born = create(tools, "NEWBORN-SCOPE")
    draft = deepcopy(task)
    draft.pop("id")
    edited = edit(
        tools,
        "NEWBORN-SCOPE",
        born["revision"],
        draft,
        "plan-frozen-future-output",
    )
    before = _external_snapshot(tools.runtime)
    with pytest.raises(PoiseError, match="allowed_paths"):
        task_action(
            tools,
            action="ready",
            request_id="reject-frozen-future-output",
            task_id="NEWBORN-SCOPE",
            expected_revision=edited["revision"],
        )
    assert _external_snapshot(tools.runtime) == before

    contracts = deepcopy(task["stage_contracts"])
    contracts[0]["allowed_paths"] = ["tests/**"]
    revised = edit(
        tools,
        "NEWBORN-SCOPE",
        edited["revision"],
        {"stage_contracts": contracts},
        "authorize-future-output-before-ready",
    )
    ready = task_action(
        tools,
        action="ready",
        request_id="ready-after-authorized-scope-revision",
        task_id="NEWBORN-SCOPE",
        expected_revision=revised["revision"],
    )
    assert ready["status"] == "available"
    assert tools.runtime.task_queries.record("NEWBORN-SCOPE")["contract"]["stage_contracts"] == contracts


def test_direct_creation_accepts_output_owned_only_by_declared_rework_route(project):
    entry = _stage("check", allowed_paths=(), target=None)
    entry.update(
        handler="inspect",
        role="reviewer",
        transitions={"clear": "green", "changes_requested": "repair"},
    )
    entry["rework_targets"] = ["repair"]
    repair = _stage("repair", allowed_paths=("tests/**",), target="green")
    green = _stage("green", allowed_paths=(), target=None)
    process = _configure(project, [entry, repair, green])
    task = _task(
        project,
        process,
        method_inputs=_inputs(future=((MISSING_TEST, "repair"),)),
    )
    task["methods"][0]["verification_plan"]["green_stages"] = ["repair"]
    task["checks"] = {"check": [], "repair": ["CHECK"], "green": []}

    tools = WorkTools(Poise(project["config_path"], "rework-only-producer"))
    context = _bootstrap(tools, _intent(task, "accept-rework-only-producer"))

    assert context["status"] == "active"
    assert context["stage"] == "check"
    record = tools.runtime.task_queries.record(context["task"])
    repair_contract = next(
        item for item in record["contract"]["stage_contracts"]
        if item["stage_id"] == "repair"
    )
    assert repair_contract["allowed_paths"] == ["tests/**"]


def test_registered_artifact_identity_survives_rework_and_still_rejects_overwrite(project):
    tools, context = _scenario(project)
    created = tools.invoke(request("artifacts", {
        "items": [text_artifact(path="immutable-before-rework.md", text="fixed bytes")],
    }))
    artifact = created["artifacts"][0]
    records_before = tools.runtime.store.artifact_records(context["task"])
    registered = next(item for item in records_before if item["id"] == artifact["id"])
    _fail_current_stage(tools, context)

    recovered = _rework(tools)

    assert tools.runtime.store.artifact_records(context["task"]) == records_before
    assert registered["path"] == artifact["path"]
    assert registered["digest"]
    Path(artifact["path"]).write_text("replacement bytes", encoding="utf-8")
    snapshot = tools.runtime.task_queries.record(context["task"])
    with pytest.raises(PoiseError, match="Артефакт изменён после регистрации"):
        verify(tools, result(recovered, "rework must preserve artifact identity"))
    assert tools.runtime.task_queries.record(context["task"]) == snapshot
