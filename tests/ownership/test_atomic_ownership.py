from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
from threading import Barrier, Lock

import pytest

from batch.helpers import request
from conftest import WorkPoise as Poise, git, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from poise.modules.goal_config.domain import BatchValidationError, validate_process
from sprints.helpers import changes as sprint_changes
from sprints.helpers import setup as setup_sprint
from sprints.helpers import task as sprint_task


ROOT = Path(__file__).resolve().parents[2]


def _configure_process(project, worktree_required):
    process = deepcopy(project["process"])
    process["worktree_required"] = worktree_required
    project["process"] = process
    write_json(project["root"] / "config/processes/development.json", process)


def _task(project, task_id):
    task = deepcopy(project["task"])
    task["id"] = task_id
    return task


def _bootstrap(project, session, task_id):
    tools = WorkTools(Poise(project["config_path"], session))
    context = tools.invoke(request("bootstrap", {
        "task": _task(project, task_id),
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    return tools, context


def _ownership_rows(runtime):
    with runtime.store.transaction() as db:
        tasks = {
            row["id"]: row["claimed_by"]
            for row in db.execute("SELECT id,claimed_by FROM tasks ORDER BY id")
        }
        worktrees = {
            row["id"]: row["task_id"]
            for row in db.execute("SELECT id,task_id FROM sessions ORDER BY id")
        }
    return tasks, worktrees


def _actor_ownership(rows, actor):
    tasks, worktrees = rows
    return tuple(task_id for task_id, owner in tasks.items() if owner == actor), worktrees.get(actor)


def _fingerprint(path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(path), *args], stderr=subprocess.STDOUT
        )

    entries = {}
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        mode = stat.S_IMODE(item.lstat().st_mode)
        if item.is_symlink():
            entries[relative] = ("symlink", mode, os.readlink(item))
        elif item.is_file():
            entries[relative] = ("file", mode, item.read_bytes())
        elif item.is_dir():
            entries[relative] = ("directory", mode, None)
    return {
        "cwd": str(Path.cwd()),
        "head": git("rev-parse", "HEAD"),
        "index": git("ls-files", "-s", "-z"),
        "status": git("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        "entries": entries,
    }


def test_four_combinations_and_task_type_dependency(project):
    packs = sorted((ROOT / "config/processes").glob("*.json"))
    packs += sorted((ROOT / "config/projects/ai-poise/config/processes").glob("*.json"))
    packs += sorted((ROOT / "config/catalogue/processes").glob("*.json"))
    packs += sorted((ROOT / "config/catalogue/process-templates").glob("*.json"))
    packs += sorted((ROOT / "config/templates").glob("*.json"))
    assert packs
    for path in packs:
        process = json.loads(path.read_text(encoding="utf-8"))
        assert type(process["worktree_required"]) is bool, path
        validate_process(process)

    candidate = json.loads(packs[0].read_text(encoding="utf-8"))
    candidate.pop("worktree_required")
    with pytest.raises(BatchValidationError, match="worktree_required"):
        validate_process(candidate)
    candidate["worktree_required"] = "yes"
    with pytest.raises(BatchValidationError, match="worktree_required"):
        validate_process(candidate)

    _configure_process(project, False)
    _, task_only = _bootstrap(project, "task-only", "TASK-ONLY")
    assert task_only["worktree"] is None
    task_claims, worktree_claims = _ownership_rows(Poise(project["config_path"], "reader"))
    assert task_claims["TASK-ONLY"] == "task-only"
    assert worktree_claims["task-only"] is None

    _configure_process(project, True)
    complete, context = _bootstrap(project, "complete", "COMPLETE")
    assert Path(context["worktree"]).is_dir()
    complete.runtime.ownership.release_task("COMPLETE")
    workspace = Poise(project["config_path"], "workspace")
    workspace.ownership.acquire_worktree("COMPLETE")
    task_claims, worktree_claims = _ownership_rows(workspace)
    assert task_claims["COMPLETE"] is None
    assert worktree_claims["workspace"] == "COMPLETE"
    assert workspace.ownership.snapshot("neither").task_id is None
    assert workspace.ownership.snapshot("neither").worktree_task_id is None

    setup_sprint(project)
    planner = WorkTools(Poise(project["config_path"], "sprint-planner"))
    contract = sprint_task(project, "SPRINT-OWNED")
    contract["sprint_id"] = "OWNERSHIP-SPRINT"
    drafted = planner.invoke(request("sprint", {
        "action": "draft",
        "sprint_id": "OWNERSHIP-SPRINT",
        "request_id": "draft-ownership-sprint",
        "expected_revision": None,
        "template": {"id": "basic", "version": "1"},
        "changes": sprint_changes([contract]),
    }))
    planner.invoke(request("sprint", {
        "action": "publish",
        "sprint_id": None,
        "request_id": "publish-ownership-sprint",
        "expected_revision": drafted["revision"],
    }))
    sprint_executor = WorkTools(Poise(project["config_path"], "sprint-executor"))
    sprint_context = sprint_executor.invoke(request("bootstrap", {
        "task": {"id": "SPRINT-OWNED"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    task_claims, worktree_claims = _ownership_rows(sprint_executor.runtime)
    assert sprint_context["task"] == "SPRINT-OWNED"
    assert Path(sprint_context["worktree"]).is_dir()
    assert task_claims["SPRINT-OWNED"] == "sprint-executor"
    assert worktree_claims["sprint-executor"] == "SPRINT-OWNED"


def test_worktree_free_task_verifies_hands_off_and_resumes(project):
    _configure_process(project, False)
    project["cfg"]["automatic_checks"] = []
    write_json(project["config_path"], project["cfg"])
    task = _task(project, "TASK-ONLY-LIFECYCLE")
    task["methods"] = []
    task["method_inputs"] = []
    task["checks"] = {stage["id"]: [] for stage in project["process"]["stages"]}
    task["evidence_plan"] = {
        stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
        for stage in project["process"]["stages"]
    }
    first = WorkTools(Poise(project["config_path"], "task-only-first"))
    context = first.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert context["worktree"] is None
    result = deepcopy(context["result_template"])
    result["sections"]["report"] = "Worktree-free stage completed."
    verified = first.invoke(request("verify", {"result": result, "artifacts": []}))
    assert verified["status"] == "verified"
    assert verified["commit"] == git(project["app"], "rev-parse", "HEAD")

    handed_off = first.invoke(request("handoff", {
        "request_id": "task-only-handoff",
        "reason": "Continue the worktree-free Task in another session.",
        "result": None,
        "commit_message": None,
        "artifact_paths": [],
    }))
    assert handed_off["worktree"] is None
    assert handed_off["bundle_path"] is None

    second = WorkTools(Poise(project["config_path"], "task-only-second"))
    resumed = second.invoke(request("bootstrap", {
        "task": {"id": task["id"]},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    assert resumed["status"] == "verified"
    continued = second.invoke(request("bootstrap", {
        "task": None,
        "decision": "continue",
        "feedback": None,
        "rework_stage": None,
    }))
    assert continued["status"] == "active"
    assert continued["worktree"] is None


def test_complete_set_acquisition_replaces_same_kind_atomically(project):
    _configure_process(project, True)
    tools, first = _bootstrap(project, "executor", "OLD")
    second = tools.invoke(request("bootstrap", {
        "task": _task(project, "NEW"), "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    tasks, worktrees = _ownership_rows(tools.runtime)
    assert second["task"] == "NEW"
    assert tasks["OLD"] is None and tasks["NEW"] == "executor"
    assert worktrees["executor"] == "NEW"
    assert Path(first["worktree"]).is_dir()

    versions = {item: tools.runtime.task_queries.record(item)["_version"] for item in ("OLD", "NEW")}
    replay = tools.invoke(request("bootstrap", {
        "task": _task(project, "NEW"), "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert replay["task"] == "NEW"
    assert versions == {item: tools.runtime.task_queries.record(item)["_version"] for item in ("OLD", "NEW")}


def test_self_acquisition_is_idempotent_and_live_owner_is_rejected(project):
    _configure_process(project, True)
    owner, _ = _bootstrap(project, "owner", "TARGET")
    contender, current = _bootstrap(project, "contender", "CURRENT")
    before = _ownership_rows(contender.runtime)
    with pytest.raises(PoiseError, match="live.*owner|owner.*live"):
        contender.invoke(request("bootstrap", {
            "task": _task(project, "TARGET"), "decision": None,
            "feedback": None, "rework_stage": None,
        }))
    assert _ownership_rows(contender.runtime) == before
    assert Path(current["worktree"]).is_dir()
    assert owner.runtime.task_queries.record("TARGET")["claimed_by"] == "owner"

    seed, _ = _bootstrap(project, "seed", "RACE")
    seed.runtime.ownership.release_task("RACE")
    from poise.application.ownership import OwnershipCommands
    from poise.modules.ownership.domain import Liveness

    barrier = Barrier(2)
    observed_lock = Lock()
    observations = []
    before_race = _ownership_rows(contender.runtime)

    def after_preflight(observed):
        assert observed.task_id == "RACE"
        assert observed.task_owner is None
        assert observed.worktree_owner is None
        with observed_lock:
            observations.append(observed.actor)
        barrier.wait()

    def compete(actor):
        runtime = Poise(project["config_path"], actor)
        commands = OwnershipCommands(
            runtime.store.unit_of_work,
            lambda _: Liveness.LIVE,
            after_preflight,
        )
        try:
            commands.acquire_task(actor, "RACE")
        except PoiseError as exc:
            return actor, exc
        return actor, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ("racer-a", "racer-b")))
    winners = [actor for actor, error in results if error is None]
    failures = [error for _, error in results if error is not None]
    assert len(winners) == 1
    assert len(failures) == 1
    assert sorted(observations) == ["racer-a", "racer-b"]
    rows = _ownership_rows(contender.runtime)
    tasks, worktrees = rows
    loser = next(actor for actor, error in results if error is not None)
    assert tasks["RACE"] == winners[0]
    assert worktrees[winners[0]] == "RACE"
    assert list(worktrees.values()).count("RACE") == 1
    assert _actor_ownership(rows, loser) == _actor_ownership(before_race, loser)


def test_partial_acquisition_rolls_back(project):
    _configure_process(project, True)
    target_owner, _ = _bootstrap(project, "target-owner", "TARGET")
    target_owner.runtime.ownership.release_task("TARGET")
    current, _ = _bootstrap(project, "executor", "CURRENT")
    before = _ownership_rows(current.runtime)
    with current.runtime.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_target_claim BEFORE UPDATE ON tasks "
            "WHEN NEW.id='TARGET' AND NEW.claimed_by='executor' "
            "BEGIN SELECT RAISE(ABORT,'ownership-rollback'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="ownership-rollback"):
        current.runtime.ownership.acquire_task("TARGET")
    assert _ownership_rows(current.runtime) == before


def test_release_couples_required_worktree_but_preserves_independent(project):
    _configure_process(project, True)
    dependent, _ = _bootstrap(project, "dependent", "DEPENDENT")
    receipt = dependent.invoke(request("handoff", {
        "request_id": "release-dependent",
        "reason": "Exercise the public release route.",
        "result": None,
        "commit_message": None,
        "artifact_paths": [],
    }))
    assert receipt["status"] == "handed_off"
    tasks, worktrees = _ownership_rows(dependent.runtime)
    assert tasks["DEPENDENT"] is None
    assert worktrees["dependent"] is None

    independent, _ = _bootstrap(project, "independent", "INDEPENDENT-TASK")
    independent.runtime.ownership.acquire_worktree("DEPENDENT")
    independent.runtime.ownership.release_task("INDEPENDENT-TASK")
    tasks, worktrees = _ownership_rows(independent.runtime)
    assert tasks["INDEPENDENT-TASK"] is None
    assert worktrees["independent"] == "DEPENDENT"


def test_dead_owner_recovery_requires_definitive_liveness(project):
    _configure_process(project, True)
    old, _ = _bootstrap(project, "old", "TARGET")
    contender = Poise(project["config_path"], "new")
    before = _ownership_rows(contender)
    with pytest.raises(PoiseError, match="uncertain.*old|old.*uncertain"):
        contender.ownership.acquire_task("TARGET")
    assert _ownership_rows(contender) == before

    from poise.application.ownership import OwnershipCommands
    from poise.modules.ownership.domain import Liveness

    dead = OwnershipCommands(
        contender.store.unit_of_work,
        lambda session: Liveness.DEAD if session == "old" else Liveness.LIVE,
        lambda _: None,
    )
    recovered = dead.acquire_task("new", "TARGET")
    assert recovered.recovered_sessions == ("old",)
    tasks, worktrees = _ownership_rows(contender)
    assert tasks["TARGET"] == "new"
    assert worktrees["old"] is None and worktrees["new"] == "TARGET"
    assert old.runtime.task_queries.record("TARGET")["claimed_by"] == "new"


def test_one_task_and_one_worktree_limit(project):
    _configure_process(project, True)
    first, _ = _bootstrap(project, "first", "FIRST")
    second, _ = _bootstrap(project, "second", "SECOND")
    before = _ownership_rows(second.runtime)
    with pytest.raises(PoiseError, match="live.*first|first.*live"):
        second.runtime.ownership.acquire_worktree("FIRST")
    assert _ownership_rows(second.runtime) == before

    first.runtime.ownership.release_task("FIRST")
    second.runtime.ownership.release_task("SECOND")
    second.runtime.ownership.acquire_worktree("SECOND")
    second.runtime.ownership.acquire_worktree("FIRST")
    tasks, worktrees = _ownership_rows(second.runtime)
    assert tasks["SECOND"] is None
    assert worktrees["second"] == "FIRST"
    assert sum(owner == "second" for owner in tasks.values()) == 0
    assert list(worktrees.values()).count("FIRST") == 1
    assert Path(first.runtime.task_queries.record("FIRST")["worktree"]).is_dir()


def test_acquire_release_preserve_cwd_roots_and_wip(project, monkeypatch):
    _configure_process(project, True)
    tools, first = _bootstrap(project, "executor", "OLD")
    worktree = Path(first["worktree"])
    tracked = worktree / "src/double.py"
    tracked.write_text("def double(n):\n    return n + 2\n", encoding="utf-8")
    tracked.chmod(0o744)
    (worktree / "user-wip.bin").write_bytes(b"\x00user-wip\xff")
    launch_root = project["root"]
    monkeypatch.chdir(launch_root)
    before = _fingerprint(worktree)

    second = tools.invoke(request("bootstrap", {
        "task": _task(project, "NEW"), "decision": None,
        "feedback": None, "rework_stage": None,
    }))
    assert second["task"] == "NEW"
    assert _fingerprint(worktree) == before
    assert Path.cwd() == launch_root
    assert Path(tools.runtime.config_path).is_relative_to(launch_root)

    current_worktree = Path(second["worktree"])
    current_tracked = current_worktree / "src/double.py"
    current_tracked.write_text("def double(n):\n    return n + 3\n", encoding="utf-8")
    current_tracked.chmod(0o700)
    (current_worktree / "live-owner-wip.bin").write_bytes(b"\x00live-owner\xff")
    rival, rival_context = _bootstrap(project, "rival", "RIVAL")
    rejected_before = {
        "old": _fingerprint(worktree),
        "current": _fingerprint(current_worktree),
        "rival": _fingerprint(Path(rival_context["worktree"])),
    }
    with pytest.raises(PoiseError):
        rival.runtime.ownership.acquire_worktree("NEW")
    assert {
        "old": _fingerprint(worktree),
        "current": _fingerprint(current_worktree),
        "rival": _fingerprint(Path(rival_context["worktree"])),
    } == rejected_before

    rollback_owner, rollback_context = _bootstrap(project, "rollback-owner", "ROLLBACK")
    rollback_owner.runtime.ownership.release_task("ROLLBACK")
    rollback_worktree = Path(rollback_context["worktree"])
    rollback_before = {
        "rival": _fingerprint(Path(rival_context["worktree"])),
        "target": _fingerprint(rollback_worktree),
    }
    with rival.runtime.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_wip_claim BEFORE UPDATE ON tasks "
            "WHEN NEW.id='ROLLBACK' AND NEW.claimed_by='rival' "
            "BEGIN SELECT RAISE(ABORT,'wip-rollback'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="wip-rollback"):
        rival.runtime.ownership.acquire_task("ROLLBACK")
    assert {
        "rival": _fingerprint(Path(rival_context["worktree"])),
        "target": _fingerprint(rollback_worktree),
    } == rollback_before
    assert Path.cwd() == launch_root
