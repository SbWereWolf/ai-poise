"""Independent intent, persistent-state and real Git message contract oracles."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from conftest import WorkPoise, git
from poise.application.result_integration import ResultIntegrationCommands
from poise.application.work import WorkTools
from poise.infrastructure.result_integration import RuntimeResultIntegration
from poise.infrastructure.task_cleanup import RuntimeTaskResourceCleanup
from poise.modules.foundation.errors import DomainError, PoiseError
from poise.modules.result_integration.domain import IntegrationIntent, IntegrationRun

from .helpers import (
    advance_ref_with_same_tree,
    conflicting_source_change,
    integration_input,
    prepare_completed_task,
    request,
    source_change,
)
from .test_existing_task_worktree_completion import _fingerprint


def contract():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "commit_message_contract.json").read_text()
    )


def raw_commit(repository, commit):
    return subprocess.check_output(["git", "-C", str(repository), "cat-file", "commit", commit])


def raw_message(repository, commit):
    return raw_commit(repository, commit).split(b"\n\n", 1)[1]


def git_state(repository):
    operations = {}
    for name in ("MERGE_HEAD", "MERGE_MSG", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        path = Path(git(repository, "rev-parse", "--git-path", name))
        if not path.is_absolute():
            path = repository / path
        operations[name] = path.read_bytes() if path.exists() else None
    return {
        "worktree": _fingerprint(repository),
        "operations": operations,
        "refs": subprocess.check_output(["git", "-C", str(repository), "show-ref"]),
    }


def business_state(tools, task_id="T1"):
    # Independent persisted fields; telemetry/invocation accounting is separate.
    with sqlite3.connect(tools.runtime.store.path) as db:
        return {
            "task": db.execute(
                "SELECT status,stage_index,iteration,claimed_by,version,current_submission_id,metadata "
                "FROM tasks WHERE id=?", (task_id,),
            ).fetchall(),
            "execution": db.execute(
                "SELECT data,version FROM task_execution WHERE task_id=?", (task_id,),
            ).fetchall(),
            "workflow": db.execute(
                "SELECT data FROM task_workflows WHERE task_id=?", (task_id,),
            ).fetchall(),
            "events": db.execute(
                "SELECT seq,version,data FROM task_events WHERE task_id=? ORDER BY seq", (task_id,),
            ).fetchall(),
            "owners": db.execute(
                "SELECT id,task_id FROM sessions ORDER BY id",
            ).fetchall(),
        }


def replace_pending(tools, pending, task_id="T1"):
    # Arrangement on this test's isolated DB, never the installation Task DB.
    with sqlite3.connect(tools.runtime.store.path) as db:
        row = db.execute(
            "SELECT data FROM task_execution WHERE task_id=?", (task_id,),
        ).fetchone()
        data = json.loads(row[0])
        data["pending"] = pending
        db.execute(
            "UPDATE task_execution SET data=? WHERE task_id=?",
            (json.dumps(data), task_id),
        )


def completed(project, change=source_change, task_id="T1"):
    project["cfg"]["git"]["commit_pattern"] = "(?s).+"
    return prepare_completed_task(project, change, task_id=task_id)


def message_input(project, accepted, task_id="T1", request_id="message-contract-1"):
    return {
        **integration_input(project, accepted, task_id=task_id, request_id=request_id),
        "authorization": "Permission to integrate the reviewed result.",
        "commit_message": contract()["input"]["commit_message"],
    }


def diverge_target(project):
    (project["app"] / "target.txt").write_text("Independent target change\n")
    git(project["app"], "add", "target.txt")
    git(project["app"], "commit", "-m", "Preserve the target change")


def conflicted(project):
    tools, child, accepted = completed(project, conflicting_source_change)
    (project["app"] / "src" / "double.py").write_text("VALUE = 'target'\n")
    git(project["app"], "add", "src/double.py")
    git(project["app"], "commit", "-m", "Preserve target behavior")
    payload = message_input(project, accepted)
    result = tools.invoke(request("integrate", payload))
    assert result["status"] == "awaiting_resolution"
    return tools, child, accepted, payload, result


def test_identity_and_storage_keep_permission_and_raw_message_separate():
    values = contract()
    intent = IntegrationIntent.parse(values["input"])
    assert intent.identity() == values["expected_identity"]
    run = IntegrationRun.new(intent, "tasks/T1", "/child/T1", "/runtime/T1")
    assert run.to_storage()["intent"] == values["expected_identity"]
    assert IntegrationRun.restore(run.to_storage()).intent.identity() == values["expected_identity"]


@pytest.mark.parametrize("message", ["omit", None, 42, "", " \n\t", "bad\0message"])
def test_invalid_or_missing_message_rejected_before_application_port(message):
    values = contract()["input"]
    if message == "omit":
        values.pop("commit_message")
    else:
        values["commit_message"] = message
    calls = []

    class Port:
        def apply(self, intent):
            calls.append(intent)

    with pytest.raises(DomainError, match="commit_message|integrate input"):
        ResultIntegrationCommands(Port()).apply(values)
    assert calls == []


@pytest.mark.parametrize("entry", ["apply", "prepare_source"])
@pytest.mark.parametrize("message", [
    "Accepted header\n\nForbidden body",
    "Wrong header\n\nAllowed body",
    "Accepted header\n\nAllowed body\nextra",
])
def test_configured_full_message_refusal_precedes_git_load_and_state_change(
    project, monkeypatch, entry, message,
):
    tools, child, accepted = completed(project)
    payload = message_input(project, accepted)
    payload["commit_message"] = message
    payload["authorization"] = "Accepted header\n\nAllowed body"
    tools.runtime.cfg["git"]["commit_pattern"] = "Accepted header\\n\\nAllowed body"
    before = business_state(tools), git_state(child), git_state(project["app"])

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid message reached Git or integration load")

    monkeypatch.setattr(RuntimeResultIntegration, "_run", forbidden)
    monkeypatch.setattr(RuntimeResultIntegration, "_load", forbidden)
    with pytest.raises(PoiseError, match="configured pattern"):
        if entry == "apply":
            tools.invoke(request("integrate", payload))
        else:
            tools.runtime.integration_tools.prepare_source(payload)
    assert (business_state(tools), git_state(child), git_state(project["app"])) == before


@pytest.mark.parametrize("message", [
    "  Preserve integration purpose\n\n# Keep this line\nUnicode: причина \t \n\n",
    "Preserve purpose\n\nBody without final LF",
])
def test_real_merge_keeps_exact_message_and_cleanup_permission(project, monkeypatch, message):
    tools, child, accepted = completed(project)
    accepted_bytes = raw_commit(project["app"], accepted)
    diverge_target(project)
    payload = message_input(project, accepted)
    payload["commit_message"] = message
    # The permission deliberately cannot satisfy this configured message rule.
    tools.runtime.cfg["git"]["commit_pattern"] = "(?s).*Preserve.*"
    permissions = []
    original = RuntimeTaskResourceCleanup.validate

    def observe_permission(self, task_id, run):
        permissions.append(run.intent.authorization)
        return original(self, task_id, run)

    monkeypatch.setattr(RuntimeTaskResourceCleanup, "validate", observe_permission)
    result = tools.invoke(request("integrate", payload))
    assert result["status"] == "integrated"
    expected = message.encode("utf-8") + (b"" if message.endswith("\n") else b"\n")
    assert raw_message(project["app"], result["integration_head"]) == expected
    assert raw_commit(project["app"], accepted) == accepted_bytes
    saved = tools.runtime.task_queries.record("T1")["pending"]["intent"]
    assert saved["commit_message"] == message
    assert saved["authorization"] == "Permission to integrate the reviewed result."
    assert permissions and set(permissions) == {"Permission to integrate the reviewed result."}
    assert not child.exists()


def test_conflict_resume_and_exact_terminal_replay_keep_full_message(project):
    tools, child, accepted, payload, waiting = conflicted(project)
    before = business_state(tools), git_state(child), git_state(project["app"])
    assert tools.invoke(request("integrate", payload))["status"] == "awaiting_resolution"
    assert (business_state(tools), git_state(child), git_state(project["app"])) == before
    (child / "src" / "double.py").write_text("VALUE = 'resolved'\n")
    continuation = {**payload, "resolutions": [{
        "path": "src/double.py", "resolution": "Keep reviewed behavior with the target change.",
    }]}
    resumer = WorkTools(WorkPoise(project["config_path"], "resumer"))
    done = resumer.invoke(request("integrate", continuation))
    assert done["status"] == "integrated"
    assert done["accepted_commit"] == accepted
    assert raw_message(project["app"], done["integration_head"]) == contract()["expected_git_message"].encode()
    assert done["history"][:len(waiting["history"])] == waiting["history"]
    before = business_state(resumer), git_state(project["app"])
    replay = resumer.invoke(request("integrate", payload))
    assert replay["replayed"] is True
    assert replay["integration_head"] == done["integration_head"]
    assert (business_state(resumer), git_state(project["app"])) == before


@pytest.mark.parametrize("field,value", [
    ("authorization", "Different integration permission"),
    ("commit_message", "Different product purpose"),
    ("commit_message", "  Preserve integration purpose\n\nChanged body\n"),
    ("commit_message", "  Preserve integration purpose\n\n# Keep this line\nUnicode: причина \t \n"),
])
@pytest.mark.parametrize("terminal", [False, True])
def test_changed_permission_or_raw_message_cannot_replace_saved_request(
    project, field, value, terminal,
):
    if terminal:
        tools, child, accepted = completed(project)
        diverge_target(project)
        payload = message_input(project, accepted)
        assert tools.invoke(request("integrate", payload))["status"] == "integrated"
    else:
        tools, child, _, payload, _ = conflicted(project)
    resumer = WorkTools(WorkPoise(project["config_path"], "replay-checker"))
    before = business_state(resumer), git_state(project["app"])
    child_before = git_state(child) if child.exists() else None
    with pytest.raises(PoiseError, match="immutable"):
        resumer.invoke(request("integrate", {**payload, field: value}))
    assert (business_state(resumer), git_state(project["app"])) == before
    assert (git_state(child) if child.exists() else None) == child_before


@pytest.mark.parametrize("interruption", ["merge_before_commit", "commit_before_record"])
def test_interrupted_merge_commit_resume_keeps_raw_message(project, monkeypatch, interruption):
    tools, child, accepted = completed(project)
    diverge_target(project)
    payload = message_input(project, accepted)

    class Interrupted(Exception):
        pass

    with monkeypatch.context() as patch:
        if interruption == "merge_before_commit":
            original = RuntimeResultIntegration._run

            def interrupt(self, cwd, *args, env=None):
                receipt = original(self, cwd, *args, env=env)
                if args[:3] == ("merge", "--no-ff", "--no-commit"):
                    assert receipt["actual_exit_code"] == 0
                    raise Interrupted
                return receipt

            patch.setattr(RuntimeResultIntegration, "_run", interrupt)
        else:
            def interrupt(self, run, worktree, receipt):
                raise Interrupted

            patch.setattr(RuntimeResultIntegration, "_record_candidate", interrupt)
        with pytest.raises(Interrupted):
            tools.invoke(request("integrate", payload))
    observed = git(child, "rev-parse", "HEAD")
    resumer = WorkTools(WorkPoise(project["config_path"], "crash-resumer"))
    done = resumer.invoke(request("integrate", payload))
    assert done["status"] == "integrated"
    assert raw_message(project["app"], done["integration_head"]) == contract()["expected_git_message"].encode()
    if interruption == "commit_before_record":
        assert done["integration_head"] == observed


def test_target_drift_keeps_same_raw_message_in_both_merge_commits(project, monkeypatch):
    tools, _, accepted = completed(project)
    diverge_target(project)
    payload = message_input(project, accepted)
    original = RuntimeResultIntegration._run
    drifted = []

    def drift(self, cwd, *args, env=None):
        if args[:2] == ("merge", "--ff-only") and not drifted:
            drifted.append(advance_ref_with_same_tree(
                project["app"], git(project["app"], "rev-parse", "HEAD"), "Independent drift",
            ))
        return original(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeResultIntegration, "_run", drift)
    done = tools.invoke(request("integrate", payload))
    assert done["status"] == "integrated"
    heads = git(project["app"], "rev-list", "--first-parent", f"{accepted}..HEAD").splitlines()
    assert len(heads) == 2
    assert [raw_message(project["app"], head) for head in heads] == [
        contract()["expected_git_message"].encode(), contract()["expected_git_message"].encode(),
    ]


def test_historical_missing_message_refuses_without_rewriting_record(project, monkeypatch):
    tools, child, _, payload, _ = conflicted(project)
    pending = deepcopy(tools.runtime.task_queries.record("T1")["pending"])
    del pending["intent"]["commit_message"]
    replace_pending(tools, pending)
    before = business_state(tools), git_state(child), git_state(project["app"])

    def forbidden(*args, **kwargs):
        pytest.fail("Historical message-less intent reached Git or recovery write")

    monkeypatch.setattr(RuntimeResultIntegration, "_run", forbidden)
    monkeypatch.setattr(RuntimeResultIntegration, "_save", forbidden)
    with pytest.raises(PoiseError, match="commit_message"):
        tools.invoke(request("integrate", payload))
    assert (business_state(tools), git_state(child), git_state(project["app"])) == before


def test_changed_legacy_identity_refuses_before_recovery_write(project, monkeypatch):
    tools, child, accepted = completed(project, task_id="0048")
    payload = message_input(project, accepted, task_id="0048", request_id="integrate-0048-1")
    legacy = json.loads((Path(__file__).parent / "fixtures" / "message_legacy_integration.json").read_text())
    legacy["intent"] = {key: value for key, value in payload.items() if key != "resolutions"}
    legacy["target_before"] = payload["expected_target_commit"]
    replace_pending(tools, legacy, "0048")
    before = business_state(tools, "0048"), git_state(child), git_state(project["app"])

    def forbidden(*args, **kwargs):
        pytest.fail("Changed legacy identity reached Git or recovery write")

    monkeypatch.setattr(RuntimeResultIntegration, "_save", forbidden)
    monkeypatch.setattr(RuntimeResultIntegration, "_run", forbidden)
    with pytest.raises(PoiseError, match="immutable"):
        tools.invoke(request("integrate", {**payload, "commit_message": "Different purpose"}))
    assert (business_state(tools, "0048"), git_state(child), git_state(project["app"])) == before


def test_public_cli_preserves_multiline_message_separately_from_permission(project):
    _, _, accepted = completed(project)
    diverge_target(project)
    payload = message_input(project, accepted)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "poise", "work"],
        input=json.dumps(request("integrate", payload)), capture_output=True, text=True,
        cwd=project["app"],
        env={**{key: value for key, value in os.environ.items()
                if key not in ("CODEX_SESSION_ID", "CODEX_THREAD_ID")},
             "POISE_CONFIG": str(project["config_path"]),
             "POISE_CALLER_BINDING": str(project["root"] / "message-cli.json"),
             "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    if "response_path" in response:
        response = json.loads(Path(response["response_path"]).read_text())
    assert response["status"] == "integrated"
    assert raw_message(project["app"], response["integration_head"]) == contract()["expected_git_message"].encode()
