from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request, result
from conftest import WorkPoise as Poise
from conftest import add_test, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from .helpers import bootstrap, draft, publish, setup, task, verify


def advance_base(project, name):
    marker = project["app"] / f"{name}.txt"
    marker.write_text(f"{name}\n", encoding="utf-8")
    git(project["app"], "add", marker.name)
    git(project["app"], "commit", "-m", f"Advance base for {name}")
    return git(project["app"], "rev-parse", "HEAD")


def execution(runtime, task_id):
    with runtime.store.unit_of_work() as uow:
        snapshot, _ = uow.execution.load(task_id)
    return snapshot


def accept(tools):
    return tools.invoke(request("accept", {}))


def complete_with_change(tools, context, expression):
    source = Path(context["worktree"]) / "src/double.py"
    source.write_text(f"def double(n):\n    return {expression}\n", encoding="utf-8")
    outcome = verify(tools, context)
    accept(tools)
    return outcome["commit"]


def test_result_dependency_starts_from_current_base_and_retains_provenance(project):
    setup(project)
    runtime = Poise(project["config_path"], "result-owner")
    tools = WorkTools(runtime)
    planned = draft(
        tools,
        [task(project, "A"), task(project, "B")],
        [{"predecessor": "A", "successor": "B", "kind": "result"}],
    )
    publish(tools, planned["revision"])
    predecessor = bootstrap(tools, "A")
    predecessor_commit = complete_with_change(tools, predecessor, "n * 2")
    current_base = advance_base(project, "successor-base")

    successor = bootstrap(tools, "B")

    assert predecessor_commit != current_base
    assert git(Path(successor["worktree"]), "rev-parse", "HEAD") == current_base
    assert execution(runtime, "B")["base"] == current_base
    overview = bootstrap(tools, "S")
    assert overview["result_provenance"] == {
        "B": [{"predecessor": "A", "result_commit": predecessor_commit}]
    }


def test_incomplete_result_dependency_blocks_and_reports_provenance(project):
    setup(project)
    runtime = Poise(project["config_path"], "blocked-reader")
    tools = WorkTools(runtime)
    planned = draft(
        tools,
        [task(project, "A"), task(project, "B")],
        [{"predecessor": "A", "successor": "B", "kind": "result"}],
    )
    overview = publish(tools, planned["revision"])

    assert overview["eligible"] == ["A"]
    assert overview["result_provenance"] == {
        "B": [{"predecessor": "A", "result_commit": None}]
    }
    with pytest.raises(PoiseError, match="not eligible"):
        bootstrap(tools, "B")


def test_distinct_result_commits_are_provenance_not_branch_bases(project):
    setup(project)
    runtime = Poise(project["config_path"], "multi-result-owner")
    tools = WorkTools(runtime)
    planned = draft(
        tools,
        [task(project, "A"), task(project, "B"), task(project, "C")],
        [
            {"predecessor": "A", "successor": "C", "kind": "result"},
            {"predecessor": "B", "successor": "C", "kind": "result"},
        ],
    )
    publish(tools, planned["revision"])
    commits = []
    for task_id, expression in (("A", "n * 2"), ("B", "n * 3")):
        commits.append(complete_with_change(tools, bootstrap(tools, task_id), expression))
    current_base = advance_base(project, "multi-result-base")

    overview = bootstrap(tools, "S")
    assert overview["eligible"] == ["C"]
    assert overview["result_provenance"] == {
        "C": [
            {"predecessor": "A", "result_commit": commits[0]},
            {"predecessor": "B", "result_commit": commits[1]},
        ]
    }
    successor = bootstrap(tools, "C")
    assert git(Path(successor["worktree"]), "rev-parse", "HEAD") == current_base


def test_standalone_creation_uses_current_base_and_persists_it(project):
    setup(project)
    current_base = advance_base(project, "standalone-base")
    runtime = Poise(project["config_path"], "standalone-owner")
    tools = WorkTools(runtime)
    contract = task(project, "STANDALONE")
    contract["sprint_id"] = None

    context = tools.invoke(request("bootstrap", {
        "task": contract,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))

    assert git(Path(context["worktree"]), "rev-parse", "HEAD") == current_base
    assert execution(runtime, "STANDALONE")["base"] == current_base


def test_partial_setup_retry_keeps_observed_base_after_ref_moves(project, monkeypatch):
    setup(project)
    runtime = Poise(project["config_path"], "retry-owner")
    tools = WorkTools(runtime)
    contract = task(project, "RETRY")
    contract["sprint_id"] = None
    real_git = runtime._git
    failed = False

    def fail_after_add(cwd, *args, **kwargs):
        nonlocal failed
        output = real_git(cwd, *args, **kwargs)
        if not failed and args[:2] == ("worktree", "add"):
            failed = True
            raise PoiseError("injected failure after worktree add")
        return output

    monkeypatch.setattr(runtime, "_git", fail_after_add)
    with pytest.raises(PoiseError, match="recoverable|repeat"):
        tools.invoke(request("bootstrap", {
            "task": contract,
            "decision": None,
            "feedback": None,
            "rework_stage": None,
        }))
    saved_base = execution(runtime, "RETRY")["pending"]["base"]
    later_base = advance_base(project, "retry-later-base")
    monkeypatch.setattr(runtime, "_git", real_git)

    recovered = tools.invoke(request("bootstrap", {
        "task": contract,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))

    assert saved_base != later_base
    assert git(Path(recovered["worktree"]), "rev-parse", "HEAD") == saved_base
    assert execution(runtime, "RETRY")["base"] == saved_base


def test_base_observation_is_fixed_before_durable_sprint_start(project, monkeypatch):
    setup(project)
    runtime = Poise(project["config_path"], "boundary-owner")
    tools = WorkTools(runtime)
    planned = draft(tools, [task(project, "A"), task(project, "B")])
    publish(tools, planned["revision"])
    observed_base = git(project["app"], "rev-parse", "HEAD")
    real_start = runtime.task_commands.start
    moved = {}

    def move_base_then_start(task_id, actor, snapshot, *, force_duplicate_start=False, restart_entry_tree=None):
        moved["base"] = advance_base(project, "boundary-later-base")
        return real_start(task_id, actor, snapshot,
                          force_duplicate_start=force_duplicate_start, restart_entry_tree=restart_entry_tree)

    monkeypatch.setattr(runtime.task_commands, "start", move_base_then_start)
    context = bootstrap(tools, "A")

    assert moved["base"] != observed_base
    assert git(Path(context["worktree"]), "rev-parse", "HEAD") == observed_base
    assert execution(runtime, "A")["base"] == observed_base
    monkeypatch.setattr(runtime.task_commands, "start", real_start)
    verify(tools, context)
    accept(tools)

    later = bootstrap(tools, "B")
    assert git(Path(later["worktree"]), "rev-parse", "HEAD") == moved["base"]
    assert execution(runtime, "B")["base"] == moved["base"]


def test_handoff_resume_preserves_existing_worktree_when_base_moves(project):
    runtime = Poise(project["config_path"], "handoff-owner")
    tools = WorkTools(runtime)
    context = tools.invoke(request("bootstrap", {
        "task": deepcopy(project["task"]),
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    add_test(context["worktree"])
    payload = result(context, "Preserve this worktree")
    handed_off = tools.invoke(request("handoff", {
        "request_id": "branch-base-handoff",
        "reason": "Resume with another owner",
        "result": payload,
        "commit_message": "WIP: preserve worktree base",
        "artifact_paths": [],
    }))
    worktree = Path(context["worktree"])
    saved_head = git(worktree, "rev-parse", "HEAD")
    saved_base = execution(runtime, "T1")["base"]
    exclude = Path(git(worktree, "rev-parse", "--git-path", "info/exclude"))
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write("\n.resume-sentinel\n")
    sentinel = worktree / ".resume-sentinel"
    sentinel_bytes = b"existing worktree must survive resume\n"
    sentinel.write_bytes(sentinel_bytes)
    later_base = advance_base(project, "handoff-later-base")
    next_tools = WorkTools(Poise(project["config_path"], "handoff-resumer"))

    resumed = next_tools.invoke(request("bootstrap", {
        "task": {"id": "T1"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))

    assert handed_off["status"] == "handed_off"
    assert saved_head != later_base
    assert resumed["worktree"] == str(worktree)
    assert git(worktree, "rev-parse", "HEAD") == saved_head
    assert execution(next_tools.runtime, "T1")["base"] == saved_base
    assert sentinel.read_bytes() == sentinel_bytes
