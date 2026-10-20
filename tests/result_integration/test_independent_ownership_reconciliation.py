"""Terminal proof must allow real independent commits and reject stale authority."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import re
from threading import Event

import pytest

from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.infrastructure.result_integration import RuntimeResultIntegration
from poise.modules.foundation.errors import LockUnavailable, PoiseError

from .helpers import integration_input, prepare_completed_task, request, source_change


def inputs():
    return json.loads(Path(__file__).with_name("fixtures").joinpath(
        "independent-reconciliation.json").read_text(encoding="utf-8"))


def assert_relevant_refusal(observed, cause):
    """Require this operation's freshness conflict and saved-request continuation."""
    error = observed.get("refusal")
    assert isinstance(error, PoiseError), "expected a domain refusal"
    message = str(error).casefold()
    controls = inputs()["refusal_controls"]
    for pattern in controls["scope_patterns"]:
        assert re.search(pattern, message), "refusal belongs to another operation"
    assert (
        re.search(controls["causes"][cause]["pattern"], message)
        or re.search(controls["generic_basis_pattern"], message)
    ), "refusal does not explain the changed basis in this scenario"
    assert re.search(controls["continuation_pattern"], message), (
        "refusal lacks the saved integration continuation"
    )
    assert not re.search(controls["negated_continuation_pattern"], message), (
        "refusal prohibits the expected saved integration continuation"
    )


def check_refusal_oracle(cause):
    """Independent literal controls run before the blocked integration assertions."""
    controls = inputs()["refusal_controls"]
    specific = controls["causes"][cause]
    for message in (
        specific["intended"], controls["generic_intended"], specific["existing_style"],
    ):
        assert_relevant_refusal({"refusal": PoiseError(message)}, cause)
    invalid = [
        None,
        PoiseError(""),
        ValueError(specific["intended"]),
        PoiseError(controls["unrelated"]),
        PoiseError(controls["contextual_unrelated"]),
        PoiseError(controls["generic_unrelated"]),
        PoiseError(specific["wrong_scenario"]),
        PoiseError(specific["intended"].split(";")[0] + "; cancel another task."),
        PoiseError(controls["unchanged_basis"]),
        PoiseError(specific["contextual_initialization"]),
        PoiseError(specific["contextual_requirement"]),
        PoiseError(specific["split_unrelated_change"]),
        PoiseError(controls["split_basis_change"]),
        PoiseError(specific["intended"].split(";")[0] + "; do not repeat the saved integrate request."),
    ]
    for error in invalid:
        with pytest.raises(AssertionError):
            assert_relevant_refusal({"refusal": error}, cause)
    return {"accepted": 3, "rejected": len(invalid)}


def scenario(project, mode="replay"):
    tools, _, source = prepare_completed_task(project, source_change)
    packet = request("integrate", integration_input(project, source))
    result = tools.invoke(packet) if mode == "replay" else None
    writer = WorkTools(WorkPoise(project["config_path"], "independent-writer"))
    predecessor = WorkPoise(project["config_path"], "predecessor")
    replacement = WorkPoise(project["config_path"], "replacement-owner")
    fixture = inputs()
    created = writer.invoke(fixture["create"])
    edit = deepcopy(fixture["edit"])
    edit["input"]["expected_revision"] = created["revision"]
    with tools.runtime.store.transaction() as db:
        db.execute("UPDATE sessions SET task_id=NULL WHERE task_id=?", ("T1",))
        inserted = db.execute(
            "INSERT INTO sessions(id,task_id) VALUES(?,?) "
            "ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id",
            ("predecessor", "T1"),
        )
        assert inserted.rowcount == 1
        db.execute(
            "INSERT INTO sessions(id,task_id) VALUES(?,?) ON CONFLICT(id) DO NOTHING",
            ("replacement-owner", None),
        )
    return tools, packet, result, writer, edit, predecessor, replacement


def rows(runtime):
    """Read independently, without a writer transaction or a production serializer."""
    with runtime.store.database.read_transaction() as db:
        task = tuple(db.execute(
            "SELECT status,version,claimed_by,current_submission_id,metadata "
            "FROM tasks WHERE id=?", ("T1",)).fetchone())
        execution = tuple(db.execute(
            "SELECT version,data FROM task_execution WHERE task_id=?", ("T1",)).fetchone())
        bindings = dict(db.execute(
            "SELECT id,task_id FROM sessions WHERE id IN (?,?) ORDER BY id",
            ("predecessor", "replacement-owner")))
        audit = [tuple(row) for row in db.execute(
            "SELECT session_id,task_id,data FROM journal WHERE event=? ORDER BY seq",
            ("ownership.integrated_worktree_reconciled",))]
    return task, execution, bindings, audit


def pause_and_change(project, monkeypatch, tools, packet, change, *, probe_lock=False):
    """Pause a completed real external proof; always stop both independent workers."""
    entered, resume = Event(), Event()
    observed = {}
    original = RuntimeResultIntegration._verify_terminal_cleanup
    # Deadlines bound broken arrangements, not the required performance of the product.
    deadline = 3 * tools.runtime.cfg["limits"]["lock_seconds"]

    def paused(self, run, repository, execution):
        original(self, run, repository, execution)
        observed["lock"] = self._publication_lock()
        entered.set()
        if not resume.wait(3 * deadline):
            raise AssertionError("terminal proof coordinator did not release its worker")

    def attempt():
        try:
            return True, change()
        except LockUnavailable as exc:
            return False, exc

    monkeypatch.setattr(RuntimeResultIntegration, "_verify_terminal_cleanup", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        integration = pool.submit(tools.invoke, packet)
        mutation = None
        try:
            assert entered.wait(deadline), "real terminal proof did not reach its pause"
            if probe_lock:
                with observed["lock"].open("a+b") as handle:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            mutation = pool.submit(attempt)
            committed, changed = mutation.result(timeout=deadline)
            if committed:
                observed["after_change"] = rows(tools.runtime)
            observed["change"] = changed
            observed["committed_before_resume"] = committed and not resume.is_set()
        finally:
            resume.set()
            if mutation is not None:
                mutation.result(timeout=deadline)
            try:
                observed["result"] = integration.result(timeout=deadline)
            except PoiseError as exc:
                observed["refusal"] = exc
    monkeypatch.setattr(RuntimeResultIntegration, "_verify_terminal_cleanup", original)
    if probe_lock:
        with observed["lock"].open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    assert observed["committed_before_resume"], (
        "independent change was not committed before terminal proof continued"
    )
    return observed


@pytest.mark.parametrize("mode", ["completion", "replay"])
def test_unrelated_task_edit_commits_during_terminal_proof(project, monkeypatch, mode):
    tools, packet, result, writer, edit, _, _ = scenario(project, mode)
    before = rows(tools.runtime)

    def edit_and_read_back():
        response = writer.invoke(edit)
        assert response["status"] == "newborn"
        assert response["revision"] == edit["input"]["expected_revision"] + 1
        assert response["draft"]["goal"] == "Независимое сохранение завершено."
        with writer.runtime.store.database.read_transaction() as db:
            row = db.execute(
                "SELECT claimed_by,metadata FROM tasks WHERE id=?", ("T2",)).fetchone()
        assert row["claimed_by"] == "independent-writer"
        assert json.loads(row["metadata"])["newborn"]["draft"]["goal"] == (
            "Независимое сохранение завершено."
        )
        return response

    observed = pause_and_change(
        project, monkeypatch, tools, packet, edit_and_read_back, probe_lock=True,
    )
    assert "refusal" not in observed
    assert observed["result"]["status"] == "integrated"
    after = rows(tools.runtime)
    assert after[0] == before[0]
    if mode == "replay":
        assert observed["result"] == {**result, "replayed": True}
        assert after[1] == before[1]
    assert after[2] == {"predecessor": None, "replacement-owner": None}
    assert len(after[3]) == 1
    actor, task_id, data = after[3][0]
    assert actor == tools.runtime.session
    assert task_id == "T1"
    proof = json.loads(data)
    assert proof["released_owner"] == "predecessor"
    assert proof["request_id"] == "integrate-1"
    assert proof["accepted_commit"] == packet["input"]["expected_source_commit"]
    assert proof["published_commit"] == observed["result"]["target_after"]
    replay = tools.invoke(packet)
    assert replay == {**observed["result"], "replayed": True}
    assert rows(tools.runtime) == after
    # Exact edit retry keeps the already committed value and original revision.
    assert writer.invoke(edit) == {**observed["change"], "replayed": True}


def test_terminal_proof_preserves_moved_independent_binding(project, monkeypatch):
    tools, packet, result, _, _, predecessor, _ = scenario(project)
    before = rows(tools.runtime)

    def move():
        predecessor.ownership.acquire_worktree("T2")

    observed = pause_and_change(project, monkeypatch, tools, packet, move)
    assert "refusal" not in observed
    assert observed["result"] == {**result, "replayed": True}
    after = rows(tools.runtime)
    assert after[:2] == before[:2]
    assert after[2] == {"predecessor": "T2", "replacement-owner": None}
    assert after[3] == []


def test_terminal_proof_rejects_replaced_dependent_owner(project, monkeypatch):
    check_refusal_oracle("owner")
    tools, packet, _, _, _, predecessor, replacement = scenario(project)

    def replace():
        predecessor.ownership.acquire_worktree("T2")
        replacement.ownership.acquire_worktree("T1")

    observed = pause_and_change(project, monkeypatch, tools, packet, replace)
    assert_relevant_refusal(observed, "owner")
    after = rows(tools.runtime)
    assert after == observed["after_change"]
    assert after[2] == {"predecessor": "T2", "replacement-owner": "T1"}
    assert after[3] == []


@pytest.mark.parametrize("drift", ["claimed", "status", "pending", "report", "independent"])
def test_terminal_proof_rechecks_related_state(project, monkeypatch, drift):
    if drift != "independent":
        check_refusal_oracle(drift)
    tools, packet, result, _, _, _, _ = scenario(project)

    def inject_related_state():
        # Fault injection is confined to this pytest-owned DB; no live Task data is edited.
        with tools.runtime.store.transaction() as db:
            if drift == "claimed":
                db.execute("UPDATE tasks SET claimed_by=?,version=version+1 WHERE id=?",
                           ("foreign-worker", "T1"))
            elif drift == "status":
                db.execute("UPDATE tasks SET status=?,version=version+1 WHERE id=?",
                           ("active", "T1"))
            elif drift == "independent":
                raw = db.execute("SELECT metadata FROM tasks WHERE id=?", ("T1",)).fetchone()[0]
                metadata = json.loads(raw)
                metadata["workflow"]["worktree_required"] = False
                db.execute("UPDATE tasks SET metadata=?,version=version+1 WHERE id=?",
                           (json.dumps(metadata), "T1"))
            else:
                raw = db.execute("SELECT data FROM task_execution WHERE task_id=?", ("T1",)).fetchone()[0]
                execution = json.loads(raw)
                if drift == "pending":
                    execution["pending"]["publication"] = None
                else:
                    execution["last_report"]["commit"] = "0" * 40
                db.execute("UPDATE task_execution SET data=?,version=version+1 WHERE task_id=?",
                           (json.dumps(execution), "T1"))

    observed = pause_and_change(project, monkeypatch, tools, packet, inject_related_state)
    if drift == "independent":
        assert "refusal" not in observed
        assert observed["result"] == {**result, "replayed": True}
    else:
        assert_relevant_refusal(observed, drift)
    after = rows(tools.runtime)
    assert after == observed["after_change"]
    assert after[2] == {"predecessor": "T1", "replacement-owner": None}
    assert after[3] == []
