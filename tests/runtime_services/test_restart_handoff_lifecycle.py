"""A restart keeps historical work without presenting it as a new submission."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from batch.helpers import bootstrap, request, result, verify
from conftest import WorkPoise, add_test
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_task_restart import restart
from runner.helpers import decision, finding, inspect, resolution
from runner.test_runner_paths import edit, result as stage_result, setup_project


def restarted_task(project, *, include_baseline=False):
    runtime = WorkPoise(project["config_path"], "executor")
    tools = WorkTools(runtime)
    original = bootstrap(tools, project)
    add_test(original["worktree"])
    historical = verify(tools, result(original, "Historical result"))
    assert historical["status"] == "verified"
    task_id = original["task"]
    baseline = {
        "submissions": submissions(runtime, task_id),
        "proof": historical_proof(runtime, task_id),
    }
    before = runtime.task_queries.record(task_id)
    restarted = restart(tools, task_id, before["version"])
    assert submissions(runtime, task_id) == baseline["submissions"]
    assert historical_proof(runtime, task_id) == baseline["proof"]
    ready = tools.invoke(request("task", {
        "action": "ready",
        "request_id": "ready-restarted-handoff",
        "task_id": task_id,
        "expected_revision": restarted["revision"],
    }))
    assert ready["status"] == "available"
    assert submissions(runtime, task_id) == baseline["submissions"]
    assert historical_proof(runtime, task_id) == baseline["proof"]
    resumed = tools.invoke(request("bootstrap", {
        "task": {"id": task_id},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert submissions(runtime, task_id) == baseline["submissions"]
    assert historical_proof(runtime, task_id) == baseline["proof"]
    if include_baseline:
        return runtime, tools, resumed, baseline
    return runtime, tools, resumed


def handoff(tools, *, request_id="transfer-restarted", payload=None, commit_message=None):
    return tools.invoke(request("handoff", {
        "request_id": request_id,
        "reason": "Another agent continues the restarted task.",
        "result": payload,
        "commit_message": commit_message,
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


def historical_proof(runtime, task_id):
    with runtime.store.transaction() as database:
        results = [json.loads(row[0]) for row in database.execute(
            "SELECT data FROM task_results WHERE task_id=? ORDER BY submission_id",
            (task_id,),
        )]
        receipts = [json.loads(row[0]) for row in database.execute(
            "SELECT data FROM evidence WHERE task_id=? ORDER BY id", (task_id,),
        )]
        actors = [row[0] for row in database.execute(
            "SELECT session_id FROM journal WHERE task_id=? AND event=? ORDER BY at",
            (task_id, "stage.verified"),
        )]
    return results, receipts, actors


def feedback_history(runtime, task_id):
    with runtime.store.transaction() as database:
        return [json.loads(row[0]) for row in database.execute(
            "SELECT data FROM workflow_layers WHERE task_id=? ORDER BY submission_id",
            (task_id,),
        )]


def git(worktree, *args):
    return subprocess.check_output(
        ["git", "-C", str(worktree), *args], text=True,
    ).strip()


def test_restart_retains_historical_result_without_making_it_current(project):
    runtime, tools, resumed, baseline = restarted_task(project, include_baseline=True)
    task_id = resumed["task"]
    historical = baseline["submissions"]
    assert len(historical) == 1
    assert verified_results(runtime, task_id) == [historical[0][0]]
    results, receipts, actors = baseline["proof"]
    assert len(results) == len(receipts) == len(actors) == 1
    assert actors == ["executor"]
    assert receipts[0]["source_provenance"]["kind"] == "repository"
    assert receipts[0]["source_provenance"]["bindings"][0]["resolved_path"] == str(
        Path(resumed["worktree"]).resolve()
    )
    assert receipts[0]["commit"] == results[0]["commit"] == git(resumed["worktree"], "rev-parse", "HEAD")
    assert receipts[0]["tree"] == results[0]["verified_tree"]
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
    assert historical_proof(runtime, task_id) == (results, receipts, actors)


def test_null_handoff_does_not_resubmit_pre_restart_result(project):
    runtime, tools, resumed = restarted_task(project)
    task_id = resumed["task"]
    historical = submissions(runtime, task_id)

    receipt = handoff(tools)

    assert receipt["status"] == "handed_off"
    assert runtime.task_queries.record(task_id)["claimed_by"] is None
    assert submissions(runtime, task_id) == historical


def test_changed_source_keeps_old_proof_with_its_original_commit(project):
    runtime, tools, resumed, baseline = restarted_task(project, include_baseline=True)
    task_id = resumed["task"]
    previous = baseline["proof"]
    original_commit = previous[1][0]["commit"]
    source = Path(resumed["worktree"]) / "src/double.py"
    source.write_text("def double(n):\n    return n * 3\n", encoding="utf-8")

    receipt = handoff(tools, request_id="changed-source-transfer",
                      commit_message="Preserve changed source after restart")

    assert receipt["commit"] != original_commit
    assert historical_proof(runtime, task_id) == previous
    assert submissions(runtime, task_id)[:len(baseline["submissions"])] == baseline["submissions"]
    assert source.read_text(encoding="utf-8") == "def double(n):\n    return n * 3\n"
    assert git(resumed["worktree"], "rev-parse", "HEAD") == receipt["commit"]
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    acquired = receiver.invoke(request("bootstrap", {
        "task": {"id": task_id}, "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert historical_proof(runtime, task_id) == previous
    assert submissions(runtime, task_id)[:len(baseline["submissions"])] == baseline["submissions"]
    assert acquired["result_template"]["sections"]["report"] != "Historical result"


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


def test_ready_restarted_task_preserves_contract_and_working_bytes_on_transfer(project):
    runtime, tools, resumed = restarted_task(project)
    task_id = resumed["task"]
    worktree = Path(resumed["worktree"])
    branch = git(worktree, "branch", "--show-current")
    note = worktree / "operator-note.txt"
    note.write_bytes(b"Current work after restart\n")
    fresh = deepcopy(resumed["result_template"])
    fresh["sections"]["report"] = "New result after restart"

    receipt = handoff(tools, request_id="ready-work-transfer", payload=fresh,
                      commit_message="Preserve current restarted work")

    assert receipt["worktree"] == str(worktree)
    assert receipt["task"] == task_id
    assert note.read_bytes() == b"Current work after restart\n"
    assert git(worktree, "branch", "--show-current") == branch
    assert runtime.ownership.snapshot("executor").task_id is None
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    acquired = receiver.invoke(request("bootstrap", {
        "task": {"id": task_id}, "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert acquired["task"] == task_id
    assert acquired["goal"] == project["task"]["goal"]
    assert acquired["requirements"] == project["task"]["requirements"]
    assert acquired["worktree"] == str(worktree)
    assert acquired["result_template"]["sections"]["report"] == "New result after restart"
    assert receiver.runtime.ownership.snapshot("receiver").task_id == task_id
    assert note.read_bytes() == b"Current work after restart\n"
    assert git(worktree, "branch", "--show-current") == branch


def test_newborn_handoff_preserves_same_task_contract_and_three_wip_kinds(project):
    runtime = WorkPoise(project["config_path"], "executor")
    tools = WorkTools(runtime)
    original = bootstrap(tools, project)
    add_test(original["worktree"])
    assert verify(tools, result(original, "Historical result"))["status"] == "verified"
    task_id = original["task"]
    restarted = restart(tools, task_id, runtime.task_queries.record(task_id)["version"])
    worktree = Path(original["worktree"])
    branch = git(worktree, "branch", "--show-current")
    staged = worktree / "tests/staged-note.txt"
    unstaged = worktree / "src/double.py"
    untracked = worktree / "untracked-note.txt"
    staged.write_bytes(b"staged bytes\n")
    git(worktree, "add", "tests/staged-note.txt")
    unstaged.write_bytes(b"def double(n):\n    return n * 2\n")
    untracked.write_bytes(b"untracked bytes\n")
    expected = {path: path.read_bytes() for path in (staged, unstaged, untracked)}

    receipt = handoff(tools, request_id="newborn-wip-transfer",
                      commit_message="Preserve restarted draft work")

    assert receipt["stage"] is None
    assert receipt["task_status"] == "newborn"
    assert receipt["worktree"] == str(worktree)
    assert runtime.ownership.snapshot("executor").task_id is None
    assert git(worktree, "branch", "--show-current") == branch
    assert {path: path.read_bytes() for path in expected} == expected
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    acquired = receiver.invoke(request("bootstrap", {
        "task": {"id": task_id}, "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert acquired["status"] == "newborn"
    assert acquired["task"] == task_id
    assert acquired["draft"] == restarted["draft"]
    assert acquired["worktree"] == str(worktree)
    assert receiver.runtime.ownership.snapshot("receiver").task_id == task_id
    assert {path: path.read_bytes() for path in expected} == expected
    assert git(worktree, "branch", "--show-current") == branch


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


@pytest.mark.parametrize("drift,diagnostic", [
    ("missing_bundle", "bundle missing or changed"),
    ("changed_bundle", "bundle missing or changed"),
    ("changed_worktree", "Worktree changed since handoff"),
    ("changed_head", "Worktree changed since handoff"),
])
def test_changed_preserved_material_blocks_receiver_then_allows_exact_recovery(
    project, drift, diagnostic,
):
    runtime, tools, resumed = restarted_task(project)
    receipt = handoff(tools)
    task_id = resumed["task"]
    worktree = Path(receipt["worktree"])
    bundle = Path(receipt["bundle_path"])
    original_bundle = bundle.read_bytes()
    original_head = git(worktree, "rev-parse", "HEAD")
    foreign = worktree / "foreign-note.txt"
    if drift == "missing_bundle":
        bundle.unlink()
    elif drift == "changed_bundle":
        bundle.write_bytes(b"changed-bundle")
    elif drift == "changed_worktree":
        foreign.write_text("foreign working bytes\n", encoding="utf-8")
    else:
        git(worktree, "commit", "--allow-empty", "-m", "foreign test commit")
    before = runtime.task_queries.record(task_id)
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))

    with pytest.raises(PoiseError, match=diagnostic):
        receiver.invoke(request("bootstrap", {
            "task": {"id": task_id},
            "decision": None,
            "feedback": None,
            "rework_stage": None,
        }))
    assert runtime.task_queries.record(task_id) == before
    assert runtime.ownership.snapshot("receiver").task_id is None
    assert runtime.ownership.snapshot("executor").task_id is None
    if drift == "changed_worktree":
        assert foreign.read_text(encoding="utf-8") == "foreign working bytes\n"
        foreign.unlink()
    elif drift == "changed_head":
        assert git(worktree, "rev-parse", "HEAD") != original_head
        # This is the disposable test repository, not a task or operator checkout.
        git(worktree, "reset", "--hard", original_head)
    else:
        if drift == "changed_bundle":
            assert bundle.read_bytes() == b"changed-bundle"
        else:
            assert not bundle.exists()
        bundle.write_bytes(original_bundle)

    acquired = receiver.invoke(request("bootstrap", {
        "task": {"id": task_id}, "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert acquired["task"] == task_id
    assert acquired["worktree"] == str(worktree)
    assert git(worktree, "rev-parse", "HEAD") == original_head
    assert bundle.read_bytes() == original_bundle


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


def test_restart_transfer_retains_prior_finding_without_reusing_old_correction(project):
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
    edit(correction, "development", "correction\n")
    stage_result(correction, {"resolutions": [resolution("OLD-FIX", "OPEN-1")]})
    assert runtime.verify()["status"] == "verified"
    follow_up = runtime.bootstrap(decision="continue")
    stage_result(follow_up, inspect([], [decision("OLD-FIX", "rejected")]))
    assert runtime.verify()["stage_outcome"] == "changes_requested"
    sender_runtime = WorkPoise(project["config_path"], "S1")
    sender = WorkTools(sender_runtime)
    task_id = draft["task"]
    old_history = feedback_history(runtime, task_id)
    assert any(item["id"] == "OPEN-1" for layer in old_history
               for item in layer["feedback"]["findings"])
    assert any(item["id"] == "OLD-FIX" for layer in old_history
               for item in layer["feedback"]["resolutions"])

    restarted = restart(sender, task_id, runtime.task_queries.record(task_id)["version"])
    assert sender.invoke(request("task", {
        "action": "ready", "request_id": "ready-feedback-after-restart",
        "task_id": task_id, "expected_revision": restarted["revision"],
    }))["status"] == "available"
    resumed = sender.invoke(request("bootstrap", {
        "task": {"id": task_id}, "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    note = Path(resumed["worktree"]) / "unsubmitted-correction.txt"
    note.write_bytes(b"Current unsubmitted correction\n")
    before = runtime.task_queries.record(task_id)
    with pytest.raises(PoiseError, match="WIP requires explicit"):
        handoff(sender, request_id="missing-wip-message")
    assert runtime.task_queries.record(task_id) == before
    assert sender_runtime.ownership.snapshot("S1").task_id == task_id
    assert note.read_bytes() == b"Current unsubmitted correction\n"
    assert feedback_history(runtime, task_id) == old_history

    assert handoff(sender, request_id="finding-restart-transfer",
                   commit_message="Preserve current correction and findings")["status"] == "handed_off"
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    acquired = receiver.invoke(request("bootstrap", {
        "task": {"id": task_id}, "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert acquired["task"] == task_id
    assert [item["id"] for item in acquired["workflow"]["feedback"]["open_findings"]] == ["OPEN-1"]
    assert acquired["workflow"]["feedback"]["pending_resolutions"] == []
    assert feedback_history(runtime, task_id) == old_history
    assert note.read_bytes() == b"Current unsubmitted correction\n"
