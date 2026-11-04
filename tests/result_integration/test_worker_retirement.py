"""R1-R4/V1-V9: actual processes survive checks and stop before source removal."""
import json
import hashlib
from pathlib import Path
import sys

import pytest

from conftest import git, write_json
from poise.application.work import WorkTools
from .helpers import integration_input, prepare_completed_task, request, source_change
from .worker_retirement_support import FIXTURES, OWNER, scenario


def public_receipts(value):
    if isinstance(value, dict):
        if "actual_exit_code" in value:
            yield value
        for item in value.values():
            yield from public_receipts(item)
    elif isinstance(value, list):
        for item in value:
            yield from public_receipts(item)


def test_workers_stop_after_fresh_checks_and_publication_before_source_removal(project, monkeypatch):
    with scenario(project, monkeypatch) as case:
        before = len(case.events("check"))
        result = case.invoke()
        assert result["status"] == "integrated"
        assert len(case.events("check")) == before + 1
        case.assert_retired()
        calls = case.events("retire_call")
        assert len(calls) == 1
        assert calls[0]["task"] == "T1"
        assert calls[0]["project"] == "demo"
        assert calls[0]["worktree"] == calls[0]["cwd"] == str(case.tree)
        assert calls[0]["request_id"] == "worker-retirement-1"
        assert not case.tree.exists()
        events = (case.owner / "events.jsonl").read_bytes()
        replay = case.invoke()
        assert replay["status"] == "integrated"
        assert replay["accepted_commit"] == case.accepted
        assert (case.owner / "events.jsonl").read_bytes() == events
        case.assert_foreign_available()


def test_failed_fresh_check_preserves_workers_and_skips_retirement(project, monkeypatch):
    with scenario(project, monkeypatch) as case:
        target = git(project["app"], "rev-parse", "HEAD")
        case.mode("check_failure")
        result = case.invoke()
        assert result["status"] == "blocked"
        assert result["phase"] == "checks_failed"
        assert result["checks"][-1]["passed"] is False
        assert git(project["app"], "rev-parse", "HEAD") == target
        assert case.events("retire_call") == []
        assert case.tree.is_dir()
        assert [p.poll() for p in case.own] == [None, None]
        case.assert_foreign_available()


def test_unconfirmed_publication_preserves_workers_and_skips_retirement(project, monkeypatch):
    with scenario(project, monkeypatch) as case:
        target = git(project["app"], "rev-parse", "HEAD")
        overlap = project["app"] / "src/worker-app.txt"
        overlap.write_text("foreign-main-work\n")
        result = case.invoke()
        assert result["status"] == "blocked"
        assert result["phase"] == "publication_failed"
        assert result["publication"] is None
        assert git(project["app"], "rev-parse", "HEAD") == target
        assert overlap.read_text() == "foreign-main-work\n"
        assert case.events("retire_call") == []
        assert [p.poll() for p in case.own] == [None, None]
        assert case.tree.is_dir()
        case.assert_foreign_available()


def test_stop_failure_keeps_publication_proof_and_resources_until_same_request_retry(project, monkeypatch):
    with scenario(project, monkeypatch) as case:
        case.mode("refuse")
        pending = case.invoke()
        case.assert_pending(pending)
        serialized = json.dumps(pending)
        assert "fixture-runtime-retirement-refused" in serialized
        assert "complete-retirement-output" in serialized
        assert any(receipt["actual_exit_code"] == 71
                   and receipt["stderr"] == "fixture-runtime-retirement-refused\n"
                   and receipt["stdout"] == "complete-retirement-output\n"
                   for receipt in public_receipts(pending))
        assert [p.poll() for p in case.own] == [None, None]
        checks, publication = pending["checks"], pending["publication"]
        count = len(case.events("check"))
        case.mode("ok")
        done = case.invoke()
        assert done["status"] == "integrated"
        assert done["checks"] == checks
        assert done["publication"] == publication
        assert done["accepted_commit"] == case.accepted
        assert len(case.events("check")) == count
        assert [r["request_id"] for r in case.events("retire_call")] == [
            "worker-retirement-1", "worker-retirement-1",
        ]
        case.assert_retired()
        assert not case.tree.exists()


def test_partial_stop_preserves_in_flight_worker_then_finishes_only_remaining_effect(project, monkeypatch):
    with scenario(project, monkeypatch) as case:
        busy = case.owner / "own-1/busy"
        busy.touch()
        pending = case.invoke()
        case.assert_pending(pending)
        assert "fixture-runtime-work-in-flight" in json.dumps(pending)
        assert [r["worker"] for r in case.events("stopped")] == ["own-0"]
        assert case.own[0].wait(timeout=5) == 0
        assert case.own[1].poll() is None
        busy.unlink()
        done = case.invoke()
        assert done["status"] == "integrated"
        assert done["checks"] == pending["checks"]
        assert done["publication"] == pending["publication"]
        case.assert_retired()


def test_git_cleanup_retry_preserves_confirmed_stop_without_repeating_effect(project, monkeypatch):
    with scenario(project, monkeypatch) as case:
        git(project["app"], "worktree", "lock", str(case.tree))
        pending = case.invoke()
        case.assert_pending(pending)
        case.assert_retired()
        count = len(case.events("check"))
        git(project["app"], "worktree", "unlock", str(case.tree))
        done = case.invoke()
        assert done["status"] == "integrated"
        assert done["checks"] == pending["checks"]
        assert done["publication"] == pending["publication"]
        assert len(case.events("check")) == count
        case.assert_retired()
        assert not case.tree.exists()


@pytest.mark.parametrize("mismatch", ["task", "source"])
def test_owner_refusal_preserves_foreign_process_and_application_sources(project, monkeypatch, mismatch):
    with scenario(project, monkeypatch) as case:
        state = json.loads((case.owner / "owner.json").read_text())
        if mismatch == "task":
            state["task"] = "FOREIGN-TASK"
        else:
            state["worktree"] = str(case.owner / "foreign-source")
        write_json(case.owner / "owner.json", state)
        pending = case.invoke()
        case.assert_pending(pending)
        assert "fixture-runtime-owner-mismatch" in json.dumps(pending)
        assert case.events("stopped") == []
        assert [p.poll() for p in case.own] == [None, None]


def test_declared_retirement_spawn_error_is_visible_and_preserves_owned_resources(project, monkeypatch):
    with scenario(project, monkeypatch) as case:
        selected = json.loads(case.configuration.read_text())
        missing = str(case.owner / "missing-runtime-owner")
        selected["commands"]["retire"] = [missing]
        write_json(case.configuration, selected)
        pending = case.invoke()
        case.assert_pending(pending)
        assert missing in json.dumps(pending)
        assert case.events("retire_call") == []
        assert [p.poll() for p in case.own] == [None, None]


def test_preparation_with_declared_retire_needs_no_future_id_and_later_passes_real_id(project, monkeypatch):
    with scenario(project, monkeypatch, declare_before=True) as case:
        prepared = case.events("prepare")
        assert len(prepared) == 1
        assert prepared[0]["task"] == "T1"
        assert prepared[0]["project"] == "demo"
        assert prepared[0]["worktree"] == prepared[0]["cwd"] == str(case.tree)
        assert "request_id" not in prepared[0]
        assert case.events("retire_call") == []
        case.mode("refuse")
        pending = case.invoke()
        case.assert_pending(pending)
        case.mode("ok")
        assert case.invoke()["status"] == "integrated"
        assert [r["request_id"] for r in case.events("retire_call")] == [
            "worker-retirement-1", "worker-retirement-1",
        ]
        case.assert_retired()


def test_external_work_cli_executes_selected_integration_bytes_before_worker_retirement(project, monkeypatch):
    with scenario(project, monkeypatch) as case:
        code, result, origin = case.cli()
        assert code == 0
        assert result["status"] == "integrated"
        source = Path(__file__).resolve().parents[2] / "src/poise"
        assert origin["package_location"] == str(source / "__init__.py")
        assert origin["integration_source"] == str(source / "infrastructure/result_integration.py")
        assert origin["integration_sha256"] == hashlib.sha256(
            (source / "infrastructure/result_integration.py").read_bytes(),
        ).hexdigest()
        case.assert_retired()
        assert not case.tree.exists()


def test_existing_preparation_without_retire_prepares_and_integrates_without_retirement(project, monkeypatch):
    owner = project["root"] / "legacy-preparation"
    owner.mkdir()
    configuration = json.loads((FIXTURES / "preparation.json").read_text())
    del configuration["commands"]["retire"]
    configuration["parameters"] = {
        "python": sys.executable, "fixture": str(OWNER), "owner_root": str(owner),
    }
    config_path = write_json(owner / "preparation.json", configuration)
    project["cfg"]["worktree_preparation"] = {"configuration": str(config_path)}
    monkeypatch.syspath_prepend(str(FIXTURES))
    observed = []
    original = WorkTools.invoke

    def observe_preparation(self, packet):
        result = original(self, packet)
        if packet["operation"] == "bootstrap":
            observed.append(result["preparation"])
        return result

    monkeypatch.setattr(WorkTools, "invoke", observe_preparation)
    tools, tree, accepted = prepare_completed_task(project, source_change)
    assert len(observed) == 1
    assert observed[0]["status"] == "ready"
    events = owner / "events.jsonl"
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["event"] == "prepare"
    assert rows[0]["task"] == "T1"
    assert rows[0]["project"] == "demo"
    assert rows[0]["worktree"] == rows[0]["cwd"] == str(tree)
    before = events.read_bytes()
    done = tools.invoke(request("integrate", integration_input(project, accepted)))
    assert done["status"] == "integrated"
    assert done["accepted_commit"] == accepted
    assert done["publication"]["status"] == "confirmed"
    assert events.read_bytes() == before
    assert not tree.exists()
    assert "retire" not in json.loads(config_path.read_text())["commands"]
    assert json.loads(project["config_path"].read_text())["worktree_preparation"] == {
        "configuration": str(config_path),
    }
