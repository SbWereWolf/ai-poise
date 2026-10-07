"""Public maximum and explicit-target replay contracts, with literal oracles."""

import pytest

from conftest import WorkPoise, git
from poise.application.work import WorkTools
from runtime_services.restart_auto_support import (
    assert_projection, assert_recovery, launch, prepared, snapshot,
)


def test_default_mode_replays_accepted_stages_to_first_new_work(project):
    case = prepared(project)
    response = launch(case, maximum=True)
    assert response["status"] == "progression_work_required"
    assert response["stage"] == "code_review"
    replay = response["replay"]
    assert replay["mode"] == "maximum"
    assert replay["target_stage"] is None
    assert replay["reason"] == "work_required"
    assert replay["stopped_at"] == "code_review"
    assert replay["subject_commit"] is None
    assert [entry["stage"] for entry in replay["passed"]] == [
        "tests", "test_review", "implementation",
    ]
    assert [entry["commit"] for entry in replay["passed"]] == case["commits"]
    assert case["log"].read_text().splitlines() == case["commits"]
    assert git(case["root"], "rev-parse", "HEAD") == case["commits"][2]
    assert_recovery(case, response)
    assert_projection(case, response, mode="maximum", target=None,
                      reason="work_required")


@pytest.mark.parametrize("target,index", [("test_review", 1), ("implementation", 2)])
def test_explicit_target_stops_before_its_checks(project, target, index):
    case = prepared(project)
    response = launch(case, target=target)
    assert response["status"] == "progression_target_reached"
    assert response["stage"] == target
    replay = response["replay"]
    assert replay["mode"] == "target"
    assert replay["reason"] == "target_reached"
    assert replay["subject_commit"] == case["commits"][index]
    assert case["log"].read_text().splitlines() == case["commits"][:index]
    assert git(case["root"], "rev-parse", "HEAD") == case["commits"][index]
    assert_recovery(case, response)
    assert_projection(case, response, target=target, stage=target, count=index,
                      subject=case["commits"][index])


def test_already_current_target_is_a_complete_no_op(project):
    case = prepared(project)
    before = snapshot(case)
    response = launch(case, target="tests")
    assert response["status"] == "progression_target_reached"
    replay = response["replay"]
    assert replay["mode"] == "target"
    assert replay["target_stage"] == "tests"
    assert replay["recovery_ref"] is None
    assert replay["start_commit"] == case["saved"]
    assert replay["subject_commit"] is None
    assert replay["stopped_at"] == "tests"
    assert replay["reason"] == "target_reached"
    assert replay["passed"] == []
    after = snapshot(case)
    assert after == before
    assert_projection(case, response, target="tests", stage="tests", count=0, noop=True)


def test_exact_target_retry_preserves_projection_and_check_effects(project):
    case = prepared(project)
    first = launch(case, target="implementation")
    before = snapshot(case)
    case["client"] = WorkTools(WorkPoise(project["config_path"], case["client"].runtime.session))
    repeated = launch(case, target="implementation")
    assert repeated["replay"] == first["replay"]
    assert snapshot(case) == before
