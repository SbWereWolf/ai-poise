"""Real restarted readiness preserves origin and the latest revision authority."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from runtime_services.stage_criteria_helpers import boot, next_stage, verify_packet
from tasks.test_newborn_lifecycle import create, edit, tools as newborn_tools
from tasks.test_planning_flexibility import full_draft


def configured(project, *, initial_execution=True):
    project["task"]["planning"] = {
        "schema": "task-planning-1", "template": None,
        "restart_revision_policy": {
            "reviewer": ["workflow"], "user": ["workflow", "stage_contracts"],
        },
    }
    executor, context = boot(project, initial_execution=initial_execution)
    release(executor)
    reviewer = WorkTools(WorkPoise(project["config_path"], "refinement-reviewer"))
    return reviewer, context


def release(tools):
    current = tools.runtime.task_queries.record("T1")
    response = tools.invoke(request("release", {
        "task_id": "T1", "request_id": f"release-{current['version']}",
        "expected_version": current["version"],
        "reason": "Transfer the fixture Task to its independent reviewer.",
    }))
    assert response["status"] == "ownership_released"


def restart(tools, identifier, fields):
    current = tools.runtime.task_queries.record("T1")
    authority = {"role": "reviewer", "decision": "Fixture review permits these fields."}
    if fields is not None:
        authority["revision_fields"] = fields
    return tools.invoke(request("task", {
        "action": "restart", "task_id": "T1", "request_id": identifier,
        "expected_version": (current["revision"] if current["status"] == "newborn"
                             else current["version"]),
        "reason": "Refine this fixture Task without discarding its history.",
        "authorization": authority,
    }))


def ready(tools, newborn, identifier):
    return tools.invoke(request("task", {
        "action": "ready", "task_id": "T1", "request_id": identifier,
        "expected_revision": newborn["revision"],
    }))


def changed_criteria(tools, newborn, identifier, stage="build"):
    contracts = deepcopy(newborn["draft"]["stage_contracts"])
    target = next(item for item in contracts if item["stage_id"] == stage)
    target["criteria"]["requirements"] = ["Return three times the input."]
    edited = edit(tools, "T1", newborn["revision"],
                  {"stage_contracts": contracts}, identifier)
    return edited, target


def restart_context(tools):
    with tools.runtime.store.unit_of_work() as unit:
        return deepcopy(unit.tasks.restart_context("T1"))


def conserved_rows(tools):
    with tools.runtime.store.transaction() as connection:
        workflow = json.loads(connection.execute(
            "SELECT data FROM task_workflows WHERE task_id='T1'").fetchone()[0])
        return {
            "feedback": workflow["feedback"],
            "registry": workflow["registry"],
            "submissions": [tuple(row) for row in connection.execute(
                "SELECT seq,task_id,stage,iteration,digest,data FROM submissions "
                "WHERE task_id='T1' ORDER BY seq")],
            "results": [tuple(row) for row in connection.execute(
                "SELECT task_id,submission_id,data FROM task_results "
                "WHERE task_id='T1' ORDER BY submission_id")],
            "methods": [tuple(row) for row in connection.execute(
                "SELECT task_id,method_id,version,data FROM task_methods "
                "WHERE task_id='T1' ORDER BY method_id")],
            "events": [tuple(row) for row in connection.execute(
                "SELECT seq,task_id,version,at,data FROM task_events "
                "WHERE task_id='T1' ORDER BY seq")],
        }


def git_state(root):
    def output(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])
    return (output("rev-parse", "HEAD"), output("symbolic-ref", "HEAD"),
            output("write-tree"), output("status", "--porcelain=v1", "-uall"),
            (root / "refinement-wip.txt").read_bytes())


def assert_conserved(tools, root, before, rows, work):
    after = restart_context(tools)
    assert_audits_preserved(before["restart_history"], after["restart_history"])
    actual = conserved_rows(tools)
    assert actual["submissions"] == rows["submissions"]
    assert actual["results"] == rows["results"]
    assert actual["methods"] == rows["methods"]
    assert actual["feedback"] == rows["feedback"]
    assert actual["registry"] == rows["registry"]
    assert actual["events"][:len(rows["events"])] == rows["events"]
    assert git_state(root) == work
    return after["stage_criteria"]["stages"]["build"]


def assert_audits_preserved(before, after):
    assert len(after) == len(before)
    assert after[:-1] == before[:-1]
    assert {key: after[-1][key] for key in before[-1]} == before[-1]
    # Ready appends its documented resolution/context to the latest audit.
    assert set(after[-1]) - set(before[-1]) <= {
        "before_requirements_snapshot", "before_requirements_agreement", "resolved_revision",
    }


@pytest.mark.parametrize("refinements", [0, 1, 2])
@pytest.mark.parametrize("change", [False, True], ids=["unchanged", "changed"])
def test_ready_after_newborn_refinements(project, refinements, change):
    tools, context = configured(project)
    assert (context["stage"], context["iteration"]) == ("build", 1)
    root = Path(context["worktree"])
    (root / "refinement-wip.txt").write_bytes(b"Preserve unfinished fixture work.\n")
    newborn = restart(tools, "executed-origin", ["stage_contracts"])
    origin = deepcopy(restart_context(tools)["restart_history"][0])
    assert (origin["from_stage"], origin["from_iteration"]) == ("build", 1)
    for index in range(refinements):
        newborn = restart(tools, f"refinement-{index}", ["stage_contracts"])
        last = restart_context(tools)["restart_history"][-1]
        assert last["from_status"] == "newborn"
        assert "from_stage" not in last
        assert "from_iteration" not in last
    if change:
        newborn, expected = changed_criteria(tools, newborn, "change-criteria")
    before = restart_context(tools)
    original = deepcopy(before["stage_criteria"]["stages"]["build"])
    rows, work = conserved_rows(tools), git_state(root)

    response = ready(tools, newborn, "ready-refined")

    assert response["status"] == "available"
    actual = assert_conserved(tools, root, before, rows, work)
    if change:
        assert actual["criteria"] == expected["criteria"]
        assert actual["entry_requirements"] == expected["entry_requirements"]
        assert actual["exit_requirements"] == expected["exit_requirements"]
        assert actual["revision"] == original["revision"] + 1
        assert actual["history"][:-1] == original["history"]
        event = actual["history"][-1]
        assert (event["action"], event["stage"], event["iteration"]) == ("restart", "build", 1)
        assert (event["stage"], event["iteration"]) == (origin["from_stage"], origin["from_iteration"])
        assert event["reason"] == origin["reason"]
    else:
        assert actual == original


def test_newest_authority_and_executed_origin_are_both_preserved(project):
    tools, context = configured(project)
    root = Path(context["worktree"])
    (root / "refinement-wip.txt").write_bytes(b"Keep authority fixture work.\n")
    restart(tools, "workflow-only-origin", ["workflow"])
    original = deepcopy(restart_context(tools)["restart_history"][0])
    assert original["planning_revision"]["allowed_changes"] == ["workflow"]
    newborn = restart(tools, "criteria-only-refinement", ["stage_contracts"])
    newborn, expected = changed_criteria(tools, newborn, "use-newest-grant")
    before = restart_context(tools)
    assert before["restart_history"][-1]["planning_revision"]["allowed_changes"] == ["stage_contracts"]
    rows, work = conserved_rows(tools), git_state(root)

    assert ready(tools, newborn, "ready-newest-grant")["status"] == "available"

    actual = assert_conserved(tools, root, before, rows, work)
    assert restart_context(tools)["restart_history"][0] == original
    assert actual["criteria"] == expected["criteria"]
    assert (actual["history"][-1]["stage"], actual["history"][-1]["iteration"]) == ("build", 1)


def test_second_executed_restart_is_the_criteria_origin(project):
    tools, context = configured(project, initial_execution=False)
    assert (context["stage"], context["iteration"]) == ("sketch", 1)
    first = restart(tools, "first-executed-cycle", ["stage_contracts"])
    assert ready(tools, first, "ready-first-cycle")["status"] == "available"
    executor = WorkTools(WorkPoise(project["config_path"], "artisan-session"))
    context = executor.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert executor.invoke(verify_packet(context))["status"] == "verified"
    context = next_stage(executor)
    assert (context["stage"], context["iteration"]) == ("assess", 1)
    root = Path(context["worktree"])
    (root / "refinement-wip.txt").write_bytes(b"Keep the second cycle work.\n")
    release(executor)
    restart(tools, "second-executed-cycle", ["stage_contracts"])
    history = deepcopy(restart_context(tools)["restart_history"])
    assert (history[0]["from_stage"], history[0]["from_iteration"]) == ("sketch", 1)
    assert (history[1]["from_stage"], history[1]["from_iteration"]) == ("assess", 1)
    newborn = restart(tools, "second-cycle-refinement", ["stage_contracts"])
    newborn, expected = changed_criteria(tools, newborn, "second-cycle-criteria")
    before = restart_context(tools)
    rows, work = conserved_rows(tools), git_state(root)

    assert ready(tools, newborn, "ready-second-cycle")["status"] == "available"

    actual = assert_conserved(tools, root, before, rows, work)
    assert restart_context(tools)["restart_history"][:2] == history
    assert actual["criteria"] == expected["criteria"]
    event = actual["history"][-1]
    assert (event["stage"], event["iteration"]) == ("assess", 1)
    assert (event["stage"], event["iteration"]) == (history[1]["from_stage"], history[1]["from_iteration"])


def test_never_executed_newborn_keeps_initial_criteria_provenance(project):
    tools = newborn_tools(project, "newborn-refinement-fixture")
    created = create(tools, "T1")
    # The project fixture does not persist T1 until a public creation command.
    newborn = edit(tools, "T1", created["revision"], full_draft(project, "T1"), "initial-draft")
    newborn = restart(tools, "never-executed-refinement", None)
    newborn, expected = changed_criteria(
        tools, newborn, "initial-criteria-edit", stage="implementation")
    history = deepcopy(restart_context(tools)["restart_history"])

    assert ready(tools, newborn, "ready-never-executed")["status"] == "available"

    after = restart_context(tools)
    assert_audits_preserved(history, after["restart_history"])
    actual = after["stage_criteria"]["stages"]["implementation"]
    assert actual["criteria"] == expected["criteria"]
    assert actual["source"]["kind"] == "initial"
    assert not any(item["action"] == "restart" for item in actual["history"])
