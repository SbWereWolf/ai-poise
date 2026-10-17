"""Cancellation and actual mandatory consumers, arranged through public owners."""
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise
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


def test_no_result_does_not_authorize_discarding_a_unique_git_commit(project):
    tools, root, _, commit = arrange(project)
    agree(tools, {"disposition": "no_result", "outputs": []})
    tools.invoke(request("cancel", {"reason": "Cancel without delivered result."}))
    assert settle(fresh(project))["status"] == "delivery_blocked"
    assert root.is_dir()
    # Explicit commit disposition is a separate authorization, never inferred.
    cancel_result = tools.invoke(request("cleanup", {
        "task_id": "T1", "request_id": "discard-exact-commit",
        "authorization": "The fixture customer explicitly discards this exact unique commit.",
        "commit_disposition": {"kind": "discard_authorized", "expected_commit": commit},
    }))
    assert cancel_result["status"] == "cleanup_complete"
    absent(root)


def test_open_registered_draft_has_truthful_block_and_public_owner_continuation(project):
    from batch.helpers import text_artifact
    from artifact_drafts.test_lifecycle import decide
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
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    # R4 permits prevention of an unresumable terminal state OR a supported
    # terminal consumer continuation. Either route must use the public owner.
    try:
        first = tools.invoke(request("cancel", {"reason": "Cancel with a pending acceptance draft."}))
    except PoiseError as error:
        assert "draft" in str(error).lower()
    else:
        assert first["status"] in ("cancelled", "cancel_blocked")
    status = tools.runtime.task_queries.record("T1")["status"]
    assert status in ("verified", "cancelled")
    assert root.is_dir() and Path(artifact["path"]).is_file()
    if status == "cancelled":
        result = settle(fresh(project))
        assert result["status"] == "delivery_blocked" and result["reason"] == "artifact_draft_open"
    else:
        assert tools.invoke(release_packet(tools, "draft-to-independent-owner"))["status"] == "ownership_released"
    reviewer = WorkTools(WorkPoise(project["config_path"], "draft-reviewer"))
    reviewed = decide(reviewer, reviewer.runtime, "review", artifact["id"], "review-before-retirement")
    assert reviewed["status"] == "artifact_drafts_reviewed"
    assert Path(reviewed["snapshots"][0]["snapshot_path"]).read_bytes() == b"registered acceptance result\n"
    closed = decide(reviewer, reviewer.runtime, "finalize", artifact["id"], "finalize-before-retirement")
    assert closed["status"] == "artifact_drafts_finalized"
    if status == "verified":
        receiver = fresh(project)
        receiver.invoke(request("bootstrap", {
            "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None}))
        cancel_and_discard(receiver, commit)
    else:
        tools.invoke(request("cleanup", {
            "task_id": "T1", "request_id": "discard-after-draft-finalization",
            "authorization": "The fixture customer explicitly discards this exact unique commit.",
            "commit_disposition": {"kind": "discard_authorized", "expected_commit": commit},
        }))
    absent(root)
    assert (project["root"] / "result.txt").read_bytes() == b"customer result\n"


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
