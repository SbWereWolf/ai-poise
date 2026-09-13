from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from conftest import WorkPoise as Poise
from conftest import write_json
from poise.modules.foundation.errors import PoiseError

from actions.helpers import advance, inspect, result, setup, verify


PROHIBITION = "Git push is prohibited"


def test_public_work_rejects_remote_git_before_any_git_command(project, monkeypatch):
    h, ctx, plan, base = setup(project, conflict=False)
    verify(
        h,
        result(
            ctx,
            {
                "plan": plan,
                "phase": "prepare",
                "resolutions": [],
                "finding_resolutions": [],
            },
        ),
    )
    inspect(h, advance(h))
    publication = advance(h)
    calls = []

    def unexpected_git(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("publication reached Git before rejecting push")

    monkeypatch.setattr(h, "_git", unexpected_git)
    with pytest.raises(PoiseError, match=PROHIBITION):
        verify(
            h,
            result(
                publication,
                {
                    "target_ref": "refs/heads/main",
                    "expected_commit": base,
                    "authorization": "User: publish reviewed result",
                },
            ),
        )

    assert calls == []


def test_push_required_runtime_is_rejected_before_repository_git(project, monkeypatch):
    config = deepcopy(project["cfg"])
    config["git"]["push_required"] = True
    write_json(project["config_path"], config)
    calls = []

    def unexpected_git(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("runtime reached Git before rejecting push_required=true")

    monkeypatch.setattr(subprocess, "run", unexpected_git)
    with pytest.raises(PoiseError, match=PROHIBITION):
        Poise(project["config_path"], "NO-PUSH")

    assert calls == []


def test_dynamic_git_runners_reject_push_before_their_effect(project, monkeypatch):
    h = Poise(project["config_path"], "NO-PUSH")
    worktree = Path(project["app"])
    calls = []

    def unexpected_effect(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("a dynamic Git runner attempted a push effect")

    monkeypatch.setattr(subprocess, "run", unexpected_effect)
    monkeypatch.setattr(h.plan_actions, "_command", unexpected_effect)

    runners = (
        lambda: h._git(worktree, "push", "backup", "HEAD:refs/heads/task"),
        lambda: h.plan_actions._git_effect(
            {"worktree": str(worktree)}, "push", "backup", "HEAD:refs/heads/task"
        ),
        lambda: h.integration_tools.port._run(
            worktree, "push", "backup", "HEAD:refs/heads/task"
        ),
        lambda: h.cleanup_tools.adapter._run(
            worktree, "push", "backup", "HEAD:refs/heads/task"
        ),
    )

    for run in runners:
        with pytest.raises(PoiseError, match=PROHIBITION):
            run()

    assert calls == []


def test_push_disabled_verification_never_reads_or_writes_remote(project, monkeypatch):
    h = Poise(project["config_path"], "NO-PUSH")
    context = h.bootstrap(task_file=project["task_path"])
    from conftest import add_test, fill

    add_test(context["worktree"])
    fill(context)
    original = h._git
    remote_commands = []

    def observing_git(cwd, *args, **kwargs):
        if args and args[0] in {"push", "fetch", "pull", "ls-remote"}:
            remote_commands.append(args)
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(h, "_git", observing_git)
    report = h.verify()

    assert report["status"] == "verified"
    assert remote_commands == []
