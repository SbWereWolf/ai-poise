from __future__ import annotations

from copy import deepcopy

import pytest

from poise.common import PoiseError
from poise.modules.foundation.errors import DomainError
from runner.helpers import decision, finding, inspect, resolution, task, verify
from runner.test_runner_paths import edit, result, setup_project


EXPECTED_ERROR = (
    "Rework недоступен: исправления R1 ещё не осмотрены. "
    "Продолжите задачу на этап follow_up и рассмотрите каждое исправление."
)


def pending_runtime(project):
    runtime = setup_project(project, "development")
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


def pending_task():
    current = verify(task(), {}).accept("S", True).task
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
    current = pending_task().accept("S", True).task
    current = verify(
        current,
        inspect([finding("F2")], [decision("R1")]),
    ).accept("S", True).task
    current = verify(current, {"resolutions": [resolution("R2", "F2")]})

    with pytest.raises(DomainError) as caught:
        current.rework("S", "Не обходить второй осмотр.", "amend")

    message = str(caught.value)
    assert "R2" in message
    assert "R1" not in message
    context = current.workflow_context()["feedback"]
    assert context["decisions"][0]["resolution_id"] == "R1"
    assert context["decisions"][0]["decision"] == "accepted"


def test_clean_rework_remains_available():
    current = verify(task(), {})

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
