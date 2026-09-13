from pathlib import Path
from dataclasses import replace

import pytest

from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.tasks.domain import Change, TaskEvent, TaskStatus
from sprints.helpers import (
    bootstrap as sprint_bootstrap,
    draft,
    publish,
    setup as sprint_setup,
    task as sprint_task,
    verify as sprint_verify,
)

from .helpers import request


ROOT = Path(__file__).resolve().parents[2]
TERMINAL_STATES = ("completed", "cancelled", "superseded")


def _standalone_contract(project, identifier):
    contract = sprint_task(project, identifier)
    contract["sprint_id"] = None
    return contract


def _create_terminal_task(project, state):
    sprint_setup(project)
    owner = WorkTools(Poise(project["config_path"], f"owner-{state}"))
    identifier = f"terminal-{state}"

    if state == "superseded":
        source = sprint_task(project, identifier)
        planned = draft(owner, [source])
        publish(owner, planned["revision"])
        # Historical fixture from the retired pre-0079 replacement writer.
        with owner.runtime.store.unit_of_work() as unit:
            task = unit.tasks.load(identifier)
            changed = replace(
                task,
                state=replace(
                    task.state,
                    status=TaskStatus.SUPERSEDED,
                    version=task.state.version + 1,
                ),
            )
            unit.tasks.save(Change(changed, None, (TaskEvent(
                "superseded",
                task.stage.stage_id,
                task.state.iteration,
                "Historical replacement fixture.",
            ),)), task.state.version)
    else:
        context = owner.invoke(
            request(
                "bootstrap",
                {
                    "task": _standalone_contract(project, identifier),
                    "decision": None,
                    "feedback": None,
                    "rework_stage": None,
                },
            )
        )
        if state == "completed":
            assert sprint_verify(owner, context)["status"] == "verified"
            assert owner.invoke(request("accept", {}))["status"] == "completed"
        else:
            assert owner.invoke(
                request("cancel", {"reason": "The fixture records a cancelled terminal Task."})
            )["status"] == "cancelled"

    record = owner.runtime.task_queries.record(identifier)
    assert record["status"] == state
    snapshot = {
        "content": owner.runtime.task_queries.content(identifier),
        "evidence": owner.runtime.task_queries.evidence_view(identifier),
        "history": owner.runtime.task_queries.history(identifier),
    }
    return identifier, snapshot


@pytest.mark.parametrize("terminal_state", TERMINAL_STATES)
def test_terminal_task_inspection_is_non_binding_and_taskless_verify_succeeds(
    project, terminal_state
):
    identifier, snapshot = _create_terminal_task(project, terminal_state)
    reader = WorkTools(Poise(project["config_path"], f"reader-{terminal_state}"))

    inspected = sprint_bootstrap(reader, identifier)

    assert inspected["status"] == terminal_state
    assert inspected["result_template"] is None
    assert reader.runtime.current_task() is None
    assert inspected["content"] == snapshot["content"]
    assert inspected["evidence"] == snapshot["evidence"]
    assert inspected["history"] == snapshot["history"]
    assert reader.runtime.task_queries.content(identifier) == snapshot["content"]
    assert reader.runtime.task_queries.evidence_view(identifier) == snapshot["evidence"]
    assert reader.runtime.task_queries.history(identifier) == snapshot["history"]

    taskless = sprint_bootstrap(reader)
    assert taskless["status"] == "read_only"
    finalized = reader.invoke(request("verify", {"result": None, "artifacts": []}))
    assert finalized["status"] == "read_only_verified"


def test_active_task_ownership_is_not_weakened(project):
    sprint_setup(project)
    terminal_owner = WorkTools(Poise(project["config_path"], "terminal-owner"))
    terminal = _standalone_contract(project, "terminal-task")
    terminal_owner.invoke(
        request(
            "bootstrap",
            {
                "task": terminal,
                "decision": None,
                "feedback": None,
                "rework_stage": None,
            },
        )
    )
    terminal_owner.invoke(request("cancel", {"reason": "Prepare a terminal target."}))

    active_owner = WorkTools(Poise(project["config_path"], "active-owner"))
    active = _standalone_contract(project, "active-task")
    active_owner.invoke(
        request(
            "bootstrap",
            {
                "task": active,
                "decision": None,
                "feedback": None,
                "rework_stage": None,
            },
        )
    )

    with pytest.raises(PoiseError, match="прекратить|передать"):
        sprint_bootstrap(active_owner, "terminal-task")

    assert active_owner.runtime.current_task()["id"] == "active-task"
    assert active_owner.runtime.current_task()["status"] == "active"


def test_terminal_inspection_documentation_contract():
    required = {
        ROOT / "docs" / "workflows" / "batch-work.md": (
            "terminal inspection snapshot",
            "stale current-task binding",
            "read_only_verified",
        ),
        ROOT / "docs" / "architecture" / "boundaries.md": (
            "terminal inspection snapshot",
            "installation source",
        ),
        ROOT / "AGENTS.md": (
            "terminal inspection snapshot",
            "must not bind",
        ),
        ROOT / ".agents" / "skills" / "poise" / "SKILL.md": (
            "terminal inspection snapshot",
            "read_only_verified",
        ),
    }

    for path, terms in required.items():
        text = path.read_text(encoding="utf-8")
        assert all(term in text for term in terms), (path, terms)
