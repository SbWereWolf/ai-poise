"""Absent command detection must preserve arbitrary failures as failures."""
import json
from contextlib import redirect_stdout
import io
import sys
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from tests.content_reader.helpers import (
    ContentReadUnavailable, FIXTURES, Scenario, before_reader_commit, decoded,
)


def test_missing_reader_entry_is_distinguished_from_arbitrary_failure(tmp_path, monkeypatch):
    absent = (FIXTURES / 'entry-absent.stderr.txt').read_text()
    with pytest.raises(ContentReadUnavailable):
        decoded(2, '', absent)
    # Same words inside an ordinary command body are not an absent entry.
    assert decoded(2, '{"reason":"invalid policy"}', absent) == (2, {'reason': 'invalid policy'})
    for code, stdout, stderr in [(1, '', absent), (2, '', 'ModuleNotFoundError: poise'),
                                  (2, '', 'usage: content-read invalid configuration')]:
        with pytest.raises(json.JSONDecodeError):
            decoded(code, stdout, stderr)
    assert decoded(0, '{"status":"ready"}', '') == (0, {'status': 'ready'})
    # Independent reproducer: an ordinary control subprocess runs while another
    # consumer owns global stdout. No missing/future reader implementation.
    probe = Scenario.__new__(Scenario)
    probe.checkout, probe.env = tmp_path, {}
    probe.diagnostics_out, probe.diagnostics_err = io.StringIO(), io.StringIO()
    probe.argv = lambda fmt: [sys.executable, '-B', '-c', 'print("control-reset-json")']
    reader_output = io.StringIO()
    with redirect_stdout(reader_output):
        assert probe.raw({'operation': 'reset'}) == (0, 'control-reset-json\n', '')
    assert reader_output.getvalue() == ''
    assert probe.diagnostics_out.getvalue() == 'control-reset-json\n'
    assert probe.diagnostics_err.getvalue() == ''
    # Verify the test-only gate against real SQLite, independently of the
    # unavailable reader. Each supported commit spelling must pause actual DML.
    for spelling in ['method', 'sql', 'context']:
        database = tmp_path / (spelling + '.sqlite')
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE diagnostic(value INTEGER)')
        errors, completed = [], threading.Event()

        def commit():
            try:
                with sqlite3.connect(database) as db:
                    db.execute('BEGIN')
                    db.execute('SELECT value FROM diagnostic').fetchall()
                    db.commit()
                    assert not entered.is_set(), 'Read-only preparation is not registration DML'
                    db.execute('INSERT INTO diagnostic(value) VALUES(?)', (7,))
                    if spelling == 'method': db.commit()
                    elif spelling == 'sql': db.execute('COMMIT')
                completed.set()
            except BaseException as exc:
                errors.append(exc)

        with before_reader_commit(monkeypatch, SimpleNamespace(db=database)) as (entered, release):
            thread = threading.Thread(target=commit, name='reader-interleaving')
            thread.start()
            try:
                assert entered.wait(10) and not completed.is_set()
            finally:
                release.set(); thread.join(10)
            assert not thread.is_alive() and not errors and completed.is_set()
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT value FROM diagnostic').fetchall() == [(7,)]
