"""Cancelled-first delivery through real application, SQLite and material owners."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from sprints.helpers import setup
from .helpers import arrange, delivery_packet, fresh, settle


FIXTURES = Path(__file__).parent / "fixtures"


def snapshot(tools):
    return tools.invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None,
        "feedback": None, "rework_stage": None,
    }))


def cancelled_draft(project, *, material=True):
    setup(project)
    tools = WorkTools(WorkPoise(project["config_path"], "cancelled-draft-owner"))
    born = tools.invoke(request("task", {
        "action": "create", "task_id": "T1", "sprint_id": None,
        "request_id": "create-untyped-T1",
    }))
    assert born["goal_type"] is None and born["status"] == "newborn"
    snapshot(tools)
    root = tools.runtime.state / tools.runtime.paths["standalone_tasks"] / "T1"
    if material:
        root.mkdir(parents=True, exist_ok=True)
        (root / "planning.txt").write_bytes(b"discarded plan\n")
    result = tools.invoke(request("cancel", {"reason": "Discard the untyped plan."}))
    assert result["status"] == "cancelled"
    with tools.runtime.store.transaction() as db:
        row = db.execute("SELECT status,claimed_by FROM tasks WHERE id=?", ("T1",)).fetchone()
        assert tuple(row) == ("cancelled", None)
        assert db.execute("SELECT COUNT(*) FROM content_contracts WHERE task_id=?", ("T1",)).fetchone()[0] == 0
    record = snapshot(tools)
    assert record["status"] == "cancelled"
    assert record["content"]["contracts"] == [] and record["execution"] == []
    return tools, root, record["version"]


def agreement(version):
    value = json.loads((FIXTURES / "protocol.json").read_text())["agreement"]
    value.update(request_id="agree-cancelled-T1", expected_version=version,
                 declaration={"disposition": "no_result", "outputs": []})
    return request("delivery", value)


def journal(tools):
    with tools.runtime.store.transaction() as db:
        return [tuple(row) for row in db.execute(
            "SELECT seq,data FROM journal WHERE task_id=? AND event=? ORDER BY seq",
            ("T1", "task_delivery.state"),
        )]


def cancellation_events(tools):
    with tools.runtime.store.transaction() as db:
        return [tuple(row) for row in db.execute(
            "SELECT seq,version,data FROM task_events WHERE task_id=? "
            "AND json_extract(data,'$.event')=? ORDER BY seq",
            ("T1", "newborn_cancelled"),
        )]


def test_cancelled_untyped_draft_agrees_then_retires_and_replays(project):
    tools, root, version = cancelled_draft(project)
    foreign = root.parent / "FOREIGN" / "keep.txt"
    foreign.parent.mkdir()
    foreign.write_bytes(b"foreign task\n")
    shared = root.parent / "artifacts" / "shared.txt"
    shared.parent.mkdir()
    shared.write_bytes(b"shared state\n")
    history = cancellation_events(tools)
    assert len(history) == 1
    assert json.loads(history[0][2])["reason"] == "Discard the untyped plan."
    assert journal(tools) == []
    assert (root / "planning.txt").read_bytes() == b"discarded plan\n"
    packet = agreement(version)

    result = tools.invoke(packet)

    expected = json.loads((FIXTURES / "cancelled_draft_response.json").read_text())
    assert result == expected
    with tools.runtime.store.transaction() as db:
        row = db.execute(
            "SELECT seq,json_extract(data,'$.agreement.request_id') AS request_id,"
            "json_extract(data,'$.agreement.declaration.disposition') AS disposition,"
            "json_extract(data,'$.agreement.declaration.outputs') AS declared_outputs "
            "FROM journal WHERE task_id=? AND event=? "
            "AND json_extract(data,'$.agreement.request_id')=? ORDER BY seq LIMIT 1",
            ("T1", "task_delivery.state", "agree-cancelled-T1"),
        ).fetchone()
        assert row is not None
        assert row["request_id"] == "agree-cancelled-T1"
        assert row["disposition"] == "no_result"
        assert json.loads(row["declared_outputs"]) == []
    saved = journal(tools)
    assert len(saved) == 1 and saved[0][0] == row["seq"]
    expected["replayed"] = True
    assert tools.invoke(packet) == expected
    assert journal(tools) == saved
    conflict = deepcopy(packet)
    conflict["input"]["authorization"] = "A different explicit intent."
    with pytest.raises(PoiseError, match="Delivery request identity conflicts with saved intent digest"):
        tools.invoke(conflict)
    assert journal(tools) == saved
    assert (root / "planning.txt").read_bytes() == b"discarded plan\n"

    assert settle(fresh(project))["status"] == "delivery_complete"
    assert not root.exists()
    assert foreign.read_bytes() == b"foreign task\n"
    assert shared.read_bytes() == b"shared state\n"
    assert snapshot(tools)["status"] == "cancelled"
    assert cancellation_events(tools) == history
    after = journal(tools)
    assert tools.invoke(packet) == expected
    assert journal(tools) == after
    assert not root.exists()


def test_cancelled_draft_absent_root_is_refused_without_resurrection(project):
    tools, root, version = cancelled_draft(project, material=False)
    assert not root.exists()
    with pytest.raises(PoiseError, match="Task root is not a directory|No such file"):
        tools.invoke(agreement(version))
    assert not root.exists()
    assert journal(tools) == []


@pytest.mark.parametrize("fault", ["version", "owner", "missing", "ambiguous"])
def test_final_transaction_rechecks_task_metadata(project, monkeypatch, fault):
    tools, root, source, _ = arrange(project)
    packet = delivery_packet(tools, {"disposition": "no_result", "outputs": []})
    prepare = tools.runtime.delivery_effects.prepare
    database = tools.runtime.store.database.path
    original_version = packet["input"]["expected_version"]

    def changed_after_preparation(record, args):
        prepared = prepare(record, args)
        # Explicit test-only corruption/race in a disposable DB, never live state.
        # Raw sqlite disables no production guard and avoids public reentry locks.
        with sqlite3.connect(database) as db:
            if fault == "version":
                db.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("T1",))
            elif fault == "owner":
                db.execute("INSERT INTO sessions(id,task_id) VALUES(?,?)", ("race-owner", None))
                db.execute("UPDATE tasks SET claimed_by=? WHERE id=?", ("race-owner", "T1"))
            elif fault == "missing":
                # The fixture retains orphan history to expose a missing Task read.
                db.execute("DELETE FROM tasks WHERE id=?", ("T1",))
            else:
                db.execute("DROP INDEX tasks_single_claimant")
                db.execute("DROP INDEX sessions_single_worktree_owner")
                db.execute("INSERT INTO sessions(id,task_id) VALUES(?,?)", ("legacy-second", "T1"))
                db.execute("PRAGMA user_version=12")
        return prepared

    monkeypatch.setattr(tools.runtime.delivery_effects, "prepare", changed_after_preparation)
    message = json.loads((FIXTURES / "final_admission_errors.json").read_text())[fault]
    assert journal(tools) == []
    with pytest.raises(PoiseError, match=message):
        tools.invoke(packet)
    assert source.read_bytes() == b"customer result\n" and root.is_dir()
    assert journal(tools) == []
    with sqlite3.connect(database) as db:
        row = db.execute("SELECT version,claimed_by FROM tasks WHERE id=?", ("T1",)).fetchone()
        if fault == "missing":
            assert row is None
        elif fault == "version":
            assert row == (original_version + 1, "material-owner")
        elif fault == "owner":
            assert row == (original_version, "race-owner")
            assert db.execute("SELECT task_id FROM sessions WHERE id=?", ("race-owner",)).fetchone() == (None,)
        else:
            assert row == (original_version, "material-owner")
            owners = db.execute("SELECT id FROM sessions WHERE task_id=? ORDER BY id", ("T1",)).fetchall()
            assert owners == [("legacy-second",), ("material-owner",)]
            assert db.execute("PRAGMA user_version").fetchone()[0] == 12


@pytest.mark.parametrize("fault", ["missing-task", "stale-version"])
def test_cancelled_draft_initial_admission_preserves_materials(project, fault):
    tools, root, version = cancelled_draft(project)
    packet = agreement(version)
    if fault == "missing-task":
        packet["input"]["task_id"] = "MISSING"
        message = "Task is not registered"
    else:
        packet["input"]["expected_version"] = version - 1
        message = "Delivery Task version changed"
    before = cancellation_events(tools)
    with pytest.raises(PoiseError, match=message):
        tools.invoke(packet)
    assert journal(tools) == [] and cancellation_events(tools) == before
    assert (root / "planning.txt").read_bytes() == b"discarded plan\n"
