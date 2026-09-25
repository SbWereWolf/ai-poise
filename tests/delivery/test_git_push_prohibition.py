from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from conftest import WorkPoise as Poise
from conftest import write_json
from poise.composition import project_tools
from poise.modules.foundation.errors import PoiseError

from actions.helpers import advance, inspect, method, result, setup, start, verify


PROHIBITION = "Git push is prohibited"


def assert_actionable_local_integration(error: pytest.ExceptionInfo[PoiseError]) -> None:
    message = str(error.value)
    assert PROHIBITION in message
    assert "push_required=false" in message
    assert "integrate" in message
    assert "ff-only" in message


def test_public_work_local_publication_never_invokes_remote_git(project, monkeypatch):
    h,ctx,plan,base=setup(project,conflict=False)
    candidate=verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    inspect(h,advance(h));publication=advance(h)
    calls=[]; original=subprocess.run
    def no_remote(argv,*args,**kwargs):
        if isinstance(argv,(tuple,list)) and Path(str(argv[0])).name=='git':
            calls.append(list(argv))
            assert not any(a in ('push','fetch','ls-remote') for a in argv),argv
        return original(argv,*args,**kwargs)
    monkeypatch.setattr(subprocess,'run',no_remote)
    report=verify(h,result(publication,{'target_ref':'refs/heads/main','expected_commit':base,
        'authorization':'User: record reviewed local result'}))
    assert report['status']=='verified'
    assert report['commit']==candidate['commit']
    assert report['action']['steps'][0]['result']['remote_publication'] is False
    assert report['action']['steps'][0]['result']['target_updated'] is False
    assert calls


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


def test_project_setup_rejects_push_required_before_git_probe(project, monkeypatch):
    from poise.infrastructure.projects import FileProjectSetup
    from projects.helpers import setup_case

    settings, _, request = setup_case(project)
    push_edit = next(
        edit for edit in request["edits"] if edit["path"] == ["git", "push_required"]
    )
    push_edit["value"] = True
    calls = []

    def unexpected_probe(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("project setup reached a Git probe")

    monkeypatch.setattr(FileProjectSetup, "_probe", unexpected_probe)
    with pytest.raises(PoiseError) as error:
        project_tools(settings).apply(request)

    assert_actionable_local_integration(error)
    assert calls == []
    assert not (project["root"] / "configured" / "pilot").exists()


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


def test_configured_tokenizer_rejects_git_push_before_process(monkeypatch):
    from poise.infrastructure.accounting_measurement import PayloadMeasurer

    runtime = type("Runtime", (), {"cfg": {"accounting": {"tokenizer": {
        "kind": "command",
        "command": {
            "argv": ["git", "push", "backup", "HEAD:refs/heads/task"],
            "cwd": ".",
            "environment": {},
            "timeout_seconds": 1.0,
            "max_output_bytes": 1024,
        },
    }}}})()
    calls = []

    def unexpected_process(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("configured tokenizer started git push")

    monkeypatch.setattr(
        "poise.infrastructure.accounting_measurement.subprocess.run",
        unexpected_process,
    )
    with pytest.raises(PoiseError) as error:
        PayloadMeasurer(runtime).tokenize(["payload"])

    assert_actionable_local_integration(error)
    assert calls == []


def test_configured_mcp_probe_rejects_git_push_before_process(tmp_path, monkeypatch):
    from poise.infrastructure.mcp_probe import StdioProbe

    spec = {
        "argv": ["git", "push", "backup", "HEAD:refs/heads/task"],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 1.0,
        "max_output_bytes": 1024,
        "mcp": {
            "max_message_bytes": 1024,
            "max_messages": 4,
            "max_pages": 1,
            "protocol_version": "2025-06-18",
            "client_info": {"name": "test", "version": "1"},
            "required_tools": [],
            "call": None,
            "shutdown_seconds": 0.1,
        },
    }
    calls = []

    def unexpected_process(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("configured MCP probe started git push")

    monkeypatch.setattr(
        "poise.infrastructure.mcp_probe.subprocess.Popen",
        unexpected_process,
    )
    with pytest.raises(PoiseError) as error:
        StdioProbe(spec, tmp_path, {"stdout": "stdout.txt", "stderr": "stderr.txt"}).run()

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
    report = verify(h, fill(context))

    assert report["status"] == "verified"
    assert remote_commands == []
