"""Confirmed read-admission and optional-worker failure boundaries."""
import json
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
from threading import Event, Thread

import pytest

from poise.common import PoiseError
from poise.infrastructure.sqlite.database import Database


def test_transient_busy_is_resolved_before_read_body_without_replay(tmp_path, monkeypatch):
    path = tmp_path / 'state.sqlite'
    database = Database(path, tmp_path / 'state.lock', 1, 0.01, read_wait=1)
    original_connect = sqlite3.connect
    writer = original_connect(path, timeout=0, isolation_level=None, check_same_thread=False)
    writer.execute('BEGIN EXCLUSIVE')
    busy = Event()
    released = Event()
    observed = []
    body_calls = []

    class Connection:
        def __init__(self, connection):
            object.__setattr__(self, 'connection', connection)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __setattr__(self, name, value):
            setattr(self.connection, name, value)

        def execute(self, sql, *args):
            try:
                return self.connection.execute(sql, *args)
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                    observed.append({'body_entered': bool(body_calls)})
                    busy.set()
                    assert released.wait(5), 'Test-owned writer did not release its boundary'
                raise

    def connect(*args, **kwargs):
        return Connection(original_connect(*args, **kwargs))

    def release():
        if busy.wait(5):
            writer.execute('ROLLBACK')
            released.set()

    monkeypatch.setattr(sqlite3, 'connect', connect)
    thread = Thread(target=release)
    thread.start()
    try:
        try:
            with database.read_transaction() as db:
                body_calls.append('entered')
                assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        except PoiseError as error:
            pytest.fail('READ_ADMISSION_FAILED_ON_TRANSIENT_BUSY: ' + str(error))
    finally:
        thread.join(5)
        writer.close()
    assert not thread.is_alive()
    assert busy.is_set() and observed
    assert all(not observation['body_entered'] for observation in observed), \
        'READ_BODY_ENTERED_BEFORE_ADMISSION'
    assert body_calls == ['entered']


@pytest.mark.parametrize('mode', ['capture', 'dispatch'])
def test_detached_receipt_failure_never_reenters_foreground_work(tmp_path, mode):
    fixture = Path(__file__).parent / 'fixtures' / 'telemetry-receipt-failure.py'
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')}
    process = subprocess.run([sys.executable, '-B', str(fixture), str(tmp_path), mode],
                             capture_output=True, text=True, env=environment, timeout=15)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(process.stdout)
    child = int((tmp_path / 'child-pid.txt').read_text())
    try:
        descriptor = os.pidfd_open(child)
    except ProcessLookupError:
        descriptor = None
    if descriptor is not None:
        try:
            with selectors.DefaultSelector() as completed:
                completed.register(descriptor, selectors.EVENT_READ)
                assert completed.select(5), 'Test-owned detached child did not terminate'
        finally:
            os.close(descriptor)
    assert (tmp_path / 'foreground.txt').read_text().splitlines() == [str(result['parent'])], \
        'OPTIONAL_RECEIPT_FAILURE_REENTERED_FOREGROUND'
