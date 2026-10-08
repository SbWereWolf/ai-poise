"""Committed-store preservation and observed configuration facts at real boundaries."""
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from poise.common import PoiseError, load_config
from poise.infrastructure.project_availability import ReadOnlyProjectAvailability
from tests.conftest import write_json
from .test_project_preflight import case, expected, fixture, invoke, request, snapshot
from .test_preflight_store_preservation import create_available, selected_stores


@pytest.fixture
def store(case):
    selected_stores(case)
    database = Path(case[3]['task_db'])
    # Close every preparation connection explicitly before filesystem admission.
    with closing(sqlite3.connect(database)) as db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone() == ('delete',)
        assert db.execute('SELECT claimed_by FROM tasks WHERE id=?',
                          ('EXISTS-HERE',)).fetchone() == ('foreign-owner',)
    return case


def standalone_refusal(case):
    """Upstream observation precedes composition, with independent preservation."""
    before = snapshot(case[0]['root'].parent)
    failure = None
    try:
        ReadOnlyProjectAvailability().startable(
            {'project': 'demo', 'config_path': case[3]['config']})
    except PoiseError as exc:
        failure = exc
    finally:
        assert snapshot(case[0]['root'].parent) == before
    assert failure is not None, 'Unsafe store was accepted instead of protectively refused'
    assert str(failure)
    return str(failure)


def task_refusal(case, identifier, reason):
    body = request(case)
    body['task_id'] = identifier
    wanted = expected(case, status='rejected', ready=False, reason=reason)
    wanted['context']['requested_task_id'] = identifier
    wanted['checks'] = fixture('checks-task-refused.json', {'cause': reason})
    assert invoke(case, body) == (23, wanted)


@pytest.fixture(params=['without-shm', 'with-shm', 'without-sidecars'])
def wal_store(store, request):
    case = store
    path = Path(case[3]['task_db'])
    variant = request.param
    with closing(sqlite3.connect(path)) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        writer.execute('PRAGMA wal_autocheckpoint=0')
        if variant != 'without-sidecars':
            writer.execute('BEGIN')
            assert writer.execute('SELECT id FROM tasks WHERE id=?',
                                  ('EXISTS-HERE',)).fetchone() == ('EXISTS-HERE',)
            before = path.read_bytes()
            create_available(case[0], case[0]['config_path'], 'WAL-ONLY')
            with closing(sqlite3.connect(path)) as observer:
                assert observer.execute('SELECT id FROM tasks WHERE id=?',
                                        ('WAL-ONLY',)).fetchone() == ('WAL-ONLY',)
            assert path.read_bytes() == before, 'WAL-only Task must not reach main file'
            captured = {suffix: Path(str(path) + suffix).read_bytes()
                        for suffix in ('', '-wal', '-shm')}
            assert captured['-wal'] and captured['-shm']
            print(json.dumps({'fixture': variant, 'wal_only_task': 'WAL-ONLY',
                              'main_unchanged': True, 'committed_row_visible': True,
                              'wal_bytes': len(captured['-wal']),
                              'shm_bytes': len(captured['-shm'])}))
            writer.rollback()
        else:
            assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    # Restore captured fixture bytes after SQLite's own close-time checkpoint.
    if variant != 'without-sidecars':
        for suffix, data in captured.items():
            target = Path(str(path) + suffix)
            if suffix == '-shm' and variant == 'without-shm':
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(data)
    else:
        assert not Path(str(path) + '-wal').exists()
        assert not Path(str(path) + '-shm').exists()
    assert path.read_bytes()[18:20] == b'\x02\x02'
    return case, variant


def test_wal_journal_refusal_preserves_committed_store(wal_store):
    case, variant = wal_store
    reason = standalone_refusal(case)
    assert 'wal' in reason.lower(), 'Protective refusal must identify the WAL state'
    task_refusal(case, 'EXISTS-HERE' if variant == 'without-sidecars' else 'WAL-ONLY', reason)


BAD_VALUES = [
    ('missing', None),
    ('null', None),
    ('integer', 5),
    ('boolean', True),
    ('list', []),
    ('object', {}),
    ('float', 3.2),
    ('empty', ''),
    ('whitespace', '   '),
    ('nul', '\x00'),
]


@pytest.fixture(params=[(key, name, value) for key in ('database', 'lock')
                       for name, value in BAD_VALUES],
                ids=[f'{key}-{name}' for key in ('database', 'lock')
                     for name, _ in BAD_VALUES])
def malformed_store(store, request):
    case = store
    key, name, value = request.param
    cfg = case[2]
    if name == 'missing':
        del cfg['paths'][key]
    else:
        cfg['paths'][key] = value
    write_json(case[0]['config_path'], cfg)
    return case, key


def forbid_later_owners(monkeypatch):
    from poise.infrastructure.project_preflight import FileProjectPreflight
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('A later owner was called after configuration refusal')
    monkeypatch.setattr(FileProjectPreflight, 'probe_git', forbidden)
    monkeypatch.setattr(FileProjectPreflight, 'require_task', forbidden)
    return calls


def test_malformed_task_storage_paths_retain_known_context(malformed_store, monkeypatch):
    case, key = malformed_store
    calls = forbid_later_owners(monkeypatch)
    before = snapshot(case[0]['root'].parent)
    failure = None
    try:
        load_config(case[0]['config_path'])
    except Exception as exc:
        # The validator is the subject: an unexpected exception is a failed
        # classification assertion, not a fixture or pytest infrastructure error.
        failure = exc
    assert snapshot(case[0]['root'].parent) == before
    assert isinstance(failure, PoiseError), repr(failure)
    reason = str(failure)
    assert 'paths' in reason and key in reason
    wanted = expected(case, status='rejected', ready=False, reason=reason)
    wanted['context']['task_database' if key == 'database' else 'task_lock'] = None
    wanted['checks'] = fixture('checks-configuration-refused.json', {'cause': reason})
    assert invoke(case, request(case)) == (23, wanted)
    assert calls == []


def test_unexpected_validator_failure_retains_observed_context(store, monkeypatch):
    import poise.infrastructure.project_preflight as subject
    calls = forbid_later_owners(monkeypatch)
    def unexpected(path, manifest):
        assert manifest['project'] == 'demo' and str(path) == store[3]['config']
        raise RuntimeError('injected unexpected configuration validator failure')
    monkeypatch.setattr(subject, 'load_config_document', unexpected)
    reason = 'RuntimeError: injected unexpected configuration validator failure'
    wanted = expected(store, status='checked', ready=False, reason=reason)
    wanted['checks'] = fixture('checks-configuration-unknown.json', {'cause': reason})
    assert invoke(store, request(store)) == (23, wanted)
    assert calls == []


@pytest.fixture(params=['other-process', 'same-process', 'other-process-after-descriptor-close'])
def guarded_store(store, request):
    return store, request.param


WRITER_SCRIPT = '''
from contextlib import closing
import json, sqlite3, sys
with closing(sqlite3.connect(sys.argv[1], timeout=0)) as db:
    try:
        db.execute('PRAGMA journal_mode=WAL').fetchone()
    except sqlite3.OperationalError as exc:
        print(json.dumps({'sqlite_errorcode': exc.sqlite_errorcode}))
    else:
        print(json.dumps({'sqlite_errorcode': None}))
'''


def test_reader_protection_blocks_journal_transition(guarded_store):
    case, variant = guarded_store
    path = Path(case[3]['task_db'])
    before = snapshot(case[0]['root'].parent)
    outcomes = []
    def consume(reader, state):
        if variant == 'other-process-after-descriptor-close':
            descriptor = os.open(path, os.O_RDONLY)
            os.close(descriptor)
        if variant == 'same-process':
            with closing(sqlite3.connect(path, timeout=0)) as writer:
                try:
                    writer.execute('PRAGMA journal_mode=WAL').fetchone()
                except sqlite3.OperationalError as exc:
                    outcomes.append(exc.sqlite_errorcode)
                else:
                    outcomes.append(None)
        else:
            result = subprocess.run([sys.executable, '-B', '-c', WRITER_SCRIPT, str(path)],
                                    capture_output=True, text=True)
            print(json.dumps({'writer': variant, 'exit_code': result.returncode,
                              'stdout': result.stdout, 'stderr': result.stderr}))
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert result.stderr == ''
            assert result.stdout == '{"sqlite_errorcode": 5}\n'
            outcomes.append(json.loads(result.stdout)['sqlite_errorcode'])
        assert reader.execute('SELECT id FROM tasks WHERE id=?',
                              ('EXISTS-HERE',)).fetchone()[0] == 'EXISTS-HERE'
    try:
        ReadOnlyProjectAvailability._with_store(case[0]['root'], case[2], consume)
    finally:
        # Still check side effects if the controlled writer unexpectedly succeeds.
        assert snapshot(case[0]['root'].parent) == before
    assert outcomes == [5]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        assert db.execute('SELECT claimed_by FROM tasks WHERE id=?',
                          ('EXISTS-HERE',)).fetchone() == ('foreign-owner',)
    assert snapshot(case[0]['root'].parent) == before


HOT_WRITER_SCRIPT = '''
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_size=2')
db.execute('PRAGMA cache_spill=ON')
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE tasks SET claimed_by=? WHERE id=?', ('uncommitted-hot', 'EXISTS-HERE'))
db.execute('UPDATE fixture_payload SET payload=?', (b'B' * 4096,))
os._exit(0)
'''


@pytest.fixture
def hot_store(store):
    path = Path(store[3]['task_db'])
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE fixture_payload(id INTEGER PRIMARY KEY,payload BLOB)')
        db.executemany('INSERT INTO fixture_payload(id,payload) VALUES(?,?)',
                       [(number, b'A' * 4096) for number in range(128)])
        db.commit()
    baseline = path.read_bytes()
    result = subprocess.run([sys.executable, '-B', '-c', HOT_WRITER_SCRIPT, str(path)],
                            capture_output=True, text=True)
    assert (result.returncode, result.stdout, result.stderr) == (0, '', '')
    dirty = path.read_bytes()
    assert dirty != baseline
    with closing(sqlite3.connect(':memory:')) as raw:
        raw.deserialize(dirty)
        assert raw.execute('SELECT claimed_by FROM tasks WHERE id=?',
                           ('EXISTS-HERE',)).fetchone() == ('uncommitted-hot',)
    journal = Path(str(path) + '-journal')
    data = journal.read_bytes()
    assert len(data) > 512
    assert data[:8] == b'\xd9\xd5\x05\xf9\x20\xa1\x63\xd7'
    assert not Path(str(path) + '-wal').exists()
    assert not Path(str(path) + '-shm').exists()
    # Actual RESERVED byte admission: no live fixture writer owns this byte.
    with path.open('rb') as original:
        fcntl.lockf(original, fcntl.LOCK_SH | fcntl.LOCK_NB, 1, 0x40000001, os.SEEK_SET)
        fcntl.lockf(original, fcntl.LOCK_UN, 1, 0x40000001, os.SEEK_SET)
    copy = store[0]['root'].parent / 'recovery-proof'
    copy.mkdir()
    copied = copy / 'copy.sqlite'
    shutil.copyfile(path, copied)
    shutil.copyfile(journal, Path(str(copied) + '-journal'))
    with closing(sqlite3.connect(copied)) as recovered:
        assert recovered.execute('SELECT claimed_by FROM tasks WHERE id=?',
                                 ('EXISTS-HERE',)).fetchone() == ('foreign-owner',)
    assert path.read_bytes() == dirty and journal.read_bytes() == data
    print(json.dumps({'fixture': 'hot-rollback', 'child_exit_code': result.returncode,
                      'main_spilled': True, 'original_marker': 'uncommitted-hot',
                      'copy_recovered_marker': 'foreign-owner',
                      'valid_journal_magic': True, 'journal_bytes': len(data),
                      'reserved_lock_released': True, 'wal_absent': True,
                      'shm_absent': True, 'original_preserved_during_copy_recovery': True}))
    return store


def test_hot_rollback_journal_refuses_without_recovery(hot_store):
    reason = standalone_refusal(hot_store)
    assert any(word in reason.lower() for word in ('journal', 'recovery', 'readonly', 'read-only'))
    task_refusal(hot_store, 'EXISTS-HERE', reason)
