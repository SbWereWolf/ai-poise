"""Test-owned Session transport, independent fixtures and complete observations."""
from contextlib import closing
import json
from pathlib import Path
import subprocess
import time

from generic_checks import PY, bind
from state_checks import Installation as StateInstallation, SCHEMA
from state_sql import SQL_LOG, connect, read, seed


FIX = Path(__file__).parent / 'fixtures/session'
CALLS = []
OBSERVATIONS = []


def fixture(name):
    return read(FIX / name)


def request(name):
    return fixture('requests.json')[name]


def session(name='initial-native', identity='native-A'):
    return bind(fixture('sessions.json')[name], {'@SESSION@': identity})


def observe_process(argv, raw, cwd, environment):
    raw = raw.encode('utf-8') if isinstance(raw, str) else raw
    record = {'argv': list(map(str, argv)), 'input_hex': raw.hex(),
              'input': raw.decode('utf-8', errors='backslashreplace'),
              'cwd': str(cwd), 'environment': environment}
    CALLS.append(record)
    try:
        child = subprocess.run(record['argv'], input=raw, cwd=cwd, env=environment,
                               capture_output=True, check=False)
    except Exception as error:
        record['launch_error'] = type(error).__name__ + ': ' + str(error)
        raise
    capture(child, record)
    return child, record


def capture(child, record):
    record.update(exit_code=child.returncode, stdout_hex=child.stdout.hex(),
                  stderr_hex=child.stderr.hex(),
                  stdout=child.stdout.decode('utf-8', errors='backslashreplace'),
                  stderr=child.stderr.decode('utf-8', errors='backslashreplace'))


def decoded(child, record):
    assert child.stderr == b'' and child.stdout.endswith(b'\n'), record
    assert not child.stdout.endswith(b'\n\n'), record
    try:
        value = json.loads(child.stdout.decode('utf-8'))
    except (ValueError, UnicodeError) as error:
        raise AssertionError('Session response is not one UTF8 JSON object; full bytes retained') from error
    record['decoded'] = value
    assert isinstance(value, dict), value
    assert set(value) == {'schema', 'status', 'result', 'error', 'evidence'}, value
    assert value['schema'] == 'stride-poise/v1' and value['evidence'] == [], value
    exits = {'ok': 0, 'unavailable': 1, 'rejected': 2}
    assert value['status'] in exits and child.returncode == exits[value['status']], record
    if value['status'] == 'ok':
        assert value['error'] is None and isinstance(value['result'], dict), value
        subject = value['result']['session']
        assert set(subject) == {'id', 'origin', 'status', 'revision', 'role',
                                'role_assignment', 'locked'}, subject
        assert isinstance(subject['id'], str) and subject['id'], subject
        assert type(subject['revision']) is int and subject['revision'] >= 1, subject
        assert type(subject['locked']) is bool, subject
        assert subject['role'] is None or isinstance(subject['role'], str), subject
        assert subject['role_assignment'] is None or isinstance(subject['role_assignment'], dict), subject
        if subject['role_assignment'] and 'assigned_revision' in subject['role_assignment']:
            assert type(subject['role_assignment']['assigned_revision']) is int, subject
        for field in ('created', 'changed'):
            if field in value['result']:
                assert type(value['result'][field]) is bool, value
    else:
        assert value['result'] is None and isinstance(value['error'], dict), value
        assert set(value['error']) == {'code', 'details', 'action'}, value
        if 'version' in value['error']['details']:
            assert type(value['error']['details']['version']) is int, value
    return value


class Installation:
    def __enter__(self):
        self.state = StateInstallation().__enter__()
        self.root, self.install, self.database = self.state.root, self.state.install, self.state.database
        self.path = self.state.config_path.with_name('poise.json')
        self.config = bind(fixture('config.json'), {'@DATABASE@': str(self.database)})
        self.native = {'CODEX_THREAD_ID': 'native-A'}
        self.extra_environment = {}
        self.retained_inventory = {}
        return self

    def __exit__(self, *arguments):
        observation = {'before_cleanup': str(self.database), 'raised':
                       None if arguments[1] is None else type(arguments[1]).__name__ + ': ' + str(arguments[1])}
        OBSERVATIONS.append(observation)
        try:
            if self.database.exists():
                with closing(connect(self.database, readonly=True)) as db:
                    observation['version'] = db.execute('PRAGMA user_version').fetchone()[0]
                    observation['tables'] = [list(row) for row in db.execute(
                        'SELECT name,sql FROM sqlite_master WHERE type=? ORDER BY name', ('table',))]
                if set(SCHEMA) <= {row[0] for row in observation['tables']}:
                    observation['facts'] = self.facts()
            if self.config.get('caller', {}).get('mode') == 'generated':
                name = Path(self.config['caller']['binding_file'])
                path = name if name.is_absolute() else self.path.parent / name
                if path.is_file():
                    observation['binding_bytes_hex'] = path.read_bytes().hex()
        except Exception as error:
            observation['observation_error'] = type(error).__name__ + ': ' + str(error)
        finally:
            self.state.__exit__(*arguments)

    def prepare(self):
        self.state.ok('prepare', [1])

    def generated(self, name='caller.json'):
        self.config['caller'] = {'mode': 'generated', 'binding_file': name}
        self.native = {}
        return self.path.parent / name

    def write_config(self):
        raw = (json.dumps(self.config, ensure_ascii=False, indent=2) + '\n').encode()
        self.path.write_bytes(raw)
        return raw

    def environment(self):
        return {'PATH': str(Path(PY).parent) + ':/usr/bin:/bin', 'LANG': 'C.UTF-8',
                **self.native, **self.extra_environment}

    def packet(self, operation, value):
        return bind(fixture('packet.json'), {'@OPERATION@': operation, '@INPUT@': value})

    def call(self, operation, value=None, *, raw=None, cwd=None, direct=False):
        config_raw = self.write_config()
        if raw is None:
            raw = json.dumps(self.packet(operation, {} if value is None else value), ensure_ascii=False) + '\n'
        entry = self.install / 'bin/poise'
        argv = [entry] if direct else [PY, '-I', '-S', '-B', entry]
        child, record = observe_process(argv + ['--config', self.path], raw,
                                        cwd or self.state.cwd, self.environment())
        record['config_raw'] = config_raw.decode()
        assert self.path.read_bytes() == config_raw, 'Producer changed raw configuration'
        return child, record, decoded(child, record)

    def ok(self, operation, expected, **arguments):
        child, record, value = self.call(operation, **arguments)
        target = bind(fixture('success.json'), {'@RESULT@': expected})
        assert child.returncode == 0 and value == target, (record, target)
        return value['result']

    def error(self, operation, name, value=None, *, coordinates=None, **arguments):
        child, record, body = self.call(operation, value, **arguments)
        values = {'@DATABASE@': str(self.database), **(coordinates or {})}
        target = bind(fixture('errors.json')[name], values)
        assert body == target, (record, target)
        return body

    def facts(self):
        return self.state.facts()

    def execute(self, query, parameters=()):
        record = {'sql': query, 'parameters': list(parameters)}
        SQL_LOG.append(record)
        with closing(connect(self.database)) as db:
            try:
                cursor = db.execute(query, parameters)
                record['rows'] = [list(row) for row in cursor] if cursor.description else []
                record['rowcount'] = cursor.rowcount
            except Exception as error:
                record['error'] = type(error).__name__ + ': ' + str(error)
                raise
        return record['rows']

    def assert_session(self, expected):
        identity = expected['id']
        rows = self.execute('SELECT id,origin,status,revision FROM poise_sessions WHERE id=?', (identity,))
        assert rows == [[identity, expected['origin'], expected['status'], expected['revision']]], rows
        roles = self.execute('SELECT role,data FROM poise_role_assignments WHERE session_id=?', (identity,))
        if expected['role'] is None:
            assert roles == [], roles
        else:
            assert len(roles) == 1 and roles[0][0] == expected['role'], roles
            assert json.loads(roles[0][1]) == expected['role_assignment'], roles
        bindings = self.execute('SELECT identity_key,session_id,inventory FROM poise_bindings WHERE session_id=?', (identity,))
        assert len(bindings) == 1 and bindings[0][1] == identity, bindings
        assert isinstance(bindings[0][0], str) and bindings[0][0], bindings
        assert json.loads(bindings[0][2]) == self.retained_inventory.get(identity, []), bindings

    def retain(self, identity='native-A'):
        values = bind(fixture('retained.json'), {'@SESSION@': identity})
        with closing(connect(self.database)) as db:
            seed(db, SCHEMA, values)
        inventory = ['capability-keep', 'profile-keep']
        self.execute('UPDATE poise_bindings SET inventory=? WHERE session_id=?',
                     (json.dumps(inventory), identity))
        self.retained_inventory[identity] = inventory
        return {name: rows for name, rows in values.items() if rows}


def concurrent(app, operation, inputs):
    """Deliver every complete input and EOF before collecting any response."""
    config_raw = app.write_config()
    children, records = [], []
    group = {'operation': operation, 'dispatch': [], 'collection': []}
    OBSERVATIONS.append(group)
    try:
        for value in inputs:
            raw = (json.dumps(app.packet(operation, value), ensure_ascii=False) + '\n').encode()
            argv = [PY, '-I', '-S', '-B', str(app.install / 'bin/poise'), '--config', str(app.path)]
            record = {'argv': argv, 'environment': app.environment(), 'cwd': str(app.state.cwd),
                      'input': raw.decode(), 'input_hex': raw.hex()}
            CALLS.append(record)
            child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=app.state.cwd, env=app.environment())
            record['pid'] = child.pid
            children.append(child)
            records.append(record)
        for child, record in zip(children, records, strict=True):
            child.stdin.write(bytes.fromhex(record['input_hex']))
            child.stdin.flush()
            group['dispatch'].append({'pid': child.pid, 'event': 'complete_input'})
        for child in children:
            child.stdin.close()
            child.stdin = None
            group['dispatch'].append({'pid': child.pid, 'event': 'eof'})
        results = []
        for child, record in zip(children, records, strict=True):
            group['collection'].append({'pid': child.pid, 'event': 'wait_after_all_eof'})
            out, err = child.communicate()
            completed = subprocess.CompletedProcess(child.args, child.returncode, out, err)
            capture(completed, record)
            results.append((completed, record))
        assert app.path.read_bytes() == config_raw
        return [decoded(completed, record) for completed, record in results]
    finally:
        for child, record in zip(children, records):
            if child.poll() is None:
                child.kill()
            if 'exit_code' not in record:
                out, err = child.communicate()
                capture(subprocess.CompletedProcess(child.args, child.returncode, out, err), record)


def postwrite_failure(app):
    """Observe actual public rollback-journal activity under a retained reader."""
    before = app.facts()
    app.execute('PRAGMA journal_mode=DELETE')
    journal = Path(str(app.database) + '-journal')
    assert not journal.exists()
    observation = {'before': before, 'write_observation': None}
    OBSERVATIONS.append(observation)
    app.config['limits']['sqlite_timeout_seconds'] = 1
    config_raw = app.write_config()
    reader = connect(app.database)
    child = None
    try:
        reader.execute('BEGIN')
        observation['reader_rows'] = list(reader.execute('SELECT id,revision FROM poise_sessions ORDER BY id'))
        assert reader.in_transaction
        raw = (json.dumps(app.packet('session.assign_role', request('author'))) + '\n').encode()
        argv = [PY, '-I', '-S', '-B', str(app.install / 'bin/poise'), '--config', str(app.path)]
        record = {'argv': argv, 'environment': app.environment(), 'cwd': str(app.state.cwd),
                  'input': raw.decode(), 'input_hex': raw.hex()}
        CALLS.append(record)
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=app.state.cwd, env=app.environment())
        record['pid'] = child.pid
        child.stdin.write(raw)
        child.stdin.close()
        child.stdin = None
        while child.poll() is None:
            try:
                size = journal.stat().st_size
            except FileNotFoundError:
                size = 0
            if size and child.poll() is None and observation['write_observation'] is None:
                observation['write_observation'] = {'pid': child.pid, 'alive': True,
                                                     'journal': str(journal), 'size': size}
            time.sleep(0.005)
        out, err = child.communicate()
        result = subprocess.CompletedProcess(argv, child.returncode, out, err)
        capture(result, record)
        body = decoded(result, record)
        assert body == bind(fixture('errors.json')['state_busy'], {'@DATABASE@': str(app.database)}), record
        assert observation['write_observation'], 'Public operation did not show an actual write before refusal'
        assert app.path.read_bytes() == config_raw
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            if 'exit_code' not in record:
                out, err = child.communicate()
                capture(subprocess.CompletedProcess(child.args, child.returncode, out, err), record)
        if reader.in_transaction:
            reader.rollback()
        reader.close()
        try:
            observation['fresh_after_reader_release'] = app.facts()
        except Exception as error:
            observation['fresh_observation_error'] = type(error).__name__ + ': ' + str(error)
    assert observation.get('fresh_after_reader_release') == before, observation
    app.ok('session.assign_role', {'session': session('author'), 'changed': True}, value=request('author'))
    app.assert_session(session('author'))
    app.ok('session.assign_role', {'session': session('author'), 'changed': False}, value=request('author'))
