"""Test-owned standalone state scenarios with independent input and SQL oracles."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from generic_checks import APP, LOG, PY, bind, process
from state_sql import SQL_LOG, connect, read, seed, snapshot, verify_schema


TESTS = Path(__file__).parent
FIX = TESTS / 'fixtures/state'
SCHEMA = read(FIX / 'schema.json')
FACTS = read(FIX / 'facts.json')


class Installation:
    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='t060-state-')
        self.root = Path(self.temporary.name)
        self.install = self.root / 'standalone app'
        shutil.copytree(APP, self.install,
                        ignore=shutil.ignore_patterns('tests', 'docs', 'fixtures', 'config', '__pycache__'))
        self.cwd = self.root / 'caller project'
        self.cwd.mkdir()
        self.config_path = self.root / 'config α/state.json'
        self.config_path.parent.mkdir()
        self.database = self.root / 'state α/sessions.sqlite'
        self.config = bind(read(FIX / 'config.json'), {'@DATABASE@': str(self.database)})
        return self

    def __exit__(self, *args):
        self.temporary.cleanup()

    def call(self, operation, *, raw=None, cwd=None):
        config_bytes = (json.dumps(self.config, ensure_ascii=False, indent=2) + '\n').encode()
        self.config_path.write_bytes(config_bytes)
        packet = {'schema': 'poise-state/v1', 'operation': operation, 'input': {}}
        child, record = process([PY, '-I', '-S', '-B', self.install / 'bin/poise-state',
                                 '--config', self.config_path], packet, raw=raw,
                                cwd=cwd or self.cwd,
                                environment={'PATH': str(Path(PY).parent) + ':/usr/bin:/bin',
                                             'LANG': 'C.UTF-8'})
        record['config_raw'] = config_bytes.decode()
        record['database_exists_after'] = self.database.exists()
        assert self.config_path.read_bytes() == config_bytes, 'Producer changed raw configuration'
        try:
            body = json.loads(child.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError('Public state CLI returned no valid JSON envelope; full IO retained') from exc
        record['decoded'] = body
        assert child.stderr == '' and child.stdout.endswith('\n'), record
        assert set(body) == {'schema', 'status', 'result', 'error', 'evidence'}, body
        assert body['schema'] == 'poise-state/v1' and body['evidence'] == [], body
        exits = {'ok': 0, 'unavailable': 1, 'rejected': 2}
        assert body['status'] in exits and child.returncode == exits[body['status']], record
        assert body['result'] is None or isinstance(body['result'], dict), body
        if body['status'] == 'ok':
            assert body['error'] is None and isinstance(body['result'], dict), body
            result = body['result']
            assert set(result) == {'database', 'version', 'applied_versions'}, body
            assert isinstance(result['database'], str) and Path(result['database']).is_absolute(), body
            assert type(result['version']) is int, body
            assert isinstance(result['applied_versions'], list), body
            assert all(type(value) is int for value in result['applied_versions']), body
        else:
            assert isinstance(body['error'], dict), body
            assert set(body['error']) == {'code', 'details', 'action'}, body
            assert isinstance(body['error']['details'], dict), body
            if 'version' in body['error']['details']:
                assert type(body['error']['details']['version']) is int, body
        return child.returncode, body

    def ok(self, operation, applied, *, raw=None, cwd=None):
        code, body = self.call(operation, raw=raw, cwd=cwd)
        expected = bind(read(FIX / 'success.json'),
                        {'@DATABASE@': str(self.database), '@APPLIED@': applied})
        assert code == 0 and body == expected, (body, expected)
        return body

    def error(self, code, body, name, *, path=None):
        expected = bind(read(FIX / 'errors.json')[name],
                        {'@DATABASE@': str(self.database), '@PATH@': path})
        status = 'rejected' if name in ('invalid_config', 'invalid_json', 'invalid_request', 'packet_limit') else 'unavailable'
        assert code == (2 if status == 'rejected' else 1) and body['status'] == status, body
        assert body['result'] is None and body['error'] == expected and body['evidence'] == [], body

    def empty_zero(self):
        self.database.parent.mkdir()
        with closing(connect(self.database)) as db:
            db.execute('PRAGMA user_version=0')

    def facts(self):
        with closing(connect(self.database, readonly=True)) as db:
            return snapshot(db, SCHEMA)

    def populate(self):
        with closing(connect(self.database)) as db:
            seed(db, SCHEMA, FACTS)
        assert self.facts() == read(FIX / 'snapshot.json')


def zero_snapshot(path):
    with closing(connect(path, readonly=True)) as db:
        result = {'version': db.execute('PRAGMA user_version').fetchone()[0],
                  'tables': [row[0] for row in db.execute(
                      'SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ? ORDER BY name',
                      ('table', 'sqlite_%'))]}
    SQL_LOG.append({'source_zero': result})
    return result


def lifecycle_checks():
    with Installation() as app:
        assert not app.database.parent.exists()
        code, body = app.call('status')
        app.error(code, body, 'state_not_found')
        assert not app.database.exists() and not app.database.parent.exists()
        app.empty_zero()
        before = zero_snapshot(app.database)
        assert before == {'version': 0, 'tables': []}
        code, body = app.call('status')
        app.error(code, body, 'state_not_prepared')
        assert zero_snapshot(app.database) == before
        app.ok('prepare', [1])
        with closing(connect(app.database, readonly=True)) as db:
            verify_schema(db, SCHEMA)
        assert app.facts() == read(FIX / 'empty-snapshot.json')
        app.ok('status', [])
        app.ok('prepare', [])
        assert app.facts() == read(FIX / 'empty-snapshot.json')


def refused_insert(db, query, values):
    refused = False
    entry = {'sql': query, 'parameters': values}
    SQL_LOG.append(entry)
    try:
        entry['rowcount'] = db.execute(query, values).rowcount
    except sqlite3.IntegrityError as exc:
        refused = True
        entry['actual_constraint_error'] = str(exc)
    assert refused, (query, values)


def fact_checks():
    with Installation() as app:
        app.ok('prepare', [1])
        app.populate()
        app.ok('prepare', [])
        app.ok('status', [])
        expected = read(FIX / 'snapshot.json')
        assert app.facts() == expected
        with closing(connect(app.database)) as db:
            # A new unbound candidate isolates caller-key uniqueness from session-owner uniqueness.
            db.execute('SAVEPOINT candidate')
            db.execute('INSERT INTO poise_sessions(id,origin,status,revision) VALUES(?,?,?,?)',
                       ('session-C', 'native', 'open', 1))
            refused_insert(db, 'INSERT INTO poise_bindings(identity_key,session_id,inventory) VALUES(?,?,?)',
                           ('caller-A', 'session-C', '[]'))
            db.execute('ROLLBACK TO candidate')
            db.execute('RELEASE candidate')
            refused_insert(db, 'INSERT INTO poise_bindings(identity_key,session_id,inventory) VALUES(?,?,?)',
                           ('caller-other', 'session-A', '[]'))
            refused_insert(db, 'INSERT INTO poise_role_assignments(session_id,role,data) VALUES(?,?,?)',
                           ('session-A', 'reviewer', FACTS['poise_role_assignments'][0][2]))
            refused_insert(db, 'INSERT INTO poise_bindings(identity_key,session_id,inventory) VALUES(?,?,?)',
                           ('caller-ghost', 'missing-session', '[]'))
            refused_insert(db, 'INSERT INTO poise_hook_events(seq,binding_id,event_id,event,data) VALUES(?,?,?,?,?)',
                           (30, 'hook-A', 'event-1', 'SessionStart', FACTS['poise_hook_events'][0][4]))
            refused_insert(db, 'INSERT INTO poise_cursors(session_id,path,data) VALUES(?,?,?)',
                           ('session-A', 'transcript.jsonl', FACTS['poise_cursors'][0][2]))
            for values in [('invalid-origin', 'unknown', 'open', 1),
                           ('invalid-status', 'native', 'pending', 1),
                           ('invalid-revision', 'native', 'open', 0)]:
                refused_insert(db, 'INSERT INTO poise_sessions(id,origin,status,revision) VALUES(?,?,?,?)', values)
        assert app.facts() == expected
        assert FACTS['poise_role_assignments'] == app.facts()['rows']['poise_role_assignments']
        # The same transcript path exists independently for both Sessions.
        assert [row[:2] for row in app.facts()['rows']['poise_cursors']] == [
            ['session-A', 'transcript.jsonl'], ['session-B', 'transcript.jsonl']]


def isolation_checks():
    with Installation() as first, Installation() as second:
        first.config['database'] = '../state α/sessions.sqlite'
        first.config['operator_note'] = {'kept': 'explicit future option'}
        sentinel = first.root / 'tasks.sqlite'
        with closing(connect(sentinel)) as db:
            db.execute('CREATE TABLE task_association(session TEXT,role TEXT)')
            db.execute('INSERT INTO task_association(session,role) VALUES(?,?)',
                       ('old-session', 'reviewer'))
        sentinel_bytes = sentinel.read_bytes()
        first.ok('prepare', [1], cwd=second.cwd)
        assert first.facts() == read(FIX / 'empty-snapshot.json')
        assert sentinel.read_bytes() == sentinel_bytes
        first.populate()
        code, body = second.call('status')
        second.error(code, body, 'state_not_found')
        second.ok('prepare', [1])
        assert second.facts() == read(FIX / 'empty-snapshot.json')
        second.populate()
        with closing(connect(second.database)) as db:
            db.execute('UPDATE poise_bindings SET inventory=? WHERE identity_key=?',
                       ('["second-installation"]', 'caller-A'))
        first.ok('status', [], cwd=second.cwd)
        second.ok('prepare', [], cwd=first.cwd)
        assert first.facts() == read(FIX / 'snapshot.json')
        observed = second.facts()
        expected = deepcopy(FACTS)
        expected['poise_bindings'][0][2] = '["second-installation"]'
        expected_snapshot = read(FIX / 'snapshot.json')
        expected_snapshot['rows'] = expected
        assert observed == expected_snapshot
        assert sentinel.read_bytes() == sentinel_bytes


def historical_checks():
    with Installation() as app:
        isolated = app.root / 'historical only'
        isolated.mkdir()
        for name in ['state_peer.py', 'state_sql.py']:
            shutil.copyfile(TESTS / name, isolated / name)
        shutil.copytree(FIX, isolated / 'fixtures')
        source = app.install / 'migrations/v1.py'
        assert source.is_file(), 'Missing historical migration artifact migrations/v1.py'
        shutil.copyfile(source, isolated / 'v1.py')
        child, record = process([PY, '-I', '-S', '-B', isolated / 'state_peer.py',
            '--mode', 'historical', '--migration', isolated / 'v1.py',
            '--fixtures', isolated / 'fixtures', '--directory', isolated], raw='', cwd=isolated)
        try:
            record['historical_report'] = json.loads(child.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError('Historical peer returned no structured report; full IO retained') from exc
        assert child.returncode == 0 and child.stderr == '', record
        result = record['historical_report']
        assert result['status'] == 'passed', result
        assert result['isolated'] is True
        assert result['before'] == result['after'] == read(FIX / 'snapshot.json')
        assert len(result['rollback']['ddl_attempts']) == 2
        assert result['rollback']['post_failure_tables'] == []
        assert result['rollback']['post_failure_version'] == 0
        assert result['rollback']['retry_version'] == 1


def boundary_checks():
    with Installation() as app:
        app.ok('prepare', [1])
        app.populate()
        before = app.facts()
        with closing(connect(app.database)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            app.ok('status', [])
            app.ok('prepare', [])
            assert app.facts() == before
            writer.execute('ROLLBACK')
        with closing(connect(app.database)) as db:
            db.execute('PRAGMA user_version=2')
        unsupported = read(FIX / 'snapshot.json')
        unsupported['version'] = 2
        assert app.facts() == unsupported
        for operation in ['status', 'prepare']:
            code, body = app.call(operation)
            app.error(code, body, 'state_incompatible')
            assert app.facts() == unsupported
    with Installation() as app:
        app.empty_zero()
        before = zero_snapshot(app.database)
        with closing(connect(app.database)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            code, body = app.call('prepare')
            app.error(code, body, 'state_busy')
            assert zero_snapshot(app.database) == before
            writer.execute('ROLLBACK')
        app.ok('prepare', [1])
    with Installation() as app:
        occupied = app.root / 'occupied'
        literal = b'Existing operator file stays intact.\n'
        occupied.write_bytes(literal)
        app.database = occupied / 'state.sqlite'
        app.config['database'] = str(app.database)
        try:
            code, body = app.call('prepare')
            assert code == 1 and body['status'] == 'unavailable'
            assert body['result'] is None and body['error']['code'] == 'state_io', body
            assert body['error']['action'] == 'configure:state', body
            reason = body['error']['details']['reason']
            assert isinstance(reason, str) and reason, body
        finally:
            assert occupied.read_bytes() == literal
    for case in read(FIX / 'config-cases.json'):
        with Installation() as app:
            target = app.config
            for part in case['path'][:-1]:
                target = target[part]
            if case['remove']:
                del target[case['path'][-1]]
            else:
                target[case['path'][-1]] = case['value']
            code, body = app.call('prepare')
            app.error(code, body, 'invalid_config', path=case['pointer'])
            assert not app.database.exists() and not app.database.parent.exists()
    with Installation() as app:
        code, body = app.call('prepare', raw='{')
        app.error(code, body, 'invalid_json')
        code, body = app.call('unknown')
        app.error(code, body, 'invalid_request', path='/operation')
        assert not app.database.exists() and not app.database.parent.exists()
    raw = '{"schema":"poise-state/v1","operation":"prepare","input":{}}\n'
    size = len(raw.encode('utf-8'))
    with Installation() as app:
        app.config['limits']['packet_bytes'] = size
        app.ok('prepare', [1], raw=raw)
        assert app.facts() == read(FIX / 'empty-snapshot.json')
    with Installation() as app:
        app.config['limits']['packet_bytes'] = size
        extra = raw[:-1] + ' \n'
        assert len(extra.encode('utf-8')) == size + 1
        code, body = app.call('prepare', raw=extra)
        app.error(code, body, 'packet_limit')
        assert not app.database.exists() and not app.database.parent.exists()


def prerequisites():
    child, record = process([PY, '-I', '-S', '-B', TESTS / 'state_peer.py',
                             '--mode', 'qualify'], raw='')
    if child.returncode or child.stderr:
        raise RuntimeError('Selected SQLite qualification failed: ' + str(record))
    value = json.loads(child.stdout)
    if not value['python'].startswith('3.13.') or value['foreign_keys'] != 1 or not all(
            value[key] for key in ['duplicate_refused', 'foreign_refused', 'ddl_rolled_back']):
        raise RuntimeError('Selected Python/SQLite cannot exercise the agreed tests')
    failed, record = process([PY, '-I', '-S', '-B', TESTS / 'state_peer.py',
                              '--mode', 'diagnostic'], raw='')
    diagnostic = json.loads(failed.stdout)
    record['diagnostic_report'] = diagnostic
    section = diagnostic.get('diagnostic', {})
    trace = section.get('trace', [])
    observation = section.get('observation', {})
    if (failed.returncode != 1 or failed.stderr or diagnostic.get('status') != 'failed'
            or diagnostic.get('error') != {'type': 'AssertionError', 'message': 'intentional diagnostic failure'}
            or section.get('rows') != [['before-failure']]
            or section.get('raised') != diagnostic.get('error')
            or observation.get('version') != 0 or not observation.get('in_transaction')
            or not any(row[0] == 'q_diagnostic' for row in observation.get('tables', []))
            or not all(any(fragment in statement for statement in trace) for fragment in
                       ['BEGIN', 'CREATE TABLE q_diagnostic', 'INSERT INTO q_diagnostic',
                        'SELECT value FROM q_diagnostic'])
            or not diagnostic.get('sql')):
        raise RuntimeError('Test peer did not retain its actual pre-cleanup failure observations')
    value['diagnostic_capture_qualified'] = True
    return value


GROUPS = [('lifecycle', lifecycle_checks), ('facts', fact_checks),
          ('isolation', isolation_checks), ('historical', historical_checks),
          ('boundary', boundary_checks)]
