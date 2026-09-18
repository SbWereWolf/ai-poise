"""Requirements writes use one bounded SQL boundary, not body replay."""
from contextlib import closing
from threading import Barrier, Thread
from time import monotonic
import sqlite3

import pytest

from poise.infrastructure.requirements_registry import RequirementsStore
from poise.modules.foundation.errors import DomainError, PoiseError, VersionConflict
from poise.modules.requirements_registry.domain import RequirementsRegistry
from runtime_services.test_sqlite_contention import external_lock


def store(tmp_path, wait=1.0):
    return RequirementsStore(tmp_path / 'requirements.sqlite', tmp_path / 'requirements.lock', wait, 0.005)


def request():
    return {'request_id': 'once', 'expected_revision': 0, 'operations': [
        {'kind': 'put_requirement', 'requirement': {
            'id': 'SYS-1', 'level': 'system', 'status': 'current', 'text': 'Preserve data.'}},
        {'kind': 'put_requirement', 'requirement': {
            'id': 'APP-1', 'level': 'application', 'status': 'current', 'text': 'Commit once.'}},
        {'kind': 'link', 'system': 'SYS-1', 'application': 'APP-1'}]}


def state(path):
    with closing(sqlite3.connect(path)) as db:
        return '\n'.join(db.iterdump())


def count_apply(monkeypatch):
    calls = []
    original = RequirementsRegistry.apply
    def observed(self, operations, max_items):
        calls.append('apply')
        return original(self, operations, max_items)
    monkeypatch.setattr(RequirementsRegistry, 'apply', observed)
    return calls


@pytest.mark.parametrize('boundary', ['BEGIN IMMEDIATE', 'COMMIT'])
def test_requirements_wait_for_sql_boundary_without_replaying_body(tmp_path, monkeypatch, boundary):
    owner = store(tmp_path)
    calls = count_apply(monkeypatch)
    with external_lock(owner.database, boundary, monkeypatch) as observed:
        result = owner.apply(request(), max_items=10)
    assert observed == [boundary]
    assert calls == ['apply']
    assert result == {'revision': 1, 'replayed': False}
    before = state(owner.database)
    assert owner.apply(request(), max_items=10) == {'revision': 1, 'replayed': True}
    assert state(owner.database) == before
    assert calls == ['apply']
    with closing(sqlite3.connect(owner.database)) as db:
        assert db.execute('SELECT count(*) FROM requirement_requests').fetchone()[0] == 1
        assert db.execute('SELECT system_id,application_id FROM requirement_links').fetchall() == [('SYS-1','APP-1')]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('boundary', ['BEGIN IMMEDIATE', 'COMMIT'])
def test_requirements_exhausted_budget_rolls_back_all_rows(tmp_path, monkeypatch, boundary):
    owner = store(tmp_path, wait=0.06)
    before = state(owner.database)
    calls = count_apply(monkeypatch)
    with closing(sqlite3.connect(owner.database, timeout=0, isolation_level=None)) as blocker:
        blocker.execute('BEGIN IMMEDIATE' if boundary == 'BEGIN IMMEDIATE' else 'BEGIN')
        blocker.execute('SELECT revision FROM requirements_meta').fetchall()
        start = monotonic()
        with pytest.raises(PoiseError, match='SQLite.*' + boundary) as caught:
            owner.apply(request(), max_items=10)
        assert 0.05 <= monotonic() - start < 1.5
        assert str(owner.database) in str(caught.value)
        assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
        blocker.execute('ROLLBACK')
    assert calls == ([] if boundary == 'BEGIN IMMEDIATE' else ['apply'])
    assert state(owner.database) == before
    assert owner.apply(request(), max_items=10) == {'revision': 1, 'replayed': False}


def test_requirements_initializer_waits_before_schema_read(tmp_path, monkeypatch):
    path = tmp_path / 'requirements.sqlite'
    with closing(sqlite3.connect(path)):
        pass
    with external_lock(path, 'BEGIN IMMEDIATE', monkeypatch) as observed:
        owner = store(tmp_path)
    assert observed == ['BEGIN IMMEDIATE']
    assert owner.revision() == 0
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_requirements_stale_revision_is_checked_after_reservation(tmp_path, monkeypatch):
    owner = store(tmp_path)
    connect = sqlite3.connect
    observations = []
    with closing(connect(owner.database, timeout=0, isolation_level=None)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        blocker.execute('UPDATE requirements_meta SET revision=1')
        class Observe(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                try:
                    return super().execute(sql, parameters)
                except sqlite3.OperationalError as exc:
                    if getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_BUSY:
                        observations.append(sql)
                        blocker.execute('COMMIT')
                    raise
        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, 'connect', lambda *a, **k: connect(*a, **k, factory=Observe))
            with pytest.raises(VersionConflict, match='revision changed'):
                owner.apply(request(), max_items=10)
    assert observations == ['BEGIN IMMEDIATE']
    assert owner.revision() == 1
    with closing(connect(owner.database)) as db:
        assert db.execute('SELECT count(*) FROM requirement_requests').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM requirements').fetchone()[0] == 0


def test_requirements_two_initializers_share_schema_without_partial_tables(tmp_path):
    barrier = Barrier(2)
    results, errors = [], []
    def initialize():
        try:
            barrier.wait(timeout=3)
            results.append(store(tmp_path).revision())
        except BaseException as exc:
            errors.append(exc)
    workers = [Thread(target=initialize) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(4)
    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert results == [0,0]


def test_requirements_domain_and_fk_failure_leave_one_atomic_state(tmp_path, monkeypatch):
    owner = store(tmp_path)
    before = state(owner.database)
    bad = request(); bad['operations'][-1]['application'] = 'absent'
    with pytest.raises(DomainError): owner.apply(bad, max_items=10)
    assert state(owner.database) == before
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        with owner._transaction() as db:
            assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            db.execute('INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)', ('SYS-x','system','current','x'))
            db.execute('INSERT INTO requirement_links(system_id,application_id) VALUES(?,?)', ('SYS-x','absent'))
    assert state(owner.database) == before


@pytest.mark.parametrize('version', [0, 2])
def test_requirements_reject_unknown_database_without_migration(tmp_path, version):
    path = tmp_path / 'requirements.sqlite'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE foreign_data(value TEXT)')
        db.execute('INSERT INTO foreign_data(value) VALUES(?)', ('keep',))
        db.execute(f'PRAGMA user_version={version}')
        db.commit()
    before = path.read_bytes()
    with pytest.raises(PoiseError, match='Unknown|incompatible'): store(tmp_path)
    assert path.read_bytes() == before


def test_requirements_request_id_cannot_change_payload(tmp_path):
    owner = store(tmp_path)
    owner.apply(request(), max_items=10)
    before = state(owner.database)
    changed = request(); changed['operations'][0]['requirement']['text'] = 'different'
    with pytest.raises(DomainError, match='another request'): owner.apply(changed, max_items=10)
    assert state(owner.database) == before
