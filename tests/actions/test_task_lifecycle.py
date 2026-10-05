"""Public Task restart must select a fresh native action and retain history."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import WorkPoise, git
from poise.modules.foundation.errors import PoiseError
from .helpers import advance, call, inspect, result, setup, start, verify
from .lifecycle_helpers import (
    assert_preserved, command_plan, git_state, prepare, restart_current,
    rows, runtime, transfer,
)


def verify_current(client, context, plan):
    try:
        return verify(client, prepare(context, plan))
    except PoiseError as error:
        if str(error) == "Started plan is immutable; explicit rework is required":
            pytest.fail("LIFECYCLE_NEW_PLAN_REJECTED_BY_HISTORY")
        raise


@pytest.mark.parametrize("same_plan", [False, True], ids=["different", "identical"])
def test_restart_reentry_has_no_historical_current_action(project, tmp_path, same_plan):
    client, context = runtime(project)
    marker = tmp_path / "effect.txt"
    plan = command_plan(marker, "prior")
    done = verify(client, prepare(context, plan))
    assert done["action"]["status"] == "complete"
    before = rows(client, context["task"])
    git_before = git_state(context)
    client, resumed = restart_current(client, context, edit=True)
    assert git_state(resumed)["head"] == git_before["head"]
    assert_preserved(before, rows(client, context["task"]))
    if resumed["action"] is not None:
        pytest.fail("LIFECYCLE_CURRENT_CONTEXT_IS_HISTORICAL")
    current = plan if same_plan else command_plan(marker, "current")
    out = verify_current(client, resumed, current)
    assert out["status"] == "verified"
    assert out["action"]["plan"] == current
    assert marker.read_text() == ("prior\n" if same_plan else "prior\ncurrent\n")
    after = rows(client, context["task"])
    assert_preserved(before, after)
    assert len(after["runs"]) == len(before["runs"]) + 1
    assert len(after["events"]) > len(before["events"])
    replay_before = rows(client, context["task"])
    assert verify(client, None)["replayed"] is True
    assert rows(client, context["task"]) == replay_before


def test_different_current_merge_plan_is_executable_after_public_restart(project):
    client, context, old_plan, _ = setup(project, conflict=False)
    previous = verify(client, prepare(context, old_plan))
    assert previous["status"] == "verified"
    history = rows(client, context["task"])
    app = project["app"]
    git(app, "checkout", "-b", "current-source", previous["commit"])
    (app / "README.md").write_text("Current lifecycle source\n")
    git(app, "add", "README.md")
    git(app, "commit", "-m", "Current lifecycle composition input")
    source = git(app, "rev-parse", "HEAD")
    git(app, "checkout", "main")
    new_plan = {"kind": "git_merge", "base_commit": previous["commit"],
                "sources": [{"commit": source, "checkpoint_message": "WIP current composition"}]}
    client, resumed = restart_current(client, context, edit=True)
    out = verify_current(client, resumed, new_plan)
    assert out["status"] == "verified"
    assert out["action"]["plan"] == new_plan
    assert out["action"]["cursor"] == 1
    assert Path(resumed["worktree"], "README.md").read_text() == "Current lifecycle source\n"
    assert git(Path(resumed["worktree"]), "merge-base", "--is-ancestor", source, "HEAD") == ""
    assert_preserved(history, rows(client, context["task"]))
    assert len(rows(client, context["task"])["runs"]) == 2


def test_repeated_and_newborn_restarts_create_distinct_current_runs(project, tmp_path):
    client, context = runtime(project)
    marker = tmp_path / "effects.txt"
    prior = None
    for index, tag in enumerate(["one", "two", "three"]):
        if index:
            client, context = restart_current(client, context, suffix=str(index), newborn_again=index == 2)
        out = verify_current(client, context, command_plan(marker, tag))
        assert out["action"]["status"] == "complete"
        observed = rows(client, context["task"])
        if prior is not None:
            assert_preserved(prior, observed)
        assert len(observed["runs"]) == index + 1
        prior = observed
    assert marker.read_text() == "one\ntwo\nthree\n"


def test_current_exact_plan_stays_immutable_after_restart_and_reload(project, tmp_path):
    client, context = runtime(project)
    marker = tmp_path / "effects.txt"
    verify(client, prepare(context, command_plan(marker, "one")))
    client, context = restart_current(client, context)
    current = command_plan(marker, "two")
    verify_current(client, context, current)
    before = rows(client, context["task"])
    before_git = git_state(context)
    reloaded = WorkPoise(project["config_path"], client.session)
    current_context = start(reloaded, {"id": context["task"]})
    assert current_context["action"]["plan"] == current
    changed = command_plan(marker, "forbidden")
    with pytest.raises(PoiseError, match="immutable"):
        verify(reloaded, prepare(current_context, changed))
    assert rows(reloaded, context["task"]) == before
    assert git_state(context) == before_git
    assert marker.read_text() == "one\ntwo\n"


def test_current_run_survives_handoff_reacquisition_and_exact_replay(project, tmp_path):
    client, context = runtime(project)
    marker = tmp_path / "effects.txt"
    verify(client, prepare(context, command_plan(marker, "one")))
    client, context = restart_current(client, context)
    current = command_plan(marker, "two")
    done = verify_current(client, context, current)
    before = rows(client, context["task"])
    transfer(client, "current-complete")
    receiver = WorkPoise(project["config_path"], "CURRENT-RECEIVER")
    resumed = start(receiver, {"id": context["task"]})
    assert resumed["action"]["plan"] == current
    assert resumed["action"]["version"] == done["action"]["version"]
    assert verify(receiver, None)["replayed"] is True
    assert rows(receiver, context["task"]) == before
    assert marker.read_text() == "one\ntwo\n"


@pytest.mark.parametrize("state", ["failed", "blocked"])
def test_current_lifecycle_keeps_explicit_failed_or_blocked_action_rework(project, tmp_path, state):
    client, context = runtime(project)
    marker = tmp_path / "effects.txt"
    verify(client, prepare(context, command_plan(marker, "prior")))
    client, context = restart_current(client, context)
    action = command_plan(marker, state)
    operation = "apply" if state == "failed" else "probe"
    action["steps"][0][operation]["argv"][3] = "apply-fail" if state == "failed" else "probe-unknown"
    out = verify_current(client, context, action)
    assert out["status"] == "action_" + state
    prior = rows(client, context["task"])
    repaired = call(client, "bootstrap", {
        "task": None, "decision": "rework", "feedback": "Replace the known failed/blocked current plan.",
        "rework_stage": "apply",
    })
    assert repaired["iteration"] == 2
    fixed = verify(client, prepare(repaired, command_plan(marker, "fixed")))
    assert fixed["status"] == "verified"
    assert fixed["action"]["status"] == "complete"
    assert marker.read_text() == ("prior\nfailed\nfixed\n" if state == "failed" else "prior\nfixed\n")
    assert_preserved(prior, rows(client, context["task"]))
    assert len(rows(client, context["task"])["runs"]) == 3


@pytest.mark.parametrize("restarted", [True, False], ids=["restarted", "initial"])
def test_public_cli_after_restart_does_not_return_completed_historical_action(project, tmp_path, restarted):
    client, context = runtime(project)
    marker = tmp_path / "effects.txt"
    if restarted:
        verify(client, prepare(context, command_plan(marker, "old")))
        client, context = restart_current(client, context)
    transfer(client, "cli")
    binding_parent = tmp_path / "cli-caller"
    binding_parent.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key not in {"CODEX_THREAD_ID", "CODEX_SESSION_ID", "POISE_CALLER_BINDING"}}
    env.update({"POISE_CONFIG": str(project["config_path"]),
                "POISE_CALLER_BINDING": str(binding_parent / "caller.json"),
                "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")})
    packet = {"operation": "bootstrap", "input": {
        "task": {"id": context["task"]}, "decision": None, "feedback": None,
        "rework_stage": None}, "messages": []}
    terminal = subprocess.run([sys.executable, "-B", "-m", "poise", "work"],
                              input=json.dumps(packet), env=env, text=True, capture_output=True)
    assert terminal.returncode == 0, terminal.stderr + terminal.stdout
    wire = json.loads(terminal.stdout)
    actual = json.loads(Path(wire["response_path"]).read_text()) if "response_path" in wire else wire
    if actual["action"] is not None:
        pytest.fail("LIFECYCLE_CLI_SELECTED_HISTORICAL_ACTION")
    prefix = "old\n" if restarted else ""
    assert (marker.read_text() if marker.exists() else "") == prefix
    historical = rows(client, context["task"])
    current = command_plan(marker, "cli-current")

    def cli(operation, inputs):
        request = {"operation": operation, "input": inputs, "messages": []}
        output = subprocess.run([sys.executable, "-B", "-m", "poise", "work"],
                                input=json.dumps(request), env=env, text=True, capture_output=True)
        assert output.stderr == ""
        minimal = json.loads(output.stdout)
        full = (json.loads(Path(minimal["response_path"]).read_text())
                if "response_path" in minimal else minimal)
        return output.returncode, full

    exit_code, started = cli("verify", {"result": prepare(actual, current), "artifacts": []})
    assert exit_code == 0 and started["status"] == "verified"
    assert started["action"]["plan"] == current
    assert started["action"]["status"] == "complete" and started["action"]["cursor"] == 1
    assert marker.read_text() == prefix + "cli-current\n"
    after = rows(client, context["task"])
    assert_preserved(historical, after)
    assert len(after["runs"]) == (2 if restarted else 1)
    exit_code, readback = cli("bootstrap", packet["input"])
    assert exit_code == 0 and readback["action"] == started["action"]
    before = rows(client, context["task"])
    exit_code, replayed = cli("verify", {"result": None, "artifacts": []})
    assert exit_code == 0 and replayed["replayed"] is True
    assert replayed["action"] == started["action"]
    assert rows(client, context["task"]) == before
    from .lifecycle_helpers import refusal_snapshot
    refusal_before = refusal_snapshot(client, context, marker)
    changed = command_plan(marker, "cli-forbidden")
    exit_code, rejected = cli("verify", {"result": prepare(actual, changed), "artifacts": []})
    assert exit_code == 2 and rejected == {
        "status": "rejected", "reason": "Different result after delivery requires rework",
    }
    assert refusal_snapshot(client, context, marker) == refusal_before
    assert marker.read_text() == prefix + "cli-current\n"


def test_local_publication_after_restart_uses_current_inspected_candidate(project):
    client, context, plan, base = setup(project, conflict=False)
    verify(client, prepare(context, plan))
    inspect(client, advance(client))
    publishing = advance(client)
    first = result(publishing, {"target_ref": "refs/heads/main", "expected_commit": base,
                               "authorization": "First independent local publication."})
    assert verify(client, first)["action"]["status"] == "complete"
    history = rows(client, context["task"])
    client, context = restart_current(client, context)
    verify_current(client, context, plan)
    inspected = inspect(client, advance(client))
    publishing = advance(client)
    current = result(publishing, {"target_ref": "refs/heads/main", "expected_commit": base,
                                 "authorization": "Current independent local publication."})
    try:
        out = verify(client, current)
    except PoiseError as error:
        if "immutable" in str(error):
            pytest.fail("LIFECYCLE_LOCAL_PUBLICATION_SELECTED_HISTORY")
        raise
    assert out["status"] == "verified"
    assert out["commit"] == inspected["commit"]
    assert out["action"]["plan"]["intent"] == current["stage_work"]
    assert out["action"]["steps"][0]["result"]["target_updated"] is False
    assert git(project["app"], "rev-parse", "main") == base
    assert git(project["remote"], "rev-parse", "main") == base
    assert_preserved(history, rows(client, context["task"]))
    assert len(rows(client, context["task"])["runs"]) == 4


@pytest.mark.parametrize("restarted", [True, False], ids=["restarted", "initial"])
def test_interrupted_running_current_action_recovers_once_after_restart(project, tmp_path, monkeypatch, restarted):
    client, context = runtime(project)
    marker = tmp_path / "effects.txt"
    if restarted:
        verify(client, prepare(context, command_plan(marker, "historical")))
    history = rows(client, context["task"])
    if restarted:
        client, context = restart_current(client, context)
    current = command_plan(marker, "running-current")
    original = client.plan_actions._method
    effects = []

    def lose_response(data, method):
        receipt = original(data, method)
        if method["id"] == "APPLY-COUNTER":
            effects.append(receipt)
            raise RuntimeError("lost current lifecycle action response")
        return receipt

    monkeypatch.setattr(client.plan_actions, "_method", lose_response)
    with pytest.raises(RuntimeError, match="^lost current lifecycle action response$"):
        verify_current(client, context, current)
    assert len(effects) == 1 and effects[0]["passed"]
    running = client.plan_actions.snapshot(client.current_task())
    assert running["status"] == "running" and running["cursor"] == 0 and running["attempts"] == 1
    assert running["plan"] == current
    prefix = "historical\n" if restarted else ""
    assert marker.read_text() == prefix + "running-current\n"
    before_resume = rows(client, context["task"])
    reloaded = WorkPoise(project["config_path"], client.session)
    # Pending execution resumes through the original exact packet, not handoff,
    # fabricated receipt or another action allocation.
    out = verify(reloaded, prepare(context, current))
    assert out["status"] == "verified" and out["action"]["status"] == "complete"
    assert out["action"]["attempts"] == 1 and out["action"]["cursor"] == 1
    assert out["action"]["steps"][0]["result"]["effect"] == "recovered_by_probe"
    assert marker.read_text() == prefix + "running-current\n"
    after = rows(reloaded, context["task"])
    assert len(after["runs"]) == len(before_resume["runs"]) == (2 if restarted else 1)
    assert_preserved(history, after)
    assert verify(reloaded, None)["replayed"] is True
    assert rows(reloaded, context["task"]) == after
