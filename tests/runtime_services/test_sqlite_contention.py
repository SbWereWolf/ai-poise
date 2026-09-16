"""Real independent SQLite connections; no sleeps to orchestrate lock release."""
from contextlib import closing, contextmanager
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
import sqlite3

import pytest

from poise.application.session_establishment import SessionEstablisher
from poise.common import PoiseError
from poise.infrastructure.sqlite.database import Database
from poise.infrastructure.sqlite.hook_transport import HookRegistry
from poise.infrastructure.sqlite.runtime_adapter import RuntimeRegistry
from poise.modules.session_establishment.domain import CallerIdentity


def store(tmp_path, kind='task', wait=0.12):
    path = tmp_path / 'state.sqlite'
    if kind == 'task':
        owner = Database(path, tmp_path / 'task.lock', wait, 0.005)
    else:
        owner = HookRegistry(SimpleNamespace(
            database=path, lock=tmp_path / 'hooks.lock',
            raw={'lock_seconds': wait, 'lock_poll_seconds': 0.005}))
        with owner.transaction():
            pass
    with owner.transaction() as db:
        db.execute('CREATE TABLE contention_result(value TEXT NOT NULL)')
    return owner, path


@contextmanager
def external_lock(path, boundary, monkeypatch):
    """Release only after a real SQLITE_BUSY, observed below the production policy."""
    connect = sqlite3.connect
    acquired, busy, freed = Event(), Event(), Event()
    observations, errors = [], []

    def hold():
        try:
            with closing(connect(path, timeout=0, isolation_level=None)) as db:
                db.execute('BEGIN IMMEDIATE' if boundary == 'BEGIN IMMEDIATE' else 'BEGIN')
                db.execute('SELECT name FROM sqlite_master').fetchall()
                acquired.set()
                assert busy.wait(5), 'operation never encountered the held SQLite lock'
                db.execute('ROLLBACK')
                freed.set()
        except BaseException as exc:
            errors.append(exc)
            acquired.set()
            freed.set()

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            try:
                return super().execute(sql, parameters)
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                    observations.append(sql)
                    busy.set()
                    assert freed.wait(5), 'external lock was not released'
                raise

    def observed_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=ObservedConnection)

    thread = Thread(target=hold)
    thread.start()
    assert acquired.wait(5)
    assert not errors
    monkeypatch.setattr(sqlite3, 'connect', observed_connect)
    try:
        yield observations
    finally:
        busy.set()
        thread.join(5)
        assert not thread.is_alive()
        assert not errors


@pytest.mark.parametrize('kind', ['task', 'hook'])
@pytest.mark.parametrize('boundary', ['BEGIN IMMEDIATE', 'COMMIT'])
def test_boundary_waits_for_external_connection_once(tmp_path, monkeypatch, kind, boundary):
    owner, path = store(tmp_path, kind, wait=1.0)
    calls = []
    with external_lock(path, boundary, monkeypatch) as observed:
        with owner.transaction() as db:
            calls.append('body')
            db.execute('INSERT INTO contention_result(value) VALUES(?)', ('saved',))
    assert observed == [boundary]
    assert calls == ['body']
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT value FROM contention_result').fetchall() == [('saved',)]
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_session_establishment_commit_contention_needs_no_caller_replay(tmp_path, monkeypatch):
    owner, path = store(tmp_path, wait=1.0)
    registry = RuntimeRegistry(owner)
    establisher = SessionEstablisher(registry)
    with external_lock(path, 'COMMIT', monkeypatch) as observed:
        session = establisher.establish(CallerIdentity.native('P', 'native-owner'), [])
    assert observed == ['COMMIT']
    assert session.session_id == 'native-owner'
    with owner.transaction() as db:
        assert [tuple(row) for row in db.execute(
            'SELECT session_id,inventory FROM runtime_bindings')] == [('native-owner', '[]')]
        assert db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0


@pytest.mark.parametrize('boundary', ['BEGIN IMMEDIATE', 'COMMIT'])
def test_persistent_lock_has_bounded_diagnostic_and_no_mutation(tmp_path, boundary):
    owner, path = store(tmp_path)
    with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as blocker:
        before = '\n'.join(blocker.iterdump())
        blocker.execute('BEGIN IMMEDIATE' if boundary == 'BEGIN IMMEDIATE' else 'BEGIN')
        blocker.execute('SELECT name FROM sqlite_master').fetchall()
        start = monotonic()
        with pytest.raises(PoiseError, match='SQLite.*' + boundary) as error:
            SessionEstablisher(RuntimeRegistry(owner)).establish(
                CallerIdentity.native('P', 'must-not-survive'), [])
        elapsed = monotonic() - start
        assert 0.1 <= elapsed < 2
        assert str(path) in str(error.value)
        assert 'lock_seconds' in str(error.value)
        assert isinstance(error.value.__cause__, sqlite3.OperationalError)
        blocker.execute('ROLLBACK')
        assert '\n'.join(blocker.iterdump()) == before
    # Both the SQLite lock and the external flock were released after failure.
    with owner.transaction() as db:
        assert db.execute('SELECT count(*) FROM runtime_bindings').fetchone()[0] == 0


def test_body_failure_is_not_replayed_and_rolls_back(tmp_path):
    owner, path = store(tmp_path)
    calls = []
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        with owner.transaction() as db:
            calls.append('body')
            db.execute('INSERT INTO contention_result(value) VALUES(?)', ('rollback',))
            db.execute('INSERT INTO absent(value) VALUES(1)')
    assert calls == ['body']
    with owner.transaction() as db:
        assert db.execute('SELECT count(*) FROM contention_result').fetchone()[0] == 0


def test_deferred_foreign_key_commit_error_is_not_retried(tmp_path):
    owner, path = store(tmp_path)
    with owner.transaction() as db:
        db.execute('CREATE TABLE fk_parent(id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE fk_child(id INTEGER REFERENCES fk_parent(id) DEFERRABLE INITIALLY DEFERRED)')
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        with owner.transaction() as db:
            assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            db.execute('INSERT INTO fk_child(id) VALUES(1)')
    with owner.transaction() as db:
        assert db.execute('SELECT count(*) FROM fk_child').fetchone()[0] == 0


@pytest.mark.parametrize('code', [sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY_SNAPSHOT])
def test_non_retryable_commit_error_is_preserved(tmp_path, monkeypatch, code):
    owner, path = store(tmp_path)
    connect = sqlite3.connect
    attempts = []
    error = sqlite3.OperationalError('requires another recovery, not a boundary retry')
    error.sqlite_errorcode = code

    class FailedCommit(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == 'COMMIT':
                attempts.append(sql)
                raise error
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *a, **k: connect(*a, **k, factory=FailedCommit))
        with pytest.raises(sqlite3.OperationalError) as caught:
            with owner.transaction() as db:
                db.execute('INSERT INTO contention_result(value) VALUES(?)', ('rollback',))
        assert caught.value is error
    assert attempts == ['COMMIT']
    with owner.transaction() as db:
        assert db.execute('SELECT count(*) FROM contention_result').fetchone()[0] == 0
