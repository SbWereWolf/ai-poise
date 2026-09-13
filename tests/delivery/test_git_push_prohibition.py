from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from conftest import WorkPoise as Poise
from conftest import write_json
from poise.modules.foundation.errors import PoiseError

from actions.helpers import advance, inspect, method, result, setup, start, verify


PROHIBITION = "Git push is prohibited"


def assert_actionable_local_integration(error: pytest.ExceptionInfo[PoiseError]) -> None:
    message = str(error.value)
    assert PROHIBITION in message
    assert "push_required=false" in message
    assert "integrate" in message
    assert "ff-only" in message


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
    with pytest.raises(PoiseError) as error:
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

    assert_actionable_local_integration(error)
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
    with pytest.raises(PoiseError) as error:
        Poise(project["config_path"], "NO-PUSH")

    assert_actionable_local_integration(error)
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
        with pytest.raises(PoiseError) as error:
            run()
        assert_actionable_local_integration(error)

    assert calls == []


def test_public_verification_method_rejects_git_push_before_process(project, monkeypatch):
    from conftest import add_test
    from tests.batch.helpers import bootstrap, result as batch_result, verify as batch_verify
    from verification.test_current_registry_mutation import (
        OBLIGATIONS,
        change,
        configure_public_registry_case,
        operation,
        public_tools,
        registration,
    )

    configure_public_registry_case(project)
    tools = public_tools(project, "NO-PUSH-METHOD")
    context = bootstrap(tools, project)
    add_test(context["worktree"])
    direct_push = registration(
        "PUBLIC_PUSH",
        "must-not-run",
        stage="test_implementation",
        covers=OBLIGATIONS,
    )
    direct_push["method"]["argv"] = [
        "git",
        "push",
        "backup",
        "HEAD:refs/heads/task",
    ]
    direct_push["method"]["verification_plan"].update(
        responsibility="Reject a public verification method that requests git push.",
        change_surface=["tests/**"],
    )
    prepared = batch_result(context)
    prepared["method_additions"] = change(
        operation("add", "PUBLIC_PUSH", registration=direct_push),
        request_id="public-push-method",
    )
    calls = []

    original_process = subprocess.Popen

    def unexpected_process(argv, *args, **kwargs):
        if argv and Path(argv[0]).name == "git" and "push" in argv[1:]:
            calls.append(((argv, *args), kwargs))
            raise AssertionError("verification started git push")
        return original_process(argv, *args, **kwargs)

    monkeypatch.setattr("poise.execution.subprocess.Popen", unexpected_process)
    with pytest.raises(PoiseError) as error:
        batch_verify(tools, prepared)

    assert_actionable_local_integration(error)
    assert calls == []


def test_public_command_plan_rejects_git_push_before_process(project, monkeypatch):
    h, context, _, _ = setup(project, kind="commands")
    push = method("PUSH", "raise AssertionError('python fallback must not run')")
    push["argv"] = ["git", "push", "backup", "HEAD:refs/heads/task"]
    probe = method("PROBE", "raise SystemExit(1)")
    plan = {
        "kind": "commands",
        "steps": [
            {
                "id": "push",
                "probe_false_exit_codes": [1],
                "apply": push,
                "probe": probe,
            }
        ],
    }
    calls = []

    original_process = subprocess.Popen

    def unexpected_process(argv, *args, **kwargs):
        if argv and Path(argv[0]).name == "git" and "push" in argv[1:]:
            calls.append(((argv, *args), kwargs))
            raise AssertionError("command plan started git push")
        return original_process(argv, *args, **kwargs)

    monkeypatch.setattr("poise.execution.subprocess.Popen", unexpected_process)
    with pytest.raises(PoiseError) as error:
        verify(
            h,
            result(
                context,
                {
                    "plan": plan,
                    "phase": "prepare",
                    "resolutions": [],
                    "finding_resolutions": [],
                },
            ),
        )

    assert_actionable_local_integration(error)
    assert calls == []


def test_push_disabled_verification_never_reads_or_writes_remote(project, monkeypatch):
    h = Poise(project["config_path"], "NO-PUSH")
    context = start(h, project["task"])
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
