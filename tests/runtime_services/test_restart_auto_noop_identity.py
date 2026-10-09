"""Saved no-op identity is durable while actual current state stays truthful."""

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.restart_auto_noop_support import (
    assert_noop_response, assert_single_noop, noop_snapshot,
)
from runtime_services.restart_auto_support import assert_projection, bootstrap, launch, prepared
from runtime_services.test_task_restart import restart


def initial_noop(case):
    before = noop_snapshot(case)
    response = launch(case, target="tests", request_id="N")
    assert_noop_response(response, case["saved"])
    assert_single_noop(before, noop_snapshot(case), commit=case["saved"])


def fresh_runtime(case):
    case["client"] = WorkTools(WorkPoise(
        case["project"]["config_path"], case["client"].runtime.session,
    ))


def advance_m(case):
    response = launch(case, target="implementation", request_id="M")
    assert_projection(case, response, target="implementation", stage="implementation",
                      count=2, subject=case["saved"])
    assert case["log"].read_text().splitlines() == [case["saved"]] * 2


def test_saved_noop_retry_after_runtime_reinstantiation_has_no_effect(project):
    case = prepared(project)
    initial_noop(case)
    fresh_runtime(case)
    before = noop_snapshot(case)
    assert_noop_response(launch(case, target="tests", request_id="N"), case["saved"])
    assert noop_snapshot(case) == before


def test_saved_noop_after_new_intent_keeps_original_result_and_actual_state(project):
    case = prepared(project)
    initial_noop(case)
    advance_m(case)
    fresh_runtime(case)
    before = noop_snapshot(case)
    response = launch(case, target="tests", request_id="N")
    assert_noop_response(response, case["saved"])
    assert response["stage"] == "implementation"
    assert noop_snapshot(case) == before
    assert git(case["root"], "rev-parse", "HEAD") == case["saved"]


@pytest.mark.parametrize("after_m", [False, True])
@pytest.mark.parametrize("changed", ["target", "mode"])
def test_saved_noop_changed_intent_rejects_before_current_route(project, after_m, changed):
    case = prepared(project)
    initial_noop(case)
    if after_m:
        advance_m(case)
    fresh_runtime(case)
    arguments = {"request_id": "N", "task_id": "T1"}
    if changed == "target":
        arguments["target_stage"] = "implementation"
    before = noop_snapshot(case)
    with pytest.raises(PoiseError, match="[Cc]onflict"):
        case["client"].invoke(request("advance", arguments))
    assert noop_snapshot(case) == before


def test_saved_noop_survives_public_task_restart_and_changed_work_commit(project):
    case = prepared(project)
    initial_noop(case)
    (case["root"] / "src" / "saved.txt").write_text("second preserved task work\n")
    git(case["root"], "add", "src/saved.txt")
    git(case["root"], "commit", "-m", "Preserve second task work")
    cw2 = git(case["root"], "rev-parse", "HEAD")
    assert cw2 != case["saved"]
    current = case["client"].runtime.task_queries.record("T1")
    born = restart(case["client"], "T1", current["version"], request_id="second-restart")
    ready = case["client"].invoke(request("task", {
        "action": "ready", "task_id": "T1", "request_id": "second-ready",
        "expected_revision": born["revision"],
    }))
    assert ready["ready"] is True
    bootstrap(case["client"], {"id": "T1"})
    fresh_runtime(case)
    before = noop_snapshot(case)
    assert_noop_response(launch(case, target="tests", request_id="N"), case["saved"])
    assert noop_snapshot(case) == before
    assert git(case["root"], "rev-parse", "HEAD") == cw2


@pytest.mark.parametrize("guard", ["foreign", "completed", "cancelled", "pending"])
def test_saved_noop_cannot_bypass_current_admission(project, guard):
    case = prepared(project)
    initial_noop(case)
    caller = case["client"]
    if guard == "foreign":
        caller = WorkTools(WorkPoise(project["config_path"], "foreign-actor"))
    elif guard == "pending":
        with caller.runtime.store.unit_of_work() as unit:
            unit.execution.patch("T1", {"pending": "unknown-test-owned-effect"})
    else:
        # Terminal corruption arrangement belongs only to this disposable DB.
        with caller.runtime.store.transaction() as db:
            db.execute("UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?", (guard, "T1"))
            db.execute("UPDATE sessions SET task_id=NULL WHERE task_id=?", ("T1",))
    before = noop_snapshot(case)
    with pytest.raises(PoiseError):
        caller.invoke(request("advance", {
            "request_id": "N", "task_id": "T1", "target_stage": "tests",
        }))
    assert noop_snapshot(case) == before
    if guard == "foreign":
        assert caller.runtime.ownership.snapshot("foreign-actor").task_id is None

