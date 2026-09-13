from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from conftest import write_json
from poise.common import PoiseError
from poise.modules.foundation.errors import DomainError
from poise.modules.tasks.domain import TaskStageContracts
from runner.helpers import decision, finding, inspect, process, resolution, task, verify
from runner.test_runner_paths import edit, result, setup_project


EXPECTED_ERROR = (
    "Rework недоступен: исправления R1 ещё не осмотрены. "
    "Продолжите задачу на этап follow_up и рассмотрите каждое исправление."
)


def pending_runtime(project):
    runtime = setup_project(project, "development")
    configured = runtime.processes["development"]
    project["task"]["stage_contracts"] = [
        {
            "stage_id": stage["id"],
            "allowed_paths": list(stage["allowed_paths"]),
            "entry_requirements": [],
            "exit_requirements": [],
        }
        for stage in configured["stages"]
    ]
    write_json(project["task_path"], project["task"])
    context = runtime.bootstrap(task_file=project["task_path"])
    edit(context, "development", "initial\n")
    result(context, {})
    runtime.verify()

    context = runtime.bootstrap(decision="continue")
    result(context, inspect([finding()]))
    runtime.verify()

    context = runtime.bootstrap(decision="continue")
    edit(context, "development", "resolved\n")
    result(context, {"resolutions": [resolution()]})
    runtime.verify()
    return runtime


def task_with_contracts(route=None):
    configured = process() if route is None else route
    current = task(configured)
    contracts = TaskStageContracts.parse(
        [
            {
                "stage_id": stage["id"],
                "allowed_paths": list(stage["allowed_paths"]),
                "entry_requirements": [],
                "exit_requirements": [],
            }
            for stage in configured["stages"]
        ],
        current.route,
        current.content_policy,
    )
    return replace(current, stage_contracts=contracts)


def pending_task(route=None):
    current = verify(task_with_contracts(route), {}).accept("S", True).task
    current = verify(current, inspect([finding()])).accept("S", True).task
    return verify(current, {"resolutions": [resolution()]})


def test_rework_rejects_pending_resolution_before_state_change(project):
    runtime = pending_runtime(project)
    record_before = deepcopy(runtime.task_queries.record("T1"))
    history_before = deepcopy(runtime.task_queries.history("T1"))
    counts_before = runtime.store.counts("T1")

    with pytest.raises(PoiseError) as caught:
        runtime.bootstrap(
            decision="rework",
            feedback="Переработать исправление до его осмотра.",
            rework_stage="amend",
        )

    assert str(caught.value) == EXPECTED_ERROR
    assert runtime.task_queries.record("T1") == record_before
    assert runtime.task_queries.history("T1") == history_before
    assert runtime.store.counts("T1") == counts_before


def test_failed_check_rework_rejects_pending_resolution_before_state_change():
    route = process()
    route["stages"][3]["rework_targets"].append("amend")
    current = pending_task(route).accept("S", True).task
    current = current.submit(
        "S",
        {"report": "Failed follow-up candidate."},
        (),
        "test: failed follow-up candidate",
        {"sections": [], "routes": [], "requirements": []},
        {},
        [],
        inspect(decisions=[decision("R1")]),
        {"phase": "prepare", "arguments": [], "decisions": []},
    ).task
    tree = "FAILED-TREE"
    execution_key = "FAILED-EXECUTION"
    receipt = {
        "id": "FAILED-RECEIPT",
        "method": "GUARD",
        "obligations": ["GUARD"],
        "passed": False,
        "timed_out": False,
        "actual_exit_code": 1,
        "tree": tree,
        "guard": True,
        "interpretable": True,
    }
    current = replace(
        current,
        evidence_book=current.evidence_book.record_submission_batch(
            current.stage.stage_id,
            current.state.iteration,
            tree,
            execution_key,
            current.state.submission_digest,
            [receipt],
        ),
    )
    before = current

    with pytest.raises(DomainError) as caught:
        current.rework_failed(
            "S",
            "Do not bypass pending resolution inspection after a failed check.",
            tree,
            execution_key,
            "amend",
        )

    assert str(caught.value) == EXPECTED_ERROR
    assert current == before


def test_failed_check_rework_rejects_foreign_owner_before_state_change():
    current = task_with_contracts()
    current = current.submit(
        "S",
        {"report": "Failed owned candidate."},
        (),
        "test: failed owned candidate",
        {"sections": [], "routes": [], "requirements": []},
        {},
        [],
        {},
        {"phase": "prepare", "arguments": [], "decisions": []},
    ).task
    tree = "OWNED-FAILED-TREE"
    execution_key = "OWNED-FAILED-EXECUTION"
    receipt = {
        "id": "OWNED-FAILED-RECEIPT",
        "method": "GUARD",
        "obligations": ["GUARD"],
        "passed": False,
        "timed_out": False,
        "actual_exit_code": 1,
        "tree": tree,
        "guard": True,
        "interpretable": True,
    }
    current = replace(
        current,
        evidence_book=current.evidence_book.record_submission_batch(
            current.stage.stage_id,
            current.state.iteration,
            tree,
            execution_key,
            current.state.submission_digest,
            [receipt],
        ),
    )
    before = current

    with pytest.raises(DomainError, match="другой сессией"):
        current.rework_failed(
            "FOREIGN",
            "A foreign actor must not recover the owned failed candidate.",
            tree,
            execution_key,
            "draft",
        )

    assert current == before


def test_exact_rework_retry_is_idempotent():
    current = pending_task()
    before = current
    errors = []

    for _ in range(2):
        with pytest.raises(DomainError) as caught:
            current.rework("S", "Повторить тот же возврат.", "amend")
        errors.append(str(caught.value))

    assert errors == [EXPECTED_ERROR, EXPECTED_ERROR]
    assert current == before


def test_mixed_history_reports_only_unresolved_resolution():
    route = process()
    route["stages"][2]["transitions"]["complete"] = "resolution_audit"
    route["stages"][3]["id"] = "resolution_audit"
    route["stages"][3]["rework_targets"] = ["resolution_audit", "draft"]

    current = verify(task_with_contracts(route), {}).accept("S", True).task
    current = verify(current, inspect([finding("F1")])).accept("S", True).task
    current = verify(
        current,
        {"resolutions": [resolution("R1", "F1")]},
    ).accept("S", True).task
    current = verify(
        current,
        inspect(
            [finding("F2"), finding("F3")],
            [decision("R1")],
        ),
    ).accept("S", True).task
    current = verify(
        current,
        {
            "resolutions": [
                resolution("R2", "F2"),
                resolution("R3", "F3"),
            ]
        },
    )
    before = current

    with pytest.raises(DomainError) as caught:
        current.rework("S", "Не обходить второй осмотр.", "amend")

    message = str(caught.value)
    assert "исправления R2, R3" in message
    assert "этап resolution_audit" in message
    assert "R1" not in message
    assert current == before
    context = current.workflow_context()["feedback"]
    assert context["decisions"][0]["resolution_id"] == "R1"
    assert context["decisions"][0]["decision"] == "accepted"


def test_clean_rework_remains_available():
    current = verify(task_with_contracts(), {})

    changed = current.rework("S", "Уточнить чистый результат.", "draft")

    assert changed.task.stage.stage_id == "draft"
    assert changed.task.state.iteration == 2
    assert changed.events[0].kind == "user_rework"


def test_rework_is_available_after_pending_resolution_is_inspected():
    current = pending_task().accept("S", True).task
    current = verify(current, inspect(decisions=[decision("R1")]))

    changed = current.rework("S", "Повторно проверить уже осмотренный результат.", "draft")

    assert changed.task.stage.stage_id == "draft"
    assert changed.task.state.iteration == 2
    assert changed.task.workflow_context()["feedback"]["pending_resolutions"] == []
