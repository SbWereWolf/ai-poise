"""A restart keeps historical work without presenting it as a new submission."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import bootstrap, request, result, verify
from conftest import WorkPoise, add_test
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_task_restart import restart
from runner.helpers import finding, inspect
from runner.test_runner_paths import edit, result as stage_result, setup_project


def restarted_task(project):
    runtime = WorkPoise(project["config_path"], "executor")
    tools = WorkTools(runtime)
    original = bootstrap(tools, project)
    add_test(original["worktree"])
    historical = verify(tools, result(original, "Historical result"))
    assert historical["status"] == "verified"
    task_id = original["task"]
    before = runtime.task_queries.record(task_id)
    restarted = restart(tools, task_id, before["version"])
    ready = tools.invoke(request("task", {
        "action": "ready",
        "request_id": "ready-restarted-handoff",
        "task_id": task_id,
        "expected_revision": restarted["revision"],
    }))
    assert ready["status"] == "available"
    resumed = tools.invoke(request("bootstrap", {
        "task": {"id": task_id},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    return runtime, tools, resumed


def handoff(tools, *, request_id="transfer-restarted", payload=None):
    return tools.invoke(request("handoff", {
        "request_id": request_id,
        "reason": "Another agent continues the restarted task.",
        "result": payload,
        "commit_message": None,
        "artifact_paths": [],
    }))


def submissions(runtime, task_id):
    with runtime.store.transaction() as database:
        return [tuple(row) for row in database.execute(
            "SELECT seq, stage, iteration, digest FROM submissions "
            "WHERE task_id=? ORDER BY seq", (task_id,),
        )]


def verified_results(runtime, task_id):
    with runtime.store.transaction() as database:
        return [row[0] for row in database.execute(
            "SELECT submission_id FROM task_results WHERE task_id=? ORDER BY submission_id",
            (task_id,),
        )]


def test_restart_retains_historical_result_without_making_it_current(project):
    runtime, tools, resumed = restarted_task(project)
    task_id = resumed["task"]
    historical = submissions(runtime, task_id)
    assert len(historical) == 1
    assert verified_results(runtime, task_id) == [historical[0][0]]
    assert resumed["result_template"]["sections"]["report"] != "Historical result"

    receipt = handoff(tools)

    assert receipt["status"] == "handed_off"
    assert runtime.task_queries.record(task_id)["claimed_by"] is None
    assert submissions(runtime, task_id) == historical
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    acquired = receiver.invoke(request("bootstrap", {
        "task": {"id": task_id},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert acquired["stage"] == resumed["stage"]
    assert acquired["iteration"] == resumed["iteration"]
    assert acquired["result_template"]["sections"]["report"] != "Historical result"
    assert submissions(runtime, task_id) == historical
    assert verified_results(runtime, task_id) == [historical[0][0]]


def test_null_handoff_does_not_resubmit_pre_restart_result(project):
    runtime, tools, resumed = restarted_task(project)
    task_id = resumed["task"]
    historical = submissions(runtime, task_id)

    receipt = handoff(tools)

    assert receipt["status"] == "handed_off"
    assert runtime.task_queries.record(task_id)["claimed_by"] is None
    assert submissions(runtime, task_id) == historical


def test_new_result_after_restart_is_transferred_without_replacing_history(project):
    runtime, tools, resumed = restarted_task(project)
    task_id = resumed["task"]
    historical = submissions(runtime, task_id)
    fresh = deepcopy(resumed["result_template"])
    fresh["sections"]["report"] = "New result after restart"

    receipt = handoff(tools, payload=fresh)

    assert receipt["status"] == "handed_off"
    assert submissions(runtime, task_id)[:1] == historical
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    acquired = receiver.invoke(request("bootstrap", {
        "task": {"id": task_id},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert acquired["result_template"]["sections"]["report"] == "New result after restart"


def test_replayed_transfer_has_no_new_effect_and_changed_replay_is_rejected(project):
    runtime, tools, resumed = restarted_task(project)
    first = handoff(tools)
    task_id = resumed["task"]
    after = runtime.task_queries.record(task_id)
    saved = submissions(runtime, task_id)

    assert handoff(tools) == {**first, "replayed": True}
    with pytest.raises(PoiseError, match="different content"):
        tools.invoke(request("handoff", {
            "request_id": "transfer-restarted",
            "reason": "Changed transfer content.",
            "result": None,
            "commit_message": None,
            "artifact_paths": [],
        }))
    assert runtime.task_queries.record(task_id) == after
    assert submissions(runtime, task_id) == saved


def test_missing_preserved_bundle_does_not_transfer_claim_to_receiver(project):
    runtime, tools, resumed = restarted_task(project)
    receipt = handoff(tools)
    task_id = resumed["task"]
    Path(receipt["bundle_path"]).unlink()
    before = runtime.task_queries.record(task_id)
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))

    with pytest.raises(PoiseError, match="bundle missing or changed"):
        receiver.invoke(request("bootstrap", {
            "task": {"id": task_id},
            "decision": None,
            "feedback": None,
            "rework_stage": None,
        }))
    assert runtime.task_queries.record(task_id) == before
    assert runtime.ownership.snapshot("receiver").task_id is None


def test_handoff_keeps_open_findings_for_the_receiving_agent(project):
    runtime = setup_project(project, "development")
    draft = runtime.bootstrap(task_file=project["task_path"])
    edit(draft, "development", "verified\n")
    stage_result(draft, {})
    assert runtime.verify()["status"] == "verified"
    runtime.accept()
    review = runtime.bootstrap(decision="continue")
    stage_result(review, inspect([finding("OPEN-1")]))
    assert runtime.verify()["stage_outcome"] == "changes_requested"
    correction = runtime.bootstrap(decision="continue")
    assert correction["stage"] == "amend"
    assert [item["id"] for item in correction["workflow"]["feedback"]["open_findings"]] == ["OPEN-1"]

    sender = WorkTools(runtime)
    assert handoff(sender, request_id="open-finding-transfer")["status"] == "handed_off"
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    acquired = receiver.invoke(request("bootstrap", {
        "task": {"id": correction["task"]},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert [item["id"] for item in acquired["workflow"]["feedback"]["open_findings"]] == ["OPEN-1"]
