"""Resolved Task write scopes must govern creation and later rework."""

from poise.common import PoiseError
from poise.application.work import WorkTools

from conftest import WorkPoise as Poise
from ddd.test_creation_method_preflight import (
    MISSING_TEST,
    _bootstrap,
    _configure,
    _inline_inputs,
    _inline_method,
    _inputs,
    _intent,
    _stage,
    _task,
)


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

    before = tools.runtime.task_queries.summary()
    try:
        _bootstrap(tools, _intent(task, "resolved-scope-rejected"))
    except PoiseError as error:
        assert "RESOLVED_SCOPE_GREEN" in str(error)
        assert "allowed_paths" in str(error)
    else:
        raise AssertionError("creation accepted a write surface frozen by the Task contract")
    assert tools.runtime.task_queries.summary() == before


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

    before = tools.runtime.task_queries.summary()
    try:
        _bootstrap(tools, _intent(task, "resolved-future-output-rejected"))
    except PoiseError as error:
        assert MISSING_TEST in str(error)
        assert "allowed_paths" in str(error)
    else:
        raise AssertionError("creation accepted a future output frozen by the Task contract")
    assert tools.runtime.task_queries.summary() == before
