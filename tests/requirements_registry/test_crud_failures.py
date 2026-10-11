"""Integration subject: safe CRUD failures through existing transaction/lock owners."""
from contextlib import closing, contextmanager
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
from time import monotonic

import pytest

from poise.composition import requirements_tools
from poise.interfaces import requirements_registry as adapter
from requirements_registry.crud_support import (
    FIXTURES, arrange, assert_effects, invoke, literal, snapshot,
)


FAULTS = literal("crud_faults.json")


def admitted(project, tmp_path, monkeypatch):
    database, lock, config = arrange(project, tmp_path)
    commands, settings = requirements_tools(config)
    # Construction succeeds BEFORE contention/fault injection. Do not mistake
    # constructor admission errors for the post-admission subject.
    monkeypatch.setattr(adapter, "requirements_tools", lambda path: (commands, settings))
    return database, lock, config, commands


def observe_updates(database, monkeypatch):
    calls = []
    connect = sqlite3.connect

    def connected(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.create_function("test_crud_update", 1, lambda identifier: calls.append(identifier))
        return db

    monkeypatch.setattr(sqlite3, "connect", connected)
    with closing(connected(database)) as db:
        db.execute(
            "CREATE TRIGGER count_crud_attempt AFTER UPDATE ON requirements "
            "BEGIN SELECT test_crud_update(NEW.id); END"
        )
        db.commit()
    return calls


@contextmanager
def held_outer_lock(lock, *, readiness_seconds=5, cleanup_seconds=2):
    holder = subprocess.Popen(
        [sys.executable, "-B", str(FIXTURES / "crud_lock_holder.py"), str(lock)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    ready = False
    try:
        # Read available bytes only: even a partial readiness line must not
        # turn the synchronization deadline into an unbounded readline.
        deadline = monotonic() + readiness_seconds
        acknowledgment = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(holder.stdout, selectors.EVENT_READ)
            while b"\n" not in acknowledgment:
                remaining = deadline - monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("lock holder did not announce readiness")
                chunk = os.read(holder.stdout.fileno(), 64)
                if not chunk:
                    raise AssertionError("lock holder exited before readiness")
                acknowledgment.extend(chunk)
                if len(acknowledgment) > 64:
                    raise AssertionError("unexpected lock holder acknowledgment")
        assert acknowledgment == b"locked\n"
        ready = True
        yield
    finally:
        exhausted = False
        try:
            _, errors = holder.communicate(timeout=cleanup_seconds)
        except subprocess.TimeoutExpired:
            exhausted = True
            holder.terminate()
            try:
                _, errors = holder.communicate(timeout=cleanup_seconds)
            except subprocess.TimeoutExpired:
                holder.kill()
                _, errors = holder.communicate(timeout=cleanup_seconds)
        if ready:
            if exhausted:
                raise TimeoutError("lock holder did not exit; child collected")
            assert holder.returncode == 0 and errors == b""


@pytest.mark.parametrize("boundary", ["outer", "BEGIN IMMEDIATE", "COMMIT"])
def test_post_admission_lock_exhaustion_rolls_back_without_body_replay(
    project, tmp_path, monkeypatch, boundary,
):
    database, lock, config, _ = admitted(project, tmp_path, monkeypatch)
    calls = observe_updates(database, monkeypatch)
    before = snapshot(database)
    if boundary == "outer":
        with held_outer_lock(lock):
            code, response = invoke(config, FAULTS["request"])
    else:
        with closing(sqlite3.connect(database, timeout=0, isolation_level=None)) as blocker:
            assert blocker.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
            blocker.execute("BEGIN IMMEDIATE" if boundary == "BEGIN IMMEDIATE" else "BEGIN")
            blocker.execute("SELECT revision FROM requirements_meta").fetchall()
            try:
                code, response = invoke(config, FAULTS["request"])
            finally:
                blocker.execute("ROLLBACK")
    assert response == FAULTS["failure"] and code == 2
    assert calls == (["A3"] if boundary == "COMMIT" else [])
    assert snapshot(database) == before
    # A failed, uncommitted intent can be retried after removal of its cause.
    calls.clear()
    code, response = invoke(config, FAULTS["request"])
    assert response == FAULTS["success"] and code == 0
    assert calls == ["A3"]
    assert_effects(before, snapshot(database), {
        "request": FAULTS["request"], "response": FAULTS["success"],
        "changes": FAULTS["success_changes"],
    })


@pytest.mark.parametrize("failure", ["integrity", "operational"])
def test_real_sql_failure_at_second_operation_rolls_back_every_effect(
    project, tmp_path, monkeypatch, failure,
):
    database, _, config, _ = admitted(project, tmp_path, monkeypatch)
    faults = literal("crud_sql_faults.json")
    trigger_body = (
        "SELECT RAISE(ABORT,'private integrity diagnostic');"
        if failure == "integrity" else "SELECT missing_test_function();"
    )
    with closing(sqlite3.connect(database)) as db:
        db.executescript(
            "CREATE TRIGGER reject_crud_r9 BEFORE INSERT ON requirements "
            "WHEN NEW.id='R9' BEGIN " + trigger_body + " END;"
        )
        db.commit()
    before = snapshot(database)
    code, response = invoke(config, faults["request"])
    assert response == faults[failure] and code == 2
    assert snapshot(database) == before


def test_post_admission_lock_open_permission_failure_has_safe_null_context(
    project, tmp_path, monkeypatch,
):
    database, lock, config, _ = admitted(project, tmp_path, monkeypatch)
    before = snapshot(database)
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == lock:
            raise PermissionError(f"private path must not escape: {path}")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    code, response = invoke(config, FAULTS["request"])
    assert response == FAULTS["failure"] and code == 2
    assert snapshot(database) == before


def test_confirmed_commit_cleanup_failure_retains_receipt_and_exact_replay(
    project, tmp_path, monkeypatch,
):
    database, _, config, commands = admitted(project, tmp_path, monkeypatch)
    calls = observe_updates(database, monkeypatch)
    before = snapshot(database)
    real_transaction = commands.store._transaction

    @contextmanager
    def failed_cleanup():
        with real_transaction() as connection:
            yield connection
        raise OSError("private cleanup diagnostic must not escape")

    with monkeypatch.context() as patch:
        patch.setattr(commands.store, "_transaction", failed_cleanup)
        code, response = invoke(config, FAULTS["request"])
    assert response == FAULTS["failure"] and code == 2
    after = snapshot(database)
    assert_effects(before, after, {
        "request": FAULTS["request"], "response": FAULTS["success"],
        "changes": FAULTS["success_changes"],
    })
    code, response = invoke(config, FAULTS["request"])
    assert response == FAULTS["replay"] and code == 0
    assert snapshot(database) == after
    assert calls == ["A3"]
