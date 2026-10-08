"""Actual durable owner boundaries and independently persisted replay effects."""

import json
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.restart_auto_support import (
    assert_checkout, assert_effect_counts, assert_projection, launch, owned_rows,
    prepared, replay_effects, snapshot,
)


@pytest.mark.parametrize("boundary", ["before_checkout", "receipt", "assessment", "transition"])
def test_each_confirmed_boundary_resumes_once(project, monkeypatch, boundary):
    case = prepared(project)
    baseline = owned_rows(case)
    runtime = case["client"].runtime
    fired = []
    confirmed = []
    if boundary == "before_checkout":
        owner, name = runtime, "_git"
    elif boundary == "receipt":
        owner, name = runtime.evidence_commands, "record_receipt"
    elif boundary == "assessment":
        owner, name = runtime.task_commands, "assess_evidence"
    else:
        owner, name = runtime.task_commands, "advance_progression"
    original = getattr(owner, name)

    def interrupted(*args, **kwargs):
        if boundary == "before_checkout":
            rewind = (Path(args[0]) == case["root"] and
                      any(value in ("reset", "checkout", "switch") for value in args[1:]))
            if rewind and not fired:
                fired.append(True)
                raise PoiseError("test-owned durable boundary interruption")
        answer = original(*args, **kwargs)
        if boundary != "before_checkout" and not fired:
            # The application API returns after its unit-of-work transaction commits.
            if boundary == "receipt":
                confirmed.append(args[-1]["id"])
            elif boundary == "transition":
                record = runtime.task_queries.record("T1")
                if record["stage_index"] == 0:
                    return answer
                confirmed.extend(owned_rows(case)["task_events"])
            fired.append(True)
            raise PoiseError("test-owned durable boundary interruption")
        return answer

    monkeypatch.setattr(owner, name, interrupted)
    with pytest.raises(PoiseError, match="test-owned durable boundary"):
        case["client"].invoke(request("advance", {
            "request_id": "replay", "task_id": "T1", "target_stage": "code_review",
        }))
    assert fired == [True]
    assert git(case["root"], "rev-parse", "HEAD") == (
        case["saved"] if boundary == "before_checkout" else case["commits"][0]
    )
    assert case["log"].read_text().splitlines() == (
        [] if boundary == "before_checkout" else case["commits"][:1]
    )
    assert_checkout(case, case["saved"] if boundary == "before_checkout" else case["commits"][0])
    interrupted_effects = replay_effects(case, baseline)
    expected = {
        "before_checkout": (0, 0, 0, 0), "receipt": (1, 1, 0, 0),
        "assessment": (1, 1, 1, 0), "transition": (1, 1, 1, 1),
    }
    assert_effect_counts(interrupted_effects, expected[boundary])
    if boundary == "assessment":
        with runtime.store.transaction() as database:
            proof = json.loads(database.execute(
                "SELECT data FROM task_proofs WHERE task_id=?", ("T1",),
            ).fetchone()[0])
        assessment = json.loads(proof["assessment"])
        assert assessment["ready"] is True
        assert assessment["tree"] == case["checkouts"][case["commits"][0]]["tree"]
        assert assessment["execution_key"]
    monkeypatch.setattr(owner, name, original)
    response = launch(case, target="code_review")
    assert_projection(case, response)
    assert case["log"].read_text().splitlines() == case["commits"]
    rows = owned_rows(case)
    resumed_effects = replay_effects(case, baseline)
    assert_effect_counts(resumed_effects, (3, 3, 3, 3))
    assert resumed_effects["launches"] == case["commits"]
    for key in ("receipts", "assessments", "transitions"):
        # Preserve the exact durable identities and payloads confirmed before the crash.
        assert resumed_effects[key][:len(interrupted_effects[key])] == interrupted_effects[key]
    assert [row[2] for row in resumed_effects["receipts"]] == ["tests", "test_review", "implementation"]
    assert [json.loads(row[-1])["commit"] for row in resumed_effects["receipts"]] == case["commits"]
    for key in ("assessments", "transitions"):
        assert [json.loads(row[-1])["stage"] for row in resumed_effects[key]] == [
            "tests", "test_review", "implementation",
        ]
    if boundary == "receipt":
        assert len([row for row in rows["evidence"] if row[0] == confirmed[0]]) == 1
    if boundary == "transition":
        assert rows["task_events"][:len(confirmed)] == confirmed
    stable = snapshot(case)
    assert launch(case, target="code_review")["replay"] == response["replay"]
    assert snapshot(case) == stable


def test_launched_check_with_unknown_terminal_outcome_never_blindly_reruns(project, monkeypatch):
    case = prepared(project)
    runner = case["client"].runtime.check_runner
    original = runner.run
    launches = []

    def lose_terminal(*args, **kwargs):
        launches.append(args[0] if args else kwargs["run_id"])
        original(*args, **kwargs)
        raise PoiseError("test-owned lost terminal outcome after real check launch")

    monkeypatch.setattr(runner, "run", lose_terminal)
    with pytest.raises(PoiseError, match="test-owned lost terminal"):
        case["client"].invoke(request("advance", {
            "request_id": "replay", "task_id": "T1", "target_stage": "code_review",
        }))
    assert len(launches) == 1
    assert case["log"].read_text().splitlines() == case["commits"][:1]
    monkeypatch.setattr(runner, "run", original)
    response = launch(case, target="code_review")
    assert response["status"] == "progression_stopped"
    assert_projection(case, response, stage="tests", reason="unknown_effect", count=0,
                      subject=case["commits"][0])
    stable = snapshot(case)
    assert launch(case, target="code_review")["replay"] == response["replay"]
    assert snapshot(case) == stable
    assert len(launches) == 1
    assert case["log"].read_text().splitlines() == case["commits"][:1]


def test_saved_stop_is_exact_but_new_intent_rechecks_changed_grounds(project):
    case = prepared(project)
    case["fail_file"].write_text("failure\n")
    failed = launch(case, target="code_review")
    assert_projection(case, failed, stage="tests", reason="tests_failed", count=0,
                      subject=case["commits"][0])
    case["fail_file"].unlink()
    stable = snapshot(case)
    assert launch(case, target="code_review")["replay"] == failed["replay"]
    assert snapshot(case) == stable
    original_saved = case["saved"]
    original_ref = failed["replay"]["recovery_ref"]
    case["saved"] = case["commits"][0]  # Independently observed start of this NEW intent.
    case["recovery_file"] = "tests/test_double.py"
    case["recovery_bytes"] = (Path(__file__).parent / "fixtures/restart_auto_subject_test.py").read_text().strip()
    passed = launch(case, target="code_review", request_id="fresh-grounds")
    assert_projection(case, passed)
    assert git(case["root"], "rev-parse", original_ref) == original_saved
    assert git(case["root"], "show", f"{original_ref}:src/saved.txt") == "saved work"
    assert case["log"].read_text().splitlines() == [case["commits"][0], *case["commits"]]


def test_durable_audit_identifies_initiator_sources_effects_and_first_stop(project):
    case = prepared(project)
    before = owned_rows(case)
    response = launch(case, target="code_review")
    assert_projection(case, response)
    after = owned_rows(case)
    for table, old in before.items():
        assert after[table][:len(old)] == old
    journal = [json.loads(row[-1]) for row in after["journal"][len(before["journal"]):]
               if row[-2] == "progression.started"]
    assert len(journal) == 1
    assert journal[0]["actor"] == case["client"].runtime.session
    assert journal[0]["request_id"] == "replay"
    assert journal[0]["target_stage"] == "code_review"
    # Cross-check independently persisted receipts, not response-derived expectations.
    receipts = [json.loads(row[-1]) for row in after["evidence"][len(before["evidence"]):]]
    assert [receipt["commit"] for receipt in receipts] == case["commits"]
    assert all(receipt["passed"] is True for receipt in receipts)
    assert len({receipt["id"] for receipt in receipts}) == 3
    assert [row[2] for row in after["evidence"][len(before["evidence"]):]] == [
        "tests", "test_review", "implementation",
    ]
    assert all(receipt["termination"] == {
        "schema": "run-termination-1", "capture_complete": True,
        "child_reaped": True, "process_group_stopped": True,
    } for receipt in receipts)
    transitions = [json.loads(row[-1]) for row in after["task_events"][len(before["task_events"]):]
                   if json.loads(row[-1])["event"] == "stage_progressed"]
    assert [entry["stage"] for entry in transitions] == ["tests", "test_review", "implementation"]
    assert len(transitions) == 3
    with case["client"].runtime.store.transaction() as database:
        reached = database.execute(
            "SELECT data FROM journal WHERE task_id=? AND event='progression.reached' "
            "AND json_extract(data,'$.request_id')=?", ("T1", "replay"),
        ).fetchall()
    assert [json.loads(row[0])["stage"] for row in reached] == ["code_review"]
    case["client"] = WorkTools(WorkPoise(project["config_path"], case["client"].runtime.session))
    stable = snapshot(case)
    assert launch(case, target="code_review")["replay"] == response["replay"]
    assert snapshot(case) == stable


@pytest.mark.parametrize("kind", ["ignored_collision", "untracked_collision", "prohibited_configuration"])
def test_unsafe_material_refuses_without_anchor_or_file_loss(project, kind):
    case = prepared(project)
    if kind == "prohibited_configuration":
        path = case["root"] / "config" / "working.env"
        path.parent.mkdir(); path.write_text("LOCAL_SETTING=fixture\n")
        git(case["root"], "add", "config/working.env")
        git(case["root"], "commit", "-m", "Test-owned prohibited material")
    else:
        git(case["root"], "rm", "tests/review.txt")
        git(case["root"], "commit", "-m", "Remove accepted path before restart")
        path = case["root"] / "tests" / "review.txt"
        path.write_text("foreign uncommitted bytes\n")
        if kind == "ignored_collision":
            common = Path(git(case["root"], "rev-parse", "--path-format=absolute", "--git-common-dir"))
            (common / "info" / "exclude").write_text("tests/review.txt\n")
    before = snapshot(case)
    with pytest.raises(PoiseError, match="preserv|dirty|untracked|ignored|forbidden|prohibited|scope|configuration"):
        case["client"].invoke(request("advance", {
            "request_id": "unsafe", "task_id": "T1", "target_stage": "code_review",
        }))
    assert snapshot(case) == before
    assert git(case["project"]["app"], "status", "--porcelain") == ""
