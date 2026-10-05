"""Test-owned public lifecycle arrangement and independent SQL observations."""
from copy import deepcopy
import json
from pathlib import Path
import sys

from conftest import WorkPoise, git, write_json
from .helpers import call, result, setup, start, verify


FIXTURES = Path(__file__).parent / "fixtures"


def command_plan(marker, tag):
    plan = json.loads((FIXTURES / "lifecycle_command_plan.json").read_text())
    for step in plan["steps"]:
        for operation in ("apply", "probe"):
            method = step[operation]
            method["argv"] = [
                sys.executable, "-B", str(FIXTURES / "lifecycle_effect.py"),
                operation, str(marker), tag,
            ]
    return plan


def prepare(context, plan):
    return result(context, {
        "plan": plan, "phase": "prepare", "resolutions": [],
        "finding_resolutions": [],
    })


def runtime(project, *, stage="apply"):
    client, context, _, _ = setup(project, kind="commands")
    if stage == "apply":
        return client, context
    # Only this temporary fixture configuration is reshaped. No tested Task's
    # live route is renamed and no operational project configuration is used.
    call(client, "cancel", {"reason": "Replace fixture with custom-stage Task."})
    path = project["root"] / "config/processes/action.json"
    process = json.loads(path.read_text())
    for node in process["stages"]:
        if node["id"] == "apply":
            node["id"] = stage
        node["transitions"] = {
            outcome: stage if target == "apply" else target
            for outcome, target in node["transitions"].items()
        }
        node["rework_targets"] = [
            stage if target == "apply" else target for target in node["rework_targets"]
        ]
    process["route"]["entry"] = stage
    write_json(path, process)
    task = deepcopy(client.task_queries.record(context["task"])["contract"])
    task["id"] = "SCOPED"
    for field in ("checks", "evidence_plan"):
        task[field][stage] = task[field].pop("apply")
    for phase in task["decomposition"]["phases"]:
        if phase["stage"] == "apply":
            phase["stage"] = stage
    for contract in task["stage_contracts"]:
        if contract["stage_id"] == "apply":
            contract["stage_id"] = stage
    client = WorkPoise(project["config_path"], "ACTION-AGENT")
    return client, start(client, task)


def transfer(client, suffix):
    return call(client, "handoff", {
        "request_id": "lifecycle-handoff-" + suffix,
        "reason": "Independent fixture actor continues the same Task.",
        "result": None,
        "commit_message": "Preserve lifecycle fixture state",
        "artifact_paths": [],
    })


def release_current(client, context, suffix):
    return call(client, "release", {
        "request_id": "lifecycle-release-" + suffix,
        "task_id": context["task"],
        "expected_version": client.task_queries.record(context["task"])["version"],
        "reason": "Release a quiescent fixture action without claiming checkpoint handoff.",
    })


def restart_current(client, context, *, suffix="1", edit=False, newborn_again=False):
    action = client.plan_actions.snapshot(client.current_task())
    if action is not None and action["status"] != "complete":
        # Handoff correctly refuses an unfinished external plan. Public release
        # preserves a prepared, effect-free action for the independent actor.
        release_current(client, context, "to-review-" + suffix)
    else:
        transfer(client, "to-review-" + suffix)
    reviewer = WorkPoise(client.config_path, "LIFECYCLE-REVIEWER-" + suffix)
    acquired = start(reviewer, {"id": context["task"]})
    contract = reviewer.task_queries.record(context["task"])["contract"]
    authorization = ({"role": "reviewer", "decision": "Restart this unfinished Task."}
                     if contract.get("planning") is not None else
                     "Independent reviewer authorizes restarting this unfinished Task.")
    newborn = call(reviewer, "task", {
        "action": "restart", "request_id": "lifecycle-restart-" + suffix,
        "task_id": context["task"], "expected_version": acquired["version"],
        "reason": "Start the independently agreed current lifecycle plan.",
        "authorization": authorization,
    })
    if newborn_again:
        newborn = call(reviewer, "task", {
            "action": "restart", "request_id": "lifecycle-newborn-restart-" + suffix,
            "task_id": context["task"], "expected_version": newborn["revision"],
            "reason": "Reconsider the unfinished newborn contract.",
            "authorization": {"role": "reviewer", "decision": "Restart the same newborn."},
        })
    if edit:
        newborn = call(reviewer, "task", {
            "action": "edit", "request_id": "lifecycle-edit-" + suffix,
            "task_id": context["task"], "expected_revision": newborn["revision"],
            "patch": {"definition_of_done": [
                *newborn["draft"]["definition_of_done"], "Preserve the native lifecycle fixture.",
            ]}, "remove": [],
        })
    ready = call(reviewer, "task", {
        "action": "ready", "request_id": "lifecycle-ready-" + suffix,
        "task_id": context["task"], "expected_revision": newborn["revision"],
    })
    assert ready["status"] == "available"
    successor = WorkPoise(client.config_path, client.session)
    resumed = start(successor, {"id": context["task"]})
    assert resumed["task"] == context["task"]
    assert resumed["worktree"] == context["worktree"]
    assert resumed["stage"] == context["stage"]
    assert resumed["iteration"] == 1
    return successor, resumed


def rows(client, task_id):
    statements = {
        "runs": "SELECT task_id,stage,iteration,version,data FROM action_runs WHERE task_id=? ORDER BY stage,iteration",
        "events": "SELECT seq,task_id,stage,iteration,version,at,data FROM action_events WHERE task_id=? ORDER BY seq",
        "results": "SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id",
        "submissions": "SELECT seq,stage,iteration,digest,data FROM submissions WHERE task_id=? ORDER BY seq",
        "evidence": "SELECT id,stage,iteration,data FROM evidence WHERE task_id=? ORDER BY id",
    }
    with client.store.transaction() as database:
        return {key: [tuple(row) for row in database.execute(sql, (task_id,))]
                for key, sql in statements.items()}


def assert_preserved(before, after):
    for key, prior in before.items():
        assert all(row in after[key] for row in prior), key
        if key in ("events", "submissions", "results"):
            assert after[key][:len(prior)] == prior, key


def git_state(context):
    root = Path(context["worktree"])
    return {
        "head": git(root, "rev-parse", "HEAD"),
        "tree": git(root, "write-tree"),
        "status": git(root, "status", "--porcelain=v1", "--untracked-files=all"),
        "refs": git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"),
    }


def metadata(client, task_id):
    with client.store.transaction() as database:
        value = database.execute("SELECT metadata FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
    return json.loads(value)


def corrupt_history(client, task_id, history, *, missing=False):
    value = metadata(client, task_id)
    if missing:
        value.pop("restart_history", None)
    else:
        value["restart_history"] = history
    with client.store.transaction() as database:
        database.execute("UPDATE tasks SET metadata=? WHERE id=?",
                         (json.dumps(value, ensure_ascii=False), task_id))


def refusal_snapshot(client, context, marker):
    with client.store.transaction() as database:
        task = tuple(database.execute(
            "SELECT status,stage_index,iteration,claimed_by,version,current_submission_id,metadata FROM tasks WHERE id=?",
            (context["task"],),
        ).fetchone())
        events = [tuple(row) for row in database.execute(
            "SELECT version,at,data FROM task_events WHERE task_id=? ORDER BY rowid",
            (context["task"],),
        )]
    return {"rows": rows(client, context["task"]), "task": task,
            "task_events": events, "git": git_state(context),
            "marker": marker.read_bytes() if marker.exists() else None}
