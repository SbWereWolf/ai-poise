from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest

from conftest import write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from poise.runtime import Poise
from batch.helpers import request
from transfer.helpers import destination, export, restore
from .helpers import bootstrap, draft, publish, setup, task, verify


REASON = "Исправить ошибочный неизменяемый метод"
AUTHORIZATION = "Пользователь поручил заменить незавершённую задачу"


@pytest.fixture
def sprint(project):
    setup(project)
    runtime = Poise(project["config_path"], "planner")
    return project, runtime, WorkTools(runtime)


def replacement(project, identifier="BAD-2", command='print("fixed")'):
    return task(project, identifier, command=command)


def replace(
    tools,
    revision,
    candidate,
    *,
    source="BAD",
    request_id="replace-BAD",
    sprint_id=None,
):
    return tools.invoke(
        request(
            "sprint",
            {
                "action": "replace_task",
                "sprint_id": sprint_id,
                "request_id": request_id,
                "expected_revision": revision,
                "source_task": source,
                "replacement": candidate,
                "reason": REASON,
                "authorization": AUTHORIZATION,
            },
        )
    )


def sprint_view(tools, view):
    result = tools.invoke(
        request(
            "show",
            {
                "queries": [
                    {"id": "sprint", "kind": "sprint", "sprint_id": "S", "view": view}
                ]
            },
        )
    )
    return result["results"][0]["value"]


def published_graph(sprint, edges=()):
    project, _, tools = sprint
    contracts = [task(project, name) for name in ("PRE", "BAD", "POST")]
    planned = draft(tools, contracts, edges)
    return publish(tools, planned["revision"])


def relation(result):
    return next(item for item in result["replacements"] if item["source"] == "BAD")


def test_replacement_end_to_end_redirects_graph_and_preserves_old_evidence(sprint):
    project, runtime, tools = sprint
    bad = task(project, "BAD", command="raise SystemExit(9)")
    planned = draft(
        tools,
        [task(project, "PRE"), bad, task(project, "POST")],
        [
            {"predecessor": "PRE", "successor": "BAD", "kind": "completion"},
            {"predecessor": "BAD", "successor": "POST", "kind": "result"},
        ],
    )
    current = publish(tools, planned["revision"])
    predecessor = bootstrap(tools, "PRE")
    assert verify(tools, predecessor, "The predecessor is complete.")["status"] == "verified"
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    context = bootstrap(tools, "BAD")
    made = tools.invoke(
        request(
            "artifacts",
            {
                "items": [
                    {
                        "scope": "task",
                        "path": "immutable-failure.txt",
                        "source": {
                            "kind": "text",
                            "text": "Evidence owned by the superseded Task",
                        },
                    }
                ]
            },
        )
    )
    failed = verify(tools, context, "The immutable method is erroneous.")
    assert failed["status"] == "checks_failed"
    evidence_before = runtime.task_queries.evidence_view("BAD")
    history_before = runtime.task_queries.history("BAD")
    submission_before = runtime.task_queries.latest_submission("BAD", "work", 1)
    artifacts_before = deepcopy(runtime.store.artifact_records("BAD"))
    contract_before = runtime.task_queries.record("BAD")["contract"]

    outcome = replace(tools, current["revision"], replacement(project))

    assert outcome["revision"] == current["revision"] + 1
    assert relation(outcome) | {
        "source": "BAD",
        "replacement": "BAD-2",
        "reason": REASON,
        "authorization": AUTHORIZATION,
        "from_revision": current["revision"],
        "to_revision": current["revision"] + 1,
    } == relation(outcome)
    plan = sprint_view(tools, "plan")["aggregate"]["plan"]
    assert {item["id"] for item in plan["tasks"]} == {"PRE", "BAD-2", "POST"}
    assert plan["dependencies"] == [
        {"predecessor": "BAD-2", "successor": "POST", "kind": "result"},
        {"predecessor": "PRE", "successor": "BAD-2", "kind": "completion"},
    ]
    assert runtime.task_queries.record("BAD")["status"] == "superseded"
    assert runtime.task_queries.record("BAD")["contract"] == contract_before
    assert runtime.task_queries.evidence_view("BAD") == evidence_before
    assert runtime.task_queries.history("BAD")[:-1] == history_before
    assert runtime.task_queries.latest_submission("BAD", "work", 1) == submission_before
    assert runtime.store.artifact_records("BAD") == artifacts_before
    assert Path(made["artifact_paths"][0]).read_text() == "Evidence owned by the superseded Task"
    assert runtime.task_queries.record("BAD-2")["status"] == "available"
    assert runtime.task_queries.record("BAD-2")["worktree"] is None

    reader = WorkTools(Poise(project["config_path"], "old-task-reader"))
    old = bootstrap(reader, "BAD")
    assert old["status"] == "superseded" and old["result_template"] is None
    shown = reader.invoke(request("show", {"queries": [{"id": "old", "kind": "evidence"}]}))
    assert shown["results"][0]["value"] == evidence_before
    assert reader.runtime.task_queries.latest_submission("BAD", "work", 1) == submission_before
    assert reader.runtime.store.artifact_records("BAD") == artifacts_before


def test_replacement_redirects_waiver_and_current_projection(sprint):
    project, runtime, tools = sprint
    current = published_graph(
        sprint,
        [{"predecessor": "BAD", "successor": "POST", "kind": "completion"}],
    )
    waived = tools.invoke(
        request(
            "sprint",
            {
                "action": "waive_dependencies",
                "sprint_id": None,
                "request_id": "waive-BAD",
                "decisions": [
                    {
                        "predecessor": "BAD",
                        "successor": "POST",
                        "reason": "Продолжить без результата BAD",
                    }
                ],
            },
        )
    )
    outcome = replace(tools, waived["revision"], replacement(project))
    plan_record = sprint_view(tools, "plan")["aggregate"]
    assert plan_record["waivers"] == [
        {
            "predecessor": "BAD-2",
            "successor": "POST",
            "reason": "Продолжить без результата BAD",
        }
    ]
    assert {item["id"] for item in outcome["tasks"]} == {"PRE", "BAD-2", "POST"}
    assert "BAD" not in outcome["eligible"]
    assert runtime.task_queries.record("BAD")["status"] == "superseded"


def test_replacement_rejects_invalid_candidates_without_mutation(sprint):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    before = sprint_view(tools, "plan")

    with pytest.raises(PoiseError, match="revision|Revision"):
        replace(tools, current["revision"] - 1, replacement(project), request_id="stale")
    collision = replacement(project, "PRE")
    with pytest.raises(PoiseError, match="ID|exists|collision"):
        replace(tools, current["revision"], collision, request_id="collision")
    wrong_sprint = replacement(project)
    wrong_sprint["sprint_id"] = "OTHER"
    with pytest.raises(PoiseError, match="Sprint|sprint"):
        replace(tools, current["revision"], wrong_sprint, request_id="wrong-sprint")
    invalid_method = replacement(project)
    invalid_method["checks"]["work"] = ["MISSING"]
    with pytest.raises(PoiseError, match="MISSING|method"):
        replace(tools, current["revision"], invalid_method, request_id="invalid-method")
    incomplete = replacement(project)
    del incomplete["definition_of_done"]
    with pytest.raises(PoiseError, match="task|field|keys|набор полей"):
        replace(tools, current["revision"], incomplete, request_id="incomplete-contract")

    assert sprint_view(tools, "plan") == before
    assert runtime.task_queries.record("BAD")["status"] == "available"
    assert runtime.task_queries.record("BAD-2") is None


def test_replacement_uses_published_process_snapshot_after_live_config_changes(sprint):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    saved_process = deepcopy(runtime.task_queries.record("BAD")["process"])
    live_process = deepcopy(project["process"])
    live_process["stages"][0]["id"] = "changed-work"
    live_process["stages"][0]["rework_targets"] = ["changed-work"]
    live_process["route"]["entry"] = "changed-work"
    write_json(project["root"] / "config/processes/development.json", live_process)
    fresh_runtime = Poise(project["config_path"], "fresh-planner")
    fresh_tools = WorkTools(fresh_runtime)

    outcome = replace(
        fresh_tools,
        current["revision"],
        replacement(project),
        sprint_id="S",
    )

    assert outcome["revision"] == current["revision"] + 1
    assert fresh_runtime.task_queries.record("BAD-2")["process"] == saved_process


def test_replacement_rejects_started_successor_without_graph_change(sprint):
    project, runtime, tools = sprint
    current = published_graph(
        sprint,
        [{"predecessor": "BAD", "successor": "POST", "kind": "completion"}],
    )
    waived = tools.invoke(
        request(
            "sprint",
            {
                "action": "waive_dependencies",
                "sprint_id": None,
                "request_id": "start-POST",
                "decisions": [
                    {"predecessor": "BAD", "successor": "POST", "reason": "Начать осмотр"}
                ],
            },
        )
    )
    bootstrap(tools, "POST")
    before = sprint_view(tools, "plan")
    with pytest.raises(PoiseError, match="started|prerequisite|начат"):
        replace(tools, waived["revision"], replacement(project))
    assert sprint_view(tools, "plan") == before
    assert runtime.task_queries.record("BAD-2") is None


def test_replacement_allows_caller_owned_clean_worktree_and_retains_it(sprint):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    context = bootstrap(tools, "BAD")
    worktree = Path(context["worktree"])

    outcome = replace(tools, current["revision"], replacement(project))

    safety = relation(outcome)["safety"]
    assert safety["kind"] == "caller_owned_clean"
    assert safety["worktree"] == str(worktree)
    assert safety["tree"]
    assert worktree.is_dir()
    assert runtime.task_queries.record("BAD")["claimed_by"] is None


def test_replacement_rejects_dirty_foreign_and_pending_work_with_recovery(sprint):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    context = bootstrap(tools, "BAD")
    Path(context["worktree"], "src/double.py").write_text("dirty replacement candidate\n")
    with pytest.raises(
        PoiseError,
        match=r"(?i)(?=.*(?:dirty|WIP|измен))(?=.*(?:handoff|передач))",
    ):
        replace(tools, current["revision"], replacement(project), request_id="dirty")
    assert runtime.task_queries.record("BAD")["status"] == "active"

    # Restore through a fresh isolated fixture in the two remaining state cases.


def test_replacement_rejects_foreign_owner_and_pending_operation(sprint):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    other = WorkTools(Poise(project["config_path"], "worker"))
    bootstrap(other, "BAD")
    with pytest.raises(
        PoiseError,
        match=r"(?i)(?=.*(?:owner|owned|session|владел))(?=.*(?:handoff|передач))",
    ):
        replace(
            tools,
            current["revision"],
            replacement(project),
            request_id="foreign",
            sprint_id="S",
        )
    data = other.runtime.current_task()
    data["pending"] = "checks"
    other.runtime.store.save(data)
    with pytest.raises(
        PoiseError,
        match=r"(?i)(?=.*(?:pending|ожида))(?=.*(?:outcome|resolve|заверш|устран))",
    ):
        replace(
            other,
            current["revision"],
            replacement(project),
            request_id="pending",
            sprint_id="S",
        )
    assert runtime.task_queries.record("BAD-2") is None


@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
def test_replacement_rejects_terminal_source_tasks(sprint, terminal):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    revision = current["revision"]
    if terminal == "completed":
        context = bootstrap(tools, "BAD")
        assert verify(tools, context)["status"] == "verified"
        assert tools.invoke(request("accept", {}))["status"] == "completed"
    else:
        cancelled = tools.invoke(
            request(
                "sprint",
                {
                    "action": "cancel_tasks",
                    "sprint_id": "S",
                    "request_id": "cancel-BAD",
                    "tasks": ["BAD"],
                    "mode": "single",
                    "reason": "The erroneous Task will not be executed",
                },
            )
        )
        revision = cancelled["revision"]

    with pytest.raises(PoiseError, match="unfinished|completed|cancelled|заверш|отмен"):
        replace(
            tools,
            revision,
            replacement(project),
            sprint_id="S",
        )

    assert runtime.task_queries.record("BAD")["status"] == terminal
    assert runtime.task_queries.record("BAD-2") is None


def test_replacement_current_projection_redirects_blockers_and_eligibility(sprint):
    project, _, tools = sprint
    current = published_graph(
        sprint,
        [
            {"predecessor": "PRE", "successor": "BAD", "kind": "completion"},
            {"predecessor": "BAD", "successor": "POST", "kind": "result"},
        ],
    )

    outcome = replace(tools, current["revision"], replacement(project))

    assert outcome["eligible"] == ["PRE"]
    assert outcome["blocked"] == [
        {
            "task": "BAD-2",
            "reasons": [{"predecessor": "PRE", "reason": "predecessor_incomplete"}],
        },
        {
            "task": "POST",
            "reasons": [{"predecessor": "BAD-2", "reason": "predecessor_incomplete"}],
        },
    ]


def test_replacement_accepts_released_handoff_and_preserves_receipt(sprint):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    bootstrap(tools, "BAD")
    handed = tools.invoke(
        request(
            "handoff",
            {
                "request_id": "preserve-BAD",
                "reason": "Preserve before replacement",
                "result": None,
                "commit_message": None,
                "artifact_paths": [],
            },
        )
    )
    saved = deepcopy(runtime.handoff_tools.commands.latest("BAD"))

    outcome = replace(
        tools,
        current["revision"],
        replacement(project),
        sprint_id="S",
    )

    assert relation(outcome)["safety"]["kind"] == "released_handoff"
    assert relation(outcome)["safety"]["handoff_request"] == "preserve-BAD"
    assert runtime.handoff_tools.commands.latest("BAD") == saved
    assert Path(handed["receipt_path"]).is_file()
    assert Path(handed["bundle_path"]).is_file()


def test_replacement_request_replay_is_original_and_conflict_rejected(sprint):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    packet_candidate = replacement(project)
    first = replace(tools, current["revision"], packet_candidate)
    layers = len(sprint_view(tools, "history")["layers"])

    replay = replace(tools, current["revision"], packet_candidate)
    assert replay["revision"] == first["revision"]
    assert replay["replacements"] == first["replacements"]
    assert len(sprint_view(tools, "history")["layers"]) == layers
    assert len([item for item in runtime.task_queries.summary() if item["id"] == "BAD-2"]) == 1

    changed = replacement(project, command='print("different")')
    with pytest.raises(PoiseError, match="request|intent|ID"):
        replace(tools, current["revision"], changed)


@pytest.mark.parametrize("failure", ["source", "dependency"])
def test_replacement_transaction_failure_rolls_back_and_retry_succeeds(sprint, failure):
    project, runtime, tools = sprint
    current = published_graph(
        sprint,
        [{"predecessor": "BAD", "successor": "POST", "kind": "completion"}],
    )
    trigger = "fail_replacement"
    statement = (
        "CREATE TRIGGER fail_replacement BEFORE UPDATE ON tasks "
        "WHEN OLD.id='BAD' BEGIN SELECT RAISE(ABORT,'injected source failure'); END"
        if failure == "source"
        else "CREATE TRIGGER fail_replacement BEFORE INSERT ON sprint_dependencies "
        "WHEN NEW.predecessor='BAD-2' BEGIN SELECT RAISE(ABORT,'injected dependency failure'); END"
    )
    before = sprint_view(tools, "plan")
    with runtime.store.transaction() as db:
        db.execute(statement)
    with pytest.raises(sqlite3.DatabaseError):
        replace(tools, current["revision"], replacement(project))
    assert sprint_view(tools, "plan") == before
    assert runtime.task_queries.record("BAD")["status"] == "available"
    assert runtime.task_queries.record("BAD-2") is None
    with runtime.store.transaction() as db:
        db.execute(f"DROP TRIGGER {trigger}")
    assert replace(tools, current["revision"], replacement(project))["revision"] == current["revision"] + 1


def test_replaced_sprint_transfer_round_trip_preserves_relation_and_tasks(sprint, tmp_path):
    project, runtime, tools = sprint
    current = published_graph(sprint)
    outcome = replace(tools, current["revision"], replacement(project))
    saved = export(tools, sprint="S", request_id="export-replaced")
    target = destination(project, tmp_path / "destination")
    receiver = WorkTools(Poise(target["config_path"], "receiver"))

    receipt = restore(receiver, saved["package_path"], saved["package_digest"])

    assert receipt["sprint_ids"] == ["S"]
    imported = receiver.invoke(
        request(
            "show",
            {"queries": [{"id": "s", "kind": "sprint", "sprint_id": "S", "view": "current"}]},
        )
    )["results"][0]["value"]
    assert imported["replacements"] == outcome["replacements"]
    assert receiver.runtime.task_queries.record("BAD")["status"] == "superseded"
    assert receiver.runtime.task_queries.record("BAD-2")["status"] == "available"
