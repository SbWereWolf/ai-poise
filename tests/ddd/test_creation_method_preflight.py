"""Task-creation preflight contract tests."""

from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest

from batch.helpers import request
from conftest import WorkPoise as Poise
from conftest import write_json
from poise.application.work import WorkTools
from poise.common import PoiseError


PROFILE = {"runner": "pytest", "parser": "positional-paths", "version": 1}
MISSING_TEST = "tests/contract/test_missing_creation_path.py"


def _stage(identifier, *, allowed_paths, target):
    return {
        "id": identifier,
        "instruction": f"Execute {identifier}.",
        "read_only": not bool(allowed_paths),
        "allowed_paths": list(allowed_paths),
        "normalization": "strip",
        "sections": {"report": "Report."},
        "required_sections": ["report"],
        "artifact_requirements": [],
        "handler": "produce",
        "transitions": {"complete": target},
        "rework_targets": [identifier],
    }


def _configure(project, stages):
    process = deepcopy(project["process"])
    process.update(
        goal_type="documentation",
        stages=stages,
        route={"entry": stages[0]["id"], "max_transitions": 30, "max_stage_visits": 6},
    )
    write_json(project["root"] / "config/processes/documentation.json", process)
    cfg = deepcopy(project["cfg"])
    cfg["processes"] = {"documentation": "config/processes/documentation.json"}
    cfg["automatic_checks"] = []
    cfg["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 20},
        "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    write_json(project["config_path"], cfg)
    project["cfg"] = cfg
    return process


def _linear_process(project, *, producer_allowed=("tests/**",), producer_first=True):
    if producer_first:
        stages = [
            _stage("producer", allowed_paths=producer_allowed, target="validation"),
            _stage("validation", allowed_paths=(), target=None),
        ]
    else:
        stages = [
            _stage("validation", allowed_paths=(), target="producer"),
            _stage("producer", allowed_paths=producer_allowed, target=None),
        ]
    return _configure(project, stages)


def _method(path=MISSING_TEST):
    return {
        "id": "CHECK",
        "argv": [sys.executable, "-m", "pytest", "-q", path],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 30,
        "expected_exit_code": 0,
        "stdout_contains": [],
        "stderr_contains": [],
    }


def _task(project, process, *, method_inputs=...):
    task = deepcopy(project["task"])
    task.update(
        id="UNALLOCATED",
        sprint_id=None,
        goal_type="documentation",
        goal="Validate immutable documentation checks before Task creation.",
        requirements=["Every repository input is reachable."],
        definition_of_done=["Invalid paths are rejected before allocation."],
        methods=[_method()],
        checks={stage["id"]: (["CHECK"] if stage["id"] == "validation" else []) for stage in process["stages"]},
        evidence_plan={
            stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
            for stage in process["stages"]
        },
    )
    if method_inputs is not ...:
        task["method_inputs"] = deepcopy(method_inputs)
    return task


def _inputs(*, baseline=(), future=(), profile=PROFILE, method_id="CHECK"):
    return [{
        "method_id": method_id,
        "repository_inputs": list(baseline),
        "future_outputs": [
            {"path": path, "producer_stage": producer}
            for path, producer in future
        ],
        "reference_profile": deepcopy(profile),
    }]


def _intent(task, request_id):
    candidate = deepcopy(task)
    del candidate["id"]
    return {"request_id": request_id, "task": candidate}


def _bootstrap(tools, intent):
    return tools.invoke(request("bootstrap", {
        "task": deepcopy(intent),
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))


def _external_snapshot(runtime):
    repository = Path(runtime.cfg["git"]["repository"])
    refs = subprocess.check_output(
        ["git", "-C", str(repository), "for-each-ref", "--format=%(refname)", "refs/heads/tasks/"],
        text=True,
    ).splitlines()
    worktrees = subprocess.check_output(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        text=True,
    )
    task_root = runtime.state / runtime.paths["tasks"]
    task_files = sorted(str(path.relative_to(task_root)) for path in task_root.rglob("*")) if task_root.exists() else []
    return {
        "tasks": runtime.task_queries.summary(),
        "refs": refs,
        "worktrees": worktrees,
        "task_files": task_files,
        "binding": runtime.current_task(),
    }


def _assert_rejected(tools, intent, *, tokens):
    before = _external_snapshot(tools.runtime)
    with pytest.raises(PoiseError) as caught:
        _bootstrap(tools, intent)
    message = str(caught.value)
    for token in tokens:
        assert token in message
    assert any(word in message.lower() for word in ("исправ", "укажите", "declare", "use a new"))
    assert _external_snapshot(tools.runtime) == before


def test_legacy_documentation_task_with_missing_pytest_path_is_rejected_before_allocation(project):
    process = _linear_process(project, producer_allowed=("docs/**",))
    tools = WorkTools(Poise(project["config_path"], "legacy-red"))
    legacy = _task(project, process)

    _assert_rejected(
        tools,
        _intent(legacy, "legacy-invalid-path"),
        tokens=("CHECK", "method_inputs"),
    )

    escaped_process = _linear_process(project)
    escaped_tools = WorkTools(Poise(project["config_path"], "escaped-cwd-red"))
    escaped = _task(
        project,
        escaped_process,
        method_inputs=_inputs(future=((MISSING_TEST, "producer"),)),
    )
    escaped["methods"][0]["cwd"] = "../outside"
    _assert_rejected(
        escaped_tools,
        _intent(escaped, "escaped-method-cwd"),
        tokens=("CHECK", "../outside", "cwd"),
    )


def test_existing_baseline_input_and_explicit_empty_future_are_accepted(project):
    process = _linear_process(project)
    tools = WorkTools(Poise(project["config_path"], "baseline-positive"))
    task = _task(project, process, method_inputs=_inputs(baseline=("src/double.py",)))
    task["methods"] = [_method("src/double.py")]

    result = _bootstrap(tools, _intent(task, "baseline-positive"))

    assert result["task"] == "0001"
    assert result["allocation"]["replayed"] is False
    assert tools.runtime.task_queries.record("0001")["contract"]["method_inputs"] == task["method_inputs"]


def test_reachable_future_output_allowed_before_first_execution_is_accepted(project):
    process = _linear_process(project)
    tools = WorkTools(Poise(project["config_path"], "future-positive"))
    task = _task(
        project,
        process,
        method_inputs=_inputs(future=((MISSING_TEST, "producer"),)),
    )

    result = _bootstrap(tools, _intent(task, "future-positive"))

    assert result["task"] == "0001"
    assert not (Path(result["worktree"]) / MISSING_TEST).exists()


@pytest.mark.parametrize(
    ("case", "process_options", "inputs", "tokens"),
    [
        (
            "missing-producer",
            {},
            _inputs(future=((MISSING_TEST, "unknown"),)),
            ("CHECK", MISSING_TEST, "unknown"),
        ),
        (
            "producer-after-execution",
            {"producer_first": False},
            _inputs(future=((MISSING_TEST, "producer"),)),
            ("CHECK", MISSING_TEST, "producer"),
        ),
        (
            "allowed-path-mismatch",
            {"producer_allowed": ("docs/**",)},
            _inputs(future=((MISSING_TEST, "producer"),)),
            ("CHECK", MISSING_TEST, "allowed_paths"),
        ),
        (
            "path-escape",
            {},
            _inputs(future=(("../outside.py", "producer"),)),
            ("CHECK", "../outside.py", "path"),
        ),
        (
            "duplicate",
            {},
            _inputs(baseline=("src/double.py", "src/double.py")),
            ("CHECK", "src/double.py", "duplicate"),
        ),
        (
            "classification-conflict",
            {},
            _inputs(
                baseline=(MISSING_TEST,),
                future=((MISSING_TEST, "producer"),),
            ),
            ("CHECK", MISSING_TEST, "baseline"),
        ),
        (
            "runner-profile-ref-mismatch",
            {},
            _inputs(baseline=("src/double.py",)),
            ("CHECK", MISSING_TEST, "reference"),
        ),
        (
            "method-binding-mismatch",
            {},
            _inputs(future=((MISSING_TEST, "producer"),), method_id="OTHER"),
            ("OTHER", "CHECK", "binding"),
        ),
    ],
)
def test_invalid_method_input_contract_is_rejected_atomically(
    project, case, process_options, inputs, tokens
):
    process = _linear_process(project, **process_options)
    tools = WorkTools(Poise(project["config_path"], case))
    task = _task(project, process, method_inputs=inputs)

    _assert_rejected(tools, _intent(task, case), tokens=tokens)


def test_missing_baseline_path_is_rejected_with_actionable_method_context(project):
    process = _linear_process(project)
    tools = WorkTools(Poise(project["config_path"], "missing-baseline"))
    task = _task(project, process, method_inputs=_inputs(baseline=(MISSING_TEST,)))

    _assert_rejected(
        tools,
        _intent(task, "missing-baseline"),
        tokens=("CHECK", MISSING_TEST, "base"),
    )


def test_invalid_exact_replay_stays_empty_and_corrected_new_request_uses_digest(project):
    process = _linear_process(project)
    tools = WorkTools(Poise(project["config_path"], "replay"))
    invalid = _task(project, process, method_inputs=_inputs(baseline=(MISSING_TEST,)))
    intent = _intent(invalid, "invalid-request")

    _assert_rejected(tools, intent, tokens=("CHECK", MISSING_TEST, "base"))
    _assert_rejected(tools, intent, tokens=("CHECK", MISSING_TEST, "base"))

    corrected = _task(
        project,
        process,
        method_inputs=_inputs(future=((MISSING_TEST, "producer"),)),
    )
    result = _bootstrap(tools, _intent(corrected, "corrected-request"))
    record = tools.runtime.task_queries.record(result["task"])
    assert result["task"] == "0001"
    assert record["creation_request"]["digest"]
    assert record["contract"]["method_inputs"] == corrected["method_inputs"]
