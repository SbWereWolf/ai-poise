"""Cancellation and actual mandatory consumers, arranged through public owners."""
from pathlib import Path
import hashlib
import json
import os
import shutil

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from sprints.helpers import setup
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
        assert tools.invoke(request("handoff", {
            "request_id": "draft-to-independent-owner", "reason": "Review and finalize the registered draft.",
            "result": None, "commit_message": None, "artifact_paths": [],
        }))["status"] == "handed_off"
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
    from runtime_services.test_uncertain_check_transfer import _handoff
    from runtime_services.test_task_restart import restart
    from conftest import git
    _handoff(tools, pending, request_id="unknown-check-to-reviewer")
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


def test_released_handoff_is_needed_for_real_resume_but_not_terminal_replay(project):
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    packet = request("handoff", {
        "request_id": "save-before-cancel", "reason": "Transfer verified work to the next owner.",
        "result": None, "commit_message": None, "artifact_paths": [],
    })
    saved = tools.invoke(packet)
    assert saved["status"] == "handed_off"
    assert Path(saved["bundle_path"]).is_file()
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
    assert replay["status"] == "handed_off" and replay["replayed"] is True
    absent(root)


def test_interrupted_export_blocks_retirement_until_exact_export_finishes(project, monkeypatch):
    import zipfile
    from transfer.helpers import enabled, export
    enabled(project)
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    saved = tools.invoke(request("handoff", {
        "request_id": "release-before-required-export",
        "reason": "Release verified work before exporting its materials.",
        "result": None, "commit_message": None, "artifact_paths": [],
    }))
    assert saved["status"] == "handed_off"
    assert Path(saved["bundle_path"]).is_file()
    assert tools.runtime.current_task() is None
    released = tools.runtime.task_queries.record("T1")
    assert released["status"] == "verified" and released["claimed_by"] is None
    original = zipfile.ZipFile.write
    fired = False
    def interrupted(archive, filename, *args, **kwargs):
        nonlocal fired
        effect = original(archive, filename, *args, **kwargs)
        if not fired:
            fired = True
            raise OSError("INJECTED-EXPORT-INTERRUPTION")
        return effect
    monkeypatch.setattr(zipfile.ZipFile, "write", interrupted)
    try:
        export(tools, ids=["T1"], request_id="required-customer-export", handoff=None)
    except OSError as error:
        assert str(error) == "INJECTED-EXPORT-INTERRUPTION"
    assert fired, "An actual export effect must precede the interruption"
    monkeypatch.undo()
    resumed = tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert resumed["status"] == "verified"
    assert tools.runtime.current_task()["claimed_by"] == "material-owner"
    finish(project, tools, commit, "cancelled")
    result = settle(fresh(project))
    assert result["status"] == "delivery_blocked"
    assert result["reason"] == "transfer_pending"
    assert root.is_dir() and source.read_bytes() == b"customer result\n"
    exported = export(tools, ids=["T1"], request_id="required-customer-export", handoff=None)
    assert exported["status"] == "exported"
    assert Path(exported["package_path"]).is_file()
    assert settle(fresh(project))["status"] == "delivery_complete"
    absent(root)


@pytest.mark.parametrize("phase", ["after-receipt", "during-cleanup"])
@pytest.mark.parametrize("retire_first", [False, True], ids=["replay-before-retirement", "replay-after-retirement"])
def test_completed_export_replay_finishes_only_original_preparation(project, monkeypatch, phase, retire_first):
    _completed_export_recovery(project, monkeypatch, phase, retire_first, fail_replay=False)


def test_completed_export_replay_cleanup_failure_is_truthful_and_resumable(project, monkeypatch):
    _completed_export_recovery(project, monkeypatch, "after-receipt", True, fail_replay=True)


def _completed_export_recovery(project, monkeypatch, phase, retire_first, fail_replay):
    """003/008/009: real original and replay effects, exact package, no new copy."""
    from transfer.helpers import enabled, export
    from .archive_oracle import material_provenance, no_new_material_archive

    enabled(project)
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    saved = tools.invoke(request("handoff", {
        "request_id": "release-before-export-cleanup-fault", "reason": "Release original verified export material.",
        "result": None, "commit_message": None, "artifact_paths": [],
    }))
    assert saved["status"] == "handed_off" and Path(saved["bundle_path"]).is_file()
    assert tools.runtime.current_task() is None
    assert tools.runtime.task_queries.record("T1")["claimed_by"] is None
    request_id = "export-cleanup-recovery"
    fired, removed = [], []
    original_event = tools.runtime.store.event
    with monkeypatch.context() as fault:
        if phase == "after-receipt":
            def interrupted_event(actor, task_id, event, data):
                if event == "transfer.exported" and not fired:
                    fired.append(True)
                    raise OSError("INJECTED-EXPORT-CLEANUP-FAULT")
                return original_event(actor, task_id, event, data)
            fault.setattr(tools.runtime.store, "event", interrupted_event)
        else:
            _interrupt_preparation_removal(fault, lambda path: path.name == "preparing",
                                           fired, removed, "INJECTED-EXPORT-CLEANUP-FAULT")
        with pytest.raises((OSError, PoiseError), match="INJECTED-EXPORT-CLEANUP-FAULT"):
            export(tools, ids=["T1"], request_id=request_id, handoff=None)
    assert fired, "The original export must reach the actual post-receipt effect"
    if phase == "during-cleanup":
        assert removed, "A real staging unlink must precede the interruption"
    original_digest, original_state = _export_record(tools, request_id)
    assert original_state["phase"] == "complete"
    receipt = original_state["receipt"]
    assert receipt["status"] == "exported" and receipt["replayed"] is False
    package = Path(receipt["package_path"])
    package_bytes, package_inode = package.read_bytes(), package.stat().st_ino
    assert hashlib.sha256(package_bytes).hexdigest() == receipt["package_digest"]
    preparing = package.parent / "preparing"
    assert preparing.is_dir() and any(path.is_file() for path in preparing.rglob("*"))
    if phase == "after-receipt":
        values = [path.read_bytes() for path in preparing.rglob("*") if path.is_file()]
        assert b"temporary proof must disappear\n" in values
        assert b"customer result\n" in values
    foreign = package.parent.parent / "unrelated-request" / "preparing" / "keep.txt"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"other request working material\n")

    def retire():
        receiver = fresh(project)
        resumed = receiver.invoke(request("bootstrap", {
            "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
        }))
        assert resumed["status"] == "verified"
        finish(project, receiver, commit, "cancelled")
        absent(root)
        assert destination.read_bytes() == b"customer result\n"

    if retire_first:
        retire()
    owner = WorkTools(WorkPoise(project["config_path"], "material-owner"))
    before = owner.runtime.task_queries.record("T1")
    history = owner.runtime.task_queries.history("T1")
    markers = (b"temporary proof must disappear\n", b"customer result\n")
    provenance = material_provenance(owner, markers)
    # Explicitly admit only known live original inputs, original preparation and
    # this exact deliberately delivered package; no service/archive subtree waiver.
    assert all(Path(path).is_relative_to(root) or Path(path).is_relative_to(preparing)
               or Path(path) == package for path in provenance["files"])
    if fail_replay:
        before_files = _preparation_files(preparing)
        retry_fired, retry_removed = [], []
        with monkeypatch.context() as fault:
            _interrupt_preparation_removal(fault, lambda path: path == preparing,
                                           retry_fired, retry_removed, "INJECTED-REPLAY-CLEANUP-FAULT")
            with pytest.raises((OSError, PoiseError), match="INJECTED-REPLAY-CLEANUP-FAULT"):
                export(owner, ids=["T1"], request_id=request_id, handoff=None)
            # Observe failure before removing the fault, through read-only owners.
            assert retry_fired and len(retry_removed) == 1
            remaining = _preparation_files(preparing)
            assert preparing.is_dir() and remaining
            assert len(remaining) == len(before_files) - 1
            assert all(before_files.get(path) == data for path, data in remaining.items())
            failed_digest, failed_state = _export_record(owner, request_id)
            assert failed_digest == original_digest and failed_state["receipt"] == receipt
            assert package.read_bytes() == package_bytes and package.stat().st_ino == package_inode
            assert owner.runtime.task_queries.record("T1") == before
            assert owner.runtime.task_queries.history("T1") == history
            assert owner.runtime.current_task() is None
            assert foreign.read_bytes() == b"other request working material\n"
            absent(root)
            no_new_material_archive(owner, provenance, markers)
        # The same original request is resumed by a fresh original-actor runtime.
        owner = WorkTools(WorkPoise(project["config_path"], "material-owner"))
    replay = export(owner, ids=["T1"], request_id=request_id, handoff=None)
    assert replay == {**receipt, "replayed": True}
    assert not os.path.lexists(preparing), "003: exact completed replay stranded original preparation"
    no_new_material_archive(owner, provenance, markers)
    assert package.read_bytes() == package_bytes and package.stat().st_ino == package_inode
    digest, state = _export_record(owner, request_id)
    assert digest == original_digest and state["receipt"] == receipt
    assert owner.runtime.task_queries.record("T1") == before
    assert owner.runtime.task_queries.history("T1") == history
    assert owner.runtime.current_task() is None
    assert foreign.read_bytes() == b"other request working material\n"
    if not retire_first:
        retire()
    absent(root)
    terminal = owner.runtime.task_queries.record("T1")
    replay_again = export(WorkTools(WorkPoise(project["config_path"], "material-owner")),
                          ids=["T1"], request_id=request_id, handoff=None)
    assert replay_again == {**receipt, "replayed": True}
    assert not os.path.lexists(preparing)
    no_new_material_archive(owner, provenance, markers)
    absent(root)
    assert owner.runtime.task_queries.record("T1") == terminal
    assert package.read_bytes() == package_bytes and package.stat().st_ino == package_inode
    assert destination.read_bytes() == b"customer result\n"
    assert foreign.read_bytes() == b"other request working material\n"


def _export_record(tools, request_id):
    """Read durable original request, never manufacture a completed receipt."""
    with tools.runtime.store.database.transaction() as database:
        row = database.execute(
            "SELECT digest, data FROM transfer_requests WHERE actor=? AND request_id=?",
            (tools.runtime.session, request_id),
        ).fetchone()
    assert row is not None
    return row["digest"], json.loads(row["data"])


def _preparation_files(preparing):
    return {str(path.relative_to(preparing)): path.read_bytes()
            for path in preparing.rglob("*") if path.is_file()}


def _interrupt_preparation_removal(patch, matches, fired, removed, message):
    """Interrupt one actual unlink inside the selected real rmtree traversal."""
    original_rmtree, original_unlink = shutil.rmtree, os.unlink
    def interrupted_removal(path, *args, **kwargs):
        if matches(Path(path)) and not fired:
            with patch.context() as unlink_fault:
                def interrupted_unlink(name, *a, **kw):
                    result = original_unlink(name, *a, **kw)
                    if not fired:
                        removed.append(str(name))
                        fired.append(True)
                        raise OSError(message)
                    return result
                unlink_fault.setattr(os, "unlink", interrupted_unlink)
                return original_rmtree(path, *args, **kwargs)
        return original_rmtree(path, *args, **kwargs)
    interrupted_removal.avoids_symlink_attacks = original_rmtree.avoids_symlink_attacks
    patch.setattr(shutil, "rmtree", interrupted_removal)
