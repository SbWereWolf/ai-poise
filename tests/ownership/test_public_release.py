"""Ordinary public release keeps ownership separate from handoff and evidence."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from batch.helpers import configure, request, result, verify
from conftest import WorkPoise, add_test
from poise.application.work import WorkTools
from poise.infrastructure.sqlite.ownership import SqliteOwnershipRepository
from poise.modules.foundation.errors import PoiseError
from poise.modules.work.domain import parse_request


def start(project, actor="release-owner", task_id="T1"):
    configure(project)
    client = WorkTools(WorkPoise(project["config_path"], actor))
    contract = deepcopy(project["task"])
    contract["id"] = task_id
    context = client.invoke(request("bootstrap", {
        "task": contract,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    return client, context


def release_packet(version, task_id="T1", request_id="ordinary-release-1",
                   reason="Reviewer must recover the preserved unknown check"):
    packet = json.loads(Path(__file__).with_name("fixtures").joinpath("public-release.json").read_text())
    packet["input"].update({
        "task_id": task_id,
        "request_id": request_id,
        "expected_version": version,
        "reason": reason,
    })
    return packet


def execution_state(client, task_id="T1"):
    with client.runtime.store.unit_of_work() as uow:
        execution, version = uow.execution.load(task_id)
    return execution, version


def release_journal(client):
    with client.runtime.store.transaction() as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM journal WHERE event='ownership.ordinary_released' ORDER BY seq"
        )]


def preserve_unknown_attempt(owner, context, monkeypatch):
    add_test(context["worktree"])
    original = owner.runtime.evidence_commands.record_receipt

    def lose_receipt(*args, **kwargs):
        monkeypatch.setattr(owner.runtime.evidence_commands, "record_receipt", original)
        raise OSError("injected terminal receipt persistence failure")

    monkeypatch.setattr(owner.runtime.evidence_commands, "record_receipt", lose_receipt)
    with pytest.raises(OSError, match="injected terminal receipt persistence failure"):
        verify(owner, result(context, "Preserve the unknown attempt"))
    unresolved = execution_state(owner)[0]["pending"]
    assert unresolved["kind"] == "check_attempt"
    saved_evidence = owner.runtime.evidence_commands.list_for("T1")

    return saved_evidence


def test_public_release_preserves_unknown_attempt_and_untracked_wip(project, monkeypatch):
    owner, context = start(project)
    saved_evidence = preserve_unknown_attempt(owner, context, monkeypatch)

    def forbid_rerun(*args, **kwargs):
        raise AssertionError("ordinary release reran an unknown check")

    monkeypatch.setattr(owner.runtime.check_runner, "run", forbid_rerun)
    worktree = Path(context["worktree"])
    unfinished = worktree / "unfinished.note"
    unfinished.write_bytes(b"preserve local work\n")
    head = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"])
    status = subprocess.check_output(["git", "-C", str(worktree), "status", "--porcelain=v1"])

    with pytest.raises(PoiseError, match="pending|unknown|attempt"):
        owner.invoke(request("handoff", {
            "request_id": "blocked-handoff",
            "reason": "Reviewer needs a distinct actor",
            "result": None,
            "commit_message": "Preserve pending work",
            "artifact_paths": [],
        }))
    before = owner.runtime.task_queries.record("T1")
    saved_execution = execution_state(owner)
    released = owner.invoke(release_packet(before["version"]))

    assert released["status"] == "ownership_released"
    assert released["task"] == "T1"
    assert released["request_id"] == "ordinary-release-1"
    assert released["replayed"] is False
    assert released["before"]["task_id"] == "T1"
    assert released["before"]["worktree_task_id"] == "T1"
    assert released["after"]["task_id"] is None
    assert released["after"]["worktree_task_id"] is None
    after = owner.runtime.task_queries.record("T1")
    assert after["claimed_by"] is None
    assert after["status"] == "active"
    assert after["stage_index"] == before["stage_index"]
    assert after["iteration"] == before["iteration"]
    assert execution_state(owner) == saved_execution
    assert owner.runtime.evidence_commands.list_for("T1") == saved_evidence
    assert unfinished.read_bytes() == b"preserve local work\n"
    assert subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"]) == head
    assert subprocess.check_output(["git", "-C", str(worktree), "status", "--porcelain=v1"]) == status
    assert owner.invoke(request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))["status"] == "read_only"
    assert len(release_journal(owner)) == 1


def test_public_release_rejects_foreign_stale_absent_and_conflicting_requests(project):
    owner, context = start(project)
    foreign = WorkTools(WorkPoise(project["config_path"], "foreign-actor"))
    version = context["version"]
    original = release_packet(version)
    claimed = owner.runtime.task_queries.record("T1")
    with pytest.raises(PoiseError):
        foreign.invoke(original)
    with pytest.raises(PoiseError, match="version|stale|conflict"):
        owner.invoke(release_packet(version - 1, request_id="stale-release"))
    assert owner.runtime.task_queries.record("T1") == claimed
    assert release_journal(owner) == []

    first = owner.invoke(original)
    with pytest.raises(PoiseError):
        owner.invoke(release_packet(version + 1, request_id="new-release-without-claim"))
    replay = owner.invoke(original)
    assert replay["replayed"] is True
    assert replay["before"] == first["before"]
    assert replay["after"] == first["after"]
    changed = release_packet(version, reason="Different reason under the same request ID")
    with pytest.raises(PoiseError, match="conflict"):
        owner.invoke(changed)

    successor = WorkTools(WorkPoise(project["config_path"], "successor"))
    successor.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    successor_state = successor.runtime.task_queries.record("T1")
    successor_binding = successor.runtime.ownership.snapshot("successor")
    successor_execution = execution_state(successor)
    assert owner.invoke(original)["replayed"] is True
    assert successor.runtime.task_queries.record("T1") == successor_state
    assert successor.runtime.ownership.snapshot("successor") == successor_binding
    assert execution_state(successor) == successor_execution
    with pytest.raises(PoiseError, match="actor|caller|conflict"):
        foreign.invoke(original)
    assert len(release_journal(owner)) == 1


def test_public_release_keeps_independent_worktree_binding(project):
    owner, context = start(project)
    other, _ = start(project, actor="other-owner", task_id="OTHER")
    other.runtime.ownership.release_task("OTHER")
    owner.runtime.ownership.acquire_worktree("OTHER")
    before = owner.runtime.ownership.snapshot("release-owner")
    assert before.task_id == "T1" and before.worktree_task_id == "OTHER"

    released = owner.invoke(release_packet(context["version"]))
    assert released["after"]["task_id"] is None
    assert released["after"]["worktree_task_id"] == "OTHER"
    after = owner.runtime.ownership.snapshot("release-owner")
    assert after.task_id is None and after.worktree_task_id == "OTHER"
    assert owner.runtime.task_queries.record("OTHER")["claimed_by"] is None


def test_public_release_rolls_back_when_dependent_binding_cannot_be_cleared(project, monkeypatch):
    owner, context = start(project)
    before = owner.runtime.ownership.snapshot("release-owner")
    task_before = owner.runtime.task_queries.record("T1")
    original = SqliteOwnershipRepository.bind_worktree

    def fail_dependent_clear(self, actor, task_id):
        if actor == "release-owner" and task_id is None:
            raise PoiseError("injected dependent binding failure")
        return original(self, actor, task_id)

    monkeypatch.setattr(SqliteOwnershipRepository, "bind_worktree", fail_dependent_clear)
    with pytest.raises(PoiseError, match="injected dependent binding failure"):
        owner.invoke(release_packet(context["version"]))
    assert owner.runtime.ownership.snapshot("release-owner") == before
    assert owner.runtime.task_queries.record("T1") == task_before
    assert release_journal(owner) == []


def test_public_release_rejects_foreign_dependent_worktree_without_mutation(project, monkeypatch):
    owner, context = start(project)
    evidence = preserve_unknown_attempt(owner, context, monkeypatch)
    # Isolated fixture: retain the Task claim while moving its required worktree claim.
    with owner.runtime.store.unit_of_work() as uow:
        uow.ownership.bind_worktree("release-owner", None)
        uow.ownership.bind_worktree("foreign-tree-owner", "T1")
    task = owner.runtime.task_queries.record("T1")
    bindings = [owner.runtime.ownership.snapshot(actor)
                for actor in ("release-owner", "foreign-tree-owner")]
    assert task["claimed_by"] == "release-owner"
    assert bindings[0].worktree_task_id is None
    assert bindings[1].worktree_task_id == "T1"
    execution = execution_state(owner)
    assert execution[0]["pending"]["kind"] == "check_attempt"
    worktree = Path(context["worktree"])
    wip = worktree / "foreign-owned.note"
    wip.write_bytes(b"preserve foreign worktree WIP\n")
    head = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"])
    status = subprocess.check_output(["git", "-C", str(worktree), "status", "--porcelain=v1"])
    journal = release_journal(owner)
    with pytest.raises(PoiseError):
        owner.invoke(release_packet(task["version"]))
    assert owner.runtime.task_queries.record("T1") == task
    assert [owner.runtime.ownership.snapshot(actor)
            for actor in ("release-owner", "foreign-tree-owner")] == bindings
    assert execution_state(owner) == execution
    assert owner.runtime.evidence_commands.list_for("T1") == evidence
    assert release_journal(owner) == journal
    assert wip.read_bytes() == b"preserve foreign worktree WIP\n"
    assert subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"]) == head
    assert subprocess.check_output(["git", "-C", str(worktree), "status", "--porcelain=v1"]) == status


@pytest.mark.parametrize("mutation,field,value", [
    pytest.param("omit", field, None, id=f"omit-{field}")
    for field in ("task_id", "request_id", "expected_version", "reason")
] + [
    pytest.param("set", field, None, id=f"null-{field}")
    for field in ("task_id", "request_id", "expected_version", "reason")
] + [
    pytest.param("set", "task_id", "", id="empty-task-id"),
    pytest.param("set", "task_id", [], id="invalid-task-id"),
    pytest.param("set", "reason", "", id="empty-reason"),
    pytest.param("set", "request_id", "", id="empty-request-id"),
    pytest.param("set", "expected_version", "2", id="string-version"),
    pytest.param("float", "expected_version", None, id="equal-float-version"),
    pytest.param("set", "expected_version", True, id="boolean-version"),
    pytest.param("set", "actor", "someone-else", id="extra-actor"),
])
def test_release_packet_requires_exact_schema_without_caller_identity(project, mutation, field, value):
    owner, context = start(project)
    packet = release_packet(context["version"])
    if mutation == "omit":
        del packet["input"][field]
    else:
        packet["input"][field] = float(context["version"]) if mutation == "float" else value
    task = owner.runtime.task_queries.record("T1")
    binding = owner.runtime.ownership.snapshot("release-owner")
    execution = execution_state(owner)
    journal = release_journal(owner)
    # Require schema-level refusal even when numeric values compare equal.
    with pytest.raises(PoiseError):
        parse_request(packet, owner.runtime.cfg["batch"])
    # Only a domain refusal is valid; incidental Python exceptions must fail.
    with pytest.raises(PoiseError):
        owner.invoke(packet)
    assert owner.runtime.task_queries.record("T1") == task
    assert owner.runtime.ownership.snapshot("release-owner") == binding
    assert execution_state(owner) == execution
    assert release_journal(owner) == journal
