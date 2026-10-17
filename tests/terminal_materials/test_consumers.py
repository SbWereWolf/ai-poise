"""Cancellation and actual mandatory consumers, arranged through public owners."""
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from sprints.helpers import setup
from runtime_services.test_participant_continuity import release_packet
from .helpers import (absent, agree, arrange, cancel_and_discard, declaration,
                      file_output, finish, fresh, settle)


def test_newborn_cancel_with_explicit_no_result_retires_materials(project):
    setup(project)
    tools = WorkTools(WorkPoise(project["config_path"], "newborn-owner"))
    tools.invoke(request("task", {
        "action": "create", "task_id": "T1", "sprint_id": None, "request_id": "create-T1",
    }))
    tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    root = tools.runtime.state / tools.runtime.paths["standalone_tasks"] / "T1"
    root.mkdir(parents=True, exist_ok=True)
    (root / "planning.txt").write_bytes(b"discarded plan\n")
    agree(tools, {"disposition": "no_result", "outputs": []})
    assert tools.invoke(request("cancel", {"reason": "Explicitly cancel without result."}))["status"] == "cancelled"
    assert settle(fresh(project))["status"] == "delivery_complete"
    absent(root)
    assert tools.runtime.task_queries.record("T1")["status"] == "cancelled"


def test_cancellation_discards_unrequested_own_commit_without_another_approval(project):
    tools, root, _, commit = arrange(project)
    worktree = Path(tools.runtime.current_task()['worktree'])
    branch = tools.runtime.current_task()['branch']
    assert commit != git(project['app'], 'rev-parse', 'main')
    cancelled = tools.invoke(request("cancel", {"reason": "Discard this Task and its unrequested work."}))
    assert cancelled['status'] == 'cancelled'
    assert not worktree.exists(), 'CANCELLED_TASK_WORKTREE_RETAINED'
    assert cancelled['delivery']['status'] == 'delivery_complete'
    assert branch not in git(project['app'], 'for-each-ref', '--format=%(refname:short)', 'refs/heads').splitlines()
    absent(root)


@pytest.mark.parametrize('ordered_result', [False, True])
def test_cancel_retires_own_service_draft_after_delivering_only_ordered_result(project, monkeypatch, ordered_result):
    from batch.helpers import text_artifact
    artifact_ids = []
    def declare_draft(client, _context):
        value = client.invoke(request("show", {"queries": [{"id": "task", "kind": "task"}]}))["results"][0]["value"]
        opened = client.invoke(request("artifact_drafts", {
            "action": "declare", "request_id": "declare-mandatory-draft", "task_id": "T1",
            "expected_version": value["version"],
            "drafts": [{"scope": "task", "path": "acceptance.txt"}],
            "reason": "The independent acceptance review is not final yet.",
        }))
        artifact_ids.append(opened["drafts"][0]["artifact_id"])
    tools, root, source, commit = arrange(project, before_verify=declare_draft,
        artifact_files=[text_artifact("task", "acceptance.txt", "registered acceptance result\n")])
    artifact = tools.runtime.store.artifact_records("T1")[0]
    assert artifact["id"] == artifact_ids[0]
    assert Path(artifact["path"]).read_bytes() == b"registered acceptance result\n"
    destination = project['root'] / 'result.txt'
    if ordered_result:
        agree(tools, declaration([file_output(source, destination)]))
    worktree = Path(tools.runtime.current_task()['worktree'])
    remove_resource = tools.runtime.cleanup_tools.adapter.remove
    observed = []
    def remove_after_delivery(task_id, resource):
        if ordered_result:
            assert destination.read_bytes() == b'customer result\n'
        observed.append(resource.kind)
        return remove_resource(task_id, resource)
    monkeypatch.setattr(tools.runtime.cleanup_tools.adapter, 'remove', remove_after_delivery)
    cancelled = tools.invoke(request('cancel', {'reason': 'Discard all own service materials; retain only the ordered result.'}))
    assert cancelled['status'] == 'cancelled'
    assert cancelled['delivery']['status'] == 'delivery_complete'
    assert {'worktree', 'branch'} <= set(observed)
    absent(root)
    assert not worktree.exists()
    assert not Path(artifact['path']).exists()
    if ordered_result:
        assert destination.read_bytes() == b'customer result\n'
    else:
        assert not destination.exists()


def test_unknown_check_outcome_blocks_cleanup_without_erasing_saved_output(project, monkeypatch):
    from runtime_services.test_uncertain_check_transfer import _pending
    tools, _, pending, calls = _pending(project, monkeypatch)
    context = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    root = Path(context["task_root"])
    marker = root / "unresolved-output.txt"
    marker.write_bytes(b"unresolved external outcome\n")
    agree(tools, {"disposition": "no_result", "outputs": []})
    try:
        result = settle(tools)
    except PoiseError as error:
        assert "pending" in str(error).lower() or "check" in str(error).lower()
    else:
        assert result["status"] == "delivery_blocked"
        assert result["reason"] == "checks_pending"
    assert marker.read_bytes() == b"unresolved external outcome\n"
    assert tools.runtime.current_task()["pending"] == pending
    assert calls == [pending["runs"][0]["run_id"]]
    from runtime_services.test_uncertain_check_transfer import _release
    from runtime_services.test_task_restart import restart
    from conftest import git
    _release(tools, request_id="unknown-check-to-reviewer")
    reviewer = WorkTools(WorkPoise(project["config_path"], "check-recovery-reviewer"))
    current = reviewer.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None}))
    restarted = restart(reviewer, "T1", current["version"], "retirement-after-unknown-check",
        authorization={"role": "reviewer", "decision":
            "Inspected the stopped original attempt and its effects; abandon the unknown outcome without repeating it."})
    reviewer.invoke(request("task", {
        "action": "ready", "task_id": "T1", "request_id": "ready-recovered-cancellation",
        "expected_revision": restarted["revision"]}))
    resumed = reviewer.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None}))
    assert reviewer.runtime.current_task()["pending"] is None
    from .helpers import delivery_packet
    reviewer.invoke(delivery_packet(reviewer, {"disposition": "no_result", "outputs": []},
                                    request_id="agree-recovered-cancellation"))
    commit = git(Path(resumed["worktree"]), "rev-parse", "HEAD")
    cancel_and_discard(reviewer, commit)
    assert calls == [pending["runs"][0]["run_id"]]
    absent(root)


def test_release_preserves_work_and_replays_after_terminal_cleanup(project):
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    packet = release_packet(tools, "save-before-cancel")
    saved = tools.invoke(packet)
    assert saved["status"] == "ownership_released"
    assert settle(fresh(project))["status"] == "delivery_blocked"
    assert root.is_dir()
    receiver = fresh(project)
    resumed = receiver.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert resumed["status"] == "verified"
    cancel_and_discard(receiver, commit)
    absent(root)
    replay = tools.invoke(packet)
    assert replay["status"] == "ownership_released" and replay["replayed"] is True
    absent(root)
