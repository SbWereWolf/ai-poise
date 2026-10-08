"""Historical corruption and visit selection in disposable owned Task storage."""

import json

import pytest

from conftest import git
from runtime_services.restart_auto_support import assert_projection, launch, prepared


def test_reworked_visit_uses_exact_accepted_result_identity(project):
    case = prepared(project, rework=True)
    assert [visit["visit_id"] for visit in case["visits"]] == [5, 6, 7]
    response = launch(case, target="code_review")
    assert response["status"] == "progression_target_reached"
    assert_projection(case, response)
    assert case["log"].read_text().splitlines() == case["commits"]


@pytest.mark.parametrize("damage,reason,checkout", [
    ("result", "history_unavailable", False),
    ("verified_event", "history_unavailable", False),
    ("ambiguous_visit", "history_unavailable", False),
    ("test_definition", "tests_missing", True),
    ("contradictory_receipt", "history_unavailable", False),
])
def test_incomplete_history_never_guesses_an_accepted_gate(project, damage, reason, checkout):
    case = prepared(project)
    first = case["visits"][0]["visit_id"]
    # Fault injection only into a disposable fixture DB; no managed installation writes.
    with case["client"].runtime.store.transaction() as database:
        if damage == "result":
            database.execute("DELETE FROM task_results WHERE task_id=? AND submission_id=?", ("T1", first))
        elif damage in ("verified_event", "ambiguous_visit"):
            row = database.execute(
                "SELECT seq,data FROM task_events WHERE task_id=? "
                "AND json_extract(data,'$.event')='verified' "
                "AND json_extract(data,'$.submission')=?", ("T1", first),
            ).fetchone()
            if damage == "verified_event":
                database.execute("DELETE FROM task_events WHERE seq=?", (row[0],))
            else:
                value = json.loads(row[1]); value["stage"] = "implementation"
                database.execute("UPDATE task_events SET data=? WHERE seq=?", (json.dumps(value), row[0]))
        else:
            value = json.loads(case["historical_results"][0][1])
            if damage == "test_definition":
                value["checks"][0]["method"] = "MISSING_REQUIRED_METHOD"
            else:
                value["checks"][0]["commit"] = case["commits"][2]
            database.execute("UPDATE task_results SET data=? WHERE task_id=? AND submission_id=?",
                             (json.dumps(value), "T1", first))
    response = launch(case, target="code_review")
    assert response["status"] == "progression_stopped"
    assert_projection(case, response, stage="tests", reason=reason, count=0,
                      subject=case["commits"][0] if checkout else None)
    assert case["log"].read_text() == ""
    assert git(case["root"], "rev-parse", "HEAD") == (case["commits"][0] if checkout else case["saved"])


def test_changed_route_stops_before_unsupported_historical_mapping(project):
    case = prepared(project, route_revision=True)
    response = launch(case, target="code_review")
    assert response["status"] == "progression_stopped"
    assert_projection(case, response, stage="tests", reason="route_unavailable", count=0)
    assert git(case["root"], "rev-parse", "HEAD") == case["saved"]
    assert case["log"].read_text() == ""


@pytest.mark.parametrize("publish,status,reason,stop", [
    (False, "progression_stopped", "task_acceptance_required", "code_review"),
    (True, "user_acceptance_required", "acceptance_required", "publish"),
])
def test_maximum_replay_respects_separate_final_authority(project, publish, status, reason, stop):
    case = prepared(project, finish=True, publish=publish)
    response = launch(case, maximum=True)
    assert response["status"] == status
    assert_projection(case, response, mode="maximum", target=None, stage=stop,
                      reason=reason, count=4, task_stage="code_review",
                      subject=None if publish else case["commits"][3])
    record = case["client"].runtime.task_queries.record("T1")
    assert record["status"] != "completed"
    assert not any(event["event"].startswith("user_accept") for event in record["history"])
    assert case["log"].read_text().splitlines() == case["commits"]
    assert git(case["root"], "rev-parse", "HEAD") == case["commits"][3]
    assert git(case["project"]["app"], "status", "--porcelain") == ""
