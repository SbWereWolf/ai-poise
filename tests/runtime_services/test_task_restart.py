"""Recovery of a broken unfinished Task through the public newborn lifecycle."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from batch.helpers import configure, request
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from sprints.helpers import bootstrap as sprint_bootstrap
from sprints.helpers import draft, publish, setup as setup_sprint, task as sprint_task


def restart(client, task_id, version, request_id="restart-broken-task", *,
            reason="The saved execution contract cannot reach its next stage.",
            authorization="User authorized recovery of this unfinished Task."):
    return client.invoke(request("task", {
        "action": "restart",
        "request_id": request_id,
        "task_id": task_id,
        "expected_version": version,
        "reason": reason,
        "authorization": authorization,
    }))


def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args], text=True,
    ).strip()


def execution(runtime, task_id):
    with runtime.store.unit_of_work() as unit:
        return deepcopy(unit.execution.load(task_id)[0])


def task_state(runtime, task_id, actors=()):
    return {
        "task": deepcopy(runtime.task_queries.record(task_id)),
        "execution": execution(runtime, task_id),
        "ownership": {
            actor: runtime.ownership.snapshot(actor) for actor in actors
        },
    }


def wip_state(worktree):
    root = Path(worktree)
    return {
        "head": git(root, "rev-parse", "HEAD"),
        "index": git(root, "write-tree"),
        "status": git(root, "status", "--porcelain=v1", "--untracked-files=all"),
        "tracked": (root / "src" / "double.py").read_bytes(),
        "staged": (root / "tests" / "restart-staged.txt").read_bytes(),
        "untracked": (root / "restart-untracked.txt").read_bytes(),
    }


def test_restarts_same_standalone_identity_and_preserves_history_and_worktree(project):
    configure(project)
    executor = WorkTools(Poise(project["config_path"], "executor"))
    context = executor.invoke(request("bootstrap", {
        "task": project["task"],
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    task_id = context["task"]
    worktree = Path(context["worktree"])
    (worktree / "src" / "double.py").write_text(
        "def double(value):\n    return value * 3\n", encoding="utf-8",
    )
    (worktree / "tests" / "restart-staged.txt").write_text(
        "staged WIP\n", encoding="utf-8",
    )
    git(worktree, "add", "tests/restart-staged.txt")
    (worktree / "restart-untracked.txt").write_text(
        "untracked WIP\n", encoding="utf-8",
    )
    git_before = wip_state(worktree)
    before = executor.runtime.task_queries.record(task_id)
    execution_before = execution(executor.runtime, task_id)

    restarted = restart(executor, task_id, before["version"])

    assert restarted["status"] == "newborn"
    assert restarted["task"] == task_id
    assert restarted["sprint"] is None
    assert restarted["draft"] == {
        key: deepcopy(value)
        for key, value in before["contract"].items()
        if key not in {"id", "sprint_id"}
    }
    after = executor.runtime.task_queries.record(task_id)
    assert after["worktree"] == execution_before["worktree"] == context["worktree"]
    assert after["branch"] == execution_before["branch"]
    assert after["pending"] is None
    assert after["attempts"] == 0
    assert after["publication"] is None
    assert after["last_report"] is None
    assert [item["event"] for item in after["history"]][:-1] == [
        item["event"] for item in before["history"]
    ]
    assert after["history"][-1]["event"] == "restarted_newborn"
    assert wip_state(worktree) == git_before

    ready = executor.invoke(request("task", {
        "action": "ready",
        "request_id": "ready-restarted-standalone",
        "task_id": task_id,
        "expected_revision": restarted["revision"],
    }))
    assert ready["status"] == "available"
    resumed = executor.invoke(request("bootstrap", {
        "task": {"id": task_id},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert resumed["worktree"] == str(worktree)
    assert wip_state(worktree) == git_before

    replay = restart(executor, task_id, before["version"])
    assert {key: value for key, value in replay.items() if key != "interaction"} == {
        key: value for key, value in restarted.items() if key != "interaction"
    } | {"replayed": True}


def test_restart_rejects_live_foreign_owner_without_any_task_state_change(project):
    configure(project)
    owner = WorkTools(Poise(project["config_path"], "owner"))
    context = owner.invoke(request("bootstrap", {
        "task": project["task"],
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    contender = WorkTools(Poise(project["config_path"], "contender"))
    before = task_state(owner.runtime, context["task"], ("owner", "contender"))

    with pytest.raises(PoiseError, match="owned.*another|handoff"):
        restart(
            contender,
            context["task"],
            before["task"]["version"],
            request_id="foreign-restart",
        )

    assert task_state(owner.runtime, context["task"], ("owner", "contender")) == before


def test_restart_rejects_stale_version_and_conflicting_replay_without_mutation(project):
    configure(project)
    tools = WorkTools(Poise(project["config_path"], "owner"))
    context = tools.invoke(request("bootstrap", {
        "task": project["task"],
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    current = tools.runtime.task_queries.record(context["task"])
    with pytest.raises(PoiseError, match="version"):
        restart(
            tools,
            context["task"],
            current["version"] + 1,
            request_id="stale-restart",
        )
    assert tools.runtime.task_queries.record(context["task"]) == current

    restart(tools, context["task"], current["version"])
    after = deepcopy(tools.runtime.task_queries.record(context["task"]))
    with pytest.raises(PoiseError, match="request.*conflict|intent"):
        restart(
            tools,
            context["task"],
            current["version"],
            reason="A different restart intent must not replay.",
        )
    assert tools.runtime.task_queries.record(context["task"]) == after


def test_restarted_published_sprint_member_becomes_available_under_same_id(project):
    setup_sprint(project)
    planner = WorkTools(Poise(project["config_path"], "planner"))
    planned = draft(planner, [sprint_task(project, "BROKEN")])
    publish(planner, planned["revision"])
    active = sprint_bootstrap(planner, "BROKEN")
    planner.invoke(request("handoff", {
        "request_id": "release-broken",
        "reason": "Reviewer will recover the broken contract.",
        "result": None,
        "commit_message": None,
        "artifact_paths": [],
    }))
    reviewer = WorkTools(Poise(project["config_path"], "reviewer"))
    before = reviewer.runtime.task_queries.record("BROKEN")

    born = restart(reviewer, "BROKEN", before["version"])
    assert born["sprint"] == "S"
    assert born["claimed_by"] == "reviewer"
    ready = reviewer.invoke(request("task", {
        "action": "ready",
        "request_id": "ready-restarted-member",
        "task_id": "BROKEN",
        "expected_revision": born["revision"],
    }))

    assert ready["status"] == "available"
    assert ready["task"] == "BROKEN"
    current = reviewer.runtime.sprint_tools.overview("S")
    assert [item["id"] for item in current["tasks"]] == ["BROKEN"]
    assert current["tasks"][0]["status"] == "available"
    resumed = sprint_bootstrap(reviewer, "BROKEN")
    assert resumed["status"] == "active"
    assert resumed["worktree"] == active["worktree"]


def test_restart_rejects_pending_unknown_outcome_without_mutation(project):
    configure(project)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    context = tools.invoke(request("bootstrap", {
        "task": project["task"],
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    data = tools.runtime.current_task()
    data["pending"] = "checks"
    tools.runtime.store.save(data)
    before = tools.runtime.task_queries.record(context["task"])

    with pytest.raises(PoiseError, match="unknown.*outcome|pending.*recovery"):
        restart(tools, context["task"], before["version"])

    assert tools.runtime.task_queries.record(context["task"]) == before


@pytest.mark.parametrize("status", ["completed", "cancelled", "superseded"])
def test_restart_contract_rejects_terminal_work(status, project):
    configure(project)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    context = tools.invoke(request("bootstrap", {
        "task": project["task"],
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    with tools.runtime.store.transaction() as database:
        database.execute(
            "UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?",
            (status, context["task"]),
        )
    record = tools.runtime.task_queries.record(context["task"])

    with pytest.raises(PoiseError, match="unfinished|unintegrated"):
        restart(tools, context["task"], record["version"])


def test_failed_next_stage_entry_is_reported_as_broken(project):
    configure(project)
    process = deepcopy(project["process"])
    second = process["stages"][1]
    requirement = {
        "id": "unreachable-next-input",
        "kind": "artifact",
        "stages": [second["id"]],
        "phase": "pre",
        "scope": "task",
        "pattern": "inputs/missing.txt",
        "minimum": 1,
        "maximum": 1,
        "source": {"kind": "preexisting"},
    }
    task = deepcopy(project["task"])
    task["content_contract"]["requirements"] = [requirement]
    task["stage_contracts"][1]["entry_requirements"] = [requirement["id"]]
    tools = WorkTools(Poise(project["config_path"], "executor"))
    context = tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    from conftest import add_test
    from batch.helpers import result, verify

    add_test(context["worktree"])
    verified = verify(tools, result(context))
    assert verified["status"] == "verified"
    blocked = tools.invoke(request("bootstrap", {
        "task": None,
        "decision": "continue",
        "feedback": None,
        "rework_stage": None,
    }))

    assert blocked["status"] == "broken"
    assert blocked["failure"]["kind"] == "content_requirements_failed"
    assert blocked["failure"]["phase"] == "pre"
    assert {item["action"] for item in blocked["recovery"]} == {
        "repair_stage_contract",
        "restart_task",
    }
