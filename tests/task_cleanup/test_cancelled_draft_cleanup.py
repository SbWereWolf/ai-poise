"""Real cleanup of drafts without execution, plus terminal admission guards."""
import json
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import git, track_runtime_dispatchers
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from result_integration.helpers import prepare_completed_task
from runtime_services.test_duplicate_reuse import family_result, reuse
from terminal_materials.test_cancelled_draft_agreement import cancelled_draft

from .helpers import branch_exists, prepare_cancelled_task


@pytest.fixture(autouse=True)
def close_runtime_dispatchers(monkeypatch):
    dispatchers = track_runtime_dispatchers(monkeypatch)
    try:
        yield
    finally:
        for dispatcher in dispatchers:
            dispatcher.close()


def test_cancelled_draft_without_execution_retires_materials_and_replays(project):
    tools, root, version = cancelled_draft(project)
    with tools.runtime.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM task_execution WHERE task_id='T1'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM content_contracts WHERE task_id='T1'").fetchone()[0] == 0
    nested = root / "nested" / "verification.txt"
    nested.parent.mkdir()
    nested.write_bytes(b"disposable verification receipt\n")
    unrelated = project["root"] / "unrelated-result.txt"
    unrelated.write_bytes(b"another customer's result\n")
    tools.invoke(request("delivery", {
        "action": "agree", "request_id": "cancelled-draft-no-result", "task_id": "T1",
        "expected_version": version,
        "authorization": "The customer ordered complete cleanup; this draft produced no ordered result.",
        "declaration": {"disposition": "no_result", "outputs": []},
    }))
    packet = request("cleanup", {
        "request_id": "cleanup-never-started-draft", "task_id": "T1",
        "authorization": "The customer ordered complete cleanup of this cancelled draft.",
        "commit_disposition": None,
    })

    completed = tools.invoke(packet)

    assert completed["status"] == "cleanup_complete"
    assert completed["remaining_resources"] == []
    assert not root.exists()
    assert not tools.runtime.cleanup_tools.adapter.resource_root("T1").exists()
    assert not branch_exists(project)
    assert unrelated.read_bytes() == b"another customer's result\n"
    with tools.runtime.store.transaction() as db:
        assert tuple(db.execute("SELECT status,claimed_by,version FROM tasks WHERE id='T1'").fetchone()) == (
            "cancelled", None, version,
        )
        assert db.execute("SELECT COUNT(*) FROM content_contracts WHERE task_id='T1'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM submissions WHERE task_id='T1'").fetchone()[0] == 0

    replay = tools.invoke(packet)

    assert replay["status"] == "cleanup_complete"
    assert replay["replayed"] is True
    assert replay["remaining_resources"] == []
    assert replay["history"] == completed["history"]
    assert not root.exists()
    assert not tools.runtime.cleanup_tools.adapter.resource_root("T1").exists()
    assert unrelated.read_bytes() == b"another customer's result\n"


def test_initialize_rechecks_terminal_state(project):
    tools, root, _ = cancelled_draft(project)
    pending = tools.runtime.cleanup_tools.pending(tools.runtime.cleanup_tools.prepare_terminal(
        "T1", "prepare-before-state-change", "The customer ordered draft cleanup.",
    ))
    with tools.runtime.store.transaction() as db:
        changed = db.execute("UPDATE tasks SET status='newborn',version=version+1 WHERE id='T1'")
        assert changed.rowcount == 1

    with pytest.raises(PoiseError, match="terminal cancelled Task"):
        tools.runtime.cleanup_tools.adapter.initialize("T1", pending)

    with tools.runtime.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM task_execution WHERE task_id='T1'").fetchone()[0] == 0
        assert db.execute("SELECT status FROM tasks WHERE id='T1'").fetchone()[0] == "newborn"
    assert (root / "planning.txt").read_bytes() == b"discarded plan\n"


def test_conflicting_pending_is_preserved(project):
    tools, root, _ = cancelled_draft(project)
    state = json.dumps({
        "worktree": None, "branch": None, "base": None, "attempts": 0,
        "publication": None, "pending": {"kind": "other_operation", "identity": "other-work"},
        "entry_tree": None, "last_report": None,
    }, sort_keys=True, separators=(",", ":"))
    with tools.runtime.store.transaction() as db:
        db.execute("INSERT INTO task_execution(task_id,data,version) VALUES('T1',?,0)", (state,))

    with pytest.raises(PoiseError, match="incompatible pending external operation"):
        tools.invoke(request("cleanup", {
            "request_id": "conflicting-cleanup", "task_id": "T1",
            "authorization": "The customer ordered draft cleanup.", "commit_disposition": None,
        }))

    with tools.runtime.store.transaction() as db:
        assert tuple(db.execute("SELECT data,version FROM task_execution WHERE task_id='T1'").fetchone()) == (state, 0)
    assert (root / "planning.txt").read_bytes() == b"discarded plan\n"


def test_existing_execution_missing_pending_is_not_defaulted(project):
    tools, root, _ = cancelled_draft(project)
    malformed = json.dumps({
        "worktree": None, "branch": None, "base": None, "attempts": 0,
        "publication": None, "entry_tree": None, "last_report": None,
    }, sort_keys=True, separators=(",", ":"))
    with tools.runtime.store.transaction() as db:
        db.execute("INSERT INTO task_execution(task_id,data,version) VALUES('T1',?,0)", (malformed,))

    with pytest.raises(KeyError) as failure:
        tools.runtime.cleanup_tools.adapter.load("T1")

    assert failure.value.args == ("pending",)
    with tools.runtime.store.transaction() as db:
        assert tuple(db.execute("SELECT data,version FROM task_execution WHERE task_id='T1'").fetchone()) == (malformed, 0)
    assert (root / "planning.txt").read_bytes() == b"discarded plan\n"


def test_initialize_preserves_an_existing_pending_operation(project):
    tools, worktree, _, _ = prepare_cancelled_task(project)
    pending = tools.runtime.cleanup_tools.pending(tools.runtime.cleanup_tools.prepare_terminal(
        "T1", "prepare-before-pending-change", "The customer ordered task cleanup.",
    ))
    with tools.runtime.store.unit_of_work() as uow:
        before, execution_version = uow.execution.load("T1")
        before["pending"] = {"kind": "other_operation", "identity": "concurrent-work"}
        uow.execution.save("T1", before, execution_version)

    with pytest.raises(PoiseError, match="operation"):
        tools.runtime.cleanup_tools.adapter.initialize("T1", pending)

    with tools.runtime.store.unit_of_work() as uow:
        after, _ = uow.execution.load("T1")
    assert after == before
    assert worktree.is_dir()
    assert branch_exists(project)


def test_nonterminal_draft_is_rejected(project):
    from conftest import WorkPoise
    from sprints.helpers import setup

    setup(project)
    tools = WorkTools(WorkPoise(project['config_path'], 'nonterminal-owner'))
    born = tools.invoke(request('task', {
        'action': 'create', 'task_id': 'T1', 'sprint_id': None,
        'request_id': 'create-nonterminal-draft',
    }))
    assert born['status'] == 'newborn'
    root = tools.runtime.state / tools.runtime.paths['standalone_tasks'] / 'T1'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'planning.txt').write_bytes(b'discarded plan\n')
    before = tools.runtime.task_queries.record('T1')

    with pytest.raises(PoiseError, match="terminal cancelled Task"):
        tools.runtime.cleanup_tools.adapter.load("T1")

    with tools.runtime.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM task_execution WHERE task_id='T1'").fetchone()[0] == 0
    assert tools.runtime.task_queries.record('T1') == before
    assert (root / "planning.txt").read_bytes() == b"discarded plan\n"


def test_completed_duplicate_cleanup_remains_supported(project):
    runtime, source = family_result(project, start_target=True)
    git(Path(runtime.task_queries.record("D")["worktree"]), "merge", "--ff-only", source["commit"])
    tools = WorkTools(runtime)
    assert reuse(tools)["status"] == "reuse_verified"
    assert tools.invoke(request("accept", {}))["completion_kind"] == "duplicate_reuse"
    with runtime.store.unit_of_work() as uow:
        before, execution_version = uow.execution.load("D")
        before["pending"] = None
        uow.execution.save("D", before, execution_version)
    pending = runtime.cleanup_tools.pending(runtime.cleanup_tools.prepare_terminal(
        "D", "preexisting-duplicate-cleanup", "Complete cleanup of the accepted duplicate.",
    ))

    runtime.cleanup_tools.adapter.initialize("D", pending)

    with runtime.store.unit_of_work() as uow:
        after, _ = uow.execution.load("D")
    assert after == {**before, "pending": pending}
    assert runtime.task_queries.record("D")["status"] == "completed"


def test_ordinary_completed_task_is_rejected(project):
    tools, worktree, _ = prepare_completed_task(
        project,
        lambda root: (root / "src" / "ordinary.py").write_text("VALUE = 'ordered result'\n", encoding="utf-8"),
    )

    with pytest.raises(PoiseError, match="terminal cancelled Task"):
        tools.runtime.cleanup_tools.adapter.load("T1")

    assert Path(worktree).is_dir()
    assert branch_exists(project)
