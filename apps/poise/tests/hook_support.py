"""Independent hook client, literal oracles and pre-cleanup evidence recorder."""
from contextlib import closing
import json
from pathlib import Path
import subprocess

from generic_checks import PY, bind
from session_support import Installation as Sessions, CALLS, OBSERVATIONS, observe_process
from state_sql import connect

FIX = Path(__file__).parent / 'fixtures/hooks'
PEER = FIX / 'peer.py'
CHECKPOINT = lambda: None


def fixture(name):
    return json.loads((FIX / name).read_text())


def equal(actual, expected):
    """JSON equality with false distinct from 0, recursively."""
    assert type(actual) is type(expected), (actual, expected)
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), (actual, expected)
        for key in expected:
            equal(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected), (actual, expected)
        for left, right in zip(actual, expected, strict=True):
            equal(left, right)
    else:
        assert actual == expected, (actual, expected)


def decode(child, record):
    assert child.stderr == b'' and child.stdout.endswith(b'\n'), record
    body = json.loads(child.stdout)
    record['decoded'] = body
    assert set(body) == {'schema', 'status', 'result', 'error', 'evidence'}, body
    assert body['schema'] == 'stride-poise/v1', body
    assert child.returncode == {'ok': 0, 'unavailable': 1, 'rejected': 2}[body['status']], record
    assert isinstance(body['evidence'], list), body
    if body['status'] == 'ok':
        assert body['error'] is None and isinstance(body['result'], dict), body
    else:
        assert set(body['error']) == {'code', 'details', 'action'}, body
    return body


def expected_session(identity, *, assigned=False, origin='native'):
    return bind(fixture('session-role.json' if assigned else 'session-initial.json'),
                {'@SESSION@': identity, '@ORIGIN@': origin})


def error_body(code, details, action, *, status='unavailable', result=None, evidence=None):
    return bind(fixture('error.json'), {'@STATUS@': status, '@RESULT@': result,
                '@CODE@': code, '@DETAILS@': details, '@ACTION@': action,
                '@EVIDENCE@': [] if evidence is None else evidence})


def assert_error(body, code, details, action, **values):
    equal(body, error_body(code, details, action, **values))


class Installation(Sessions):
    def __enter__(self):
        super().__enter__()
        self.native = {}
        self.config['hooks'] = fixture('hooks-config.json')
        self.generic_path = self.path.with_name('generic.json')
        self.trace = self.root / 'effects.jsonl'
        self.pid_file = self.root / 'primary.pid'
        self.results = self.path.parent.parent / 'hook results α'
        self.retained_files = {}
        return self

    def __exit__(self, *arguments):
        record = {'hook_before_cleanup': str(self.root), 'files': self.retained_files}
        OBSERVATIONS.append(record)
        try:
            paths = [self.path, self.generic_path, self.trace, self.pid_file]
            if self.results.exists():
                paths.extend(path for path in self.results.rglob('*') if path.is_file())
            for path in paths:
                if path.is_file():
                    self.retained_files[str(path)] = {'hex': path.read_bytes().hex()}
            if self.database.is_file():
                with closing(connect(self.database, readonly=True)) as db:
                    record['version'] = db.execute('PRAGMA user_version').fetchone()[0]
                if record['version'] == 1:
                    record['facts'] = self.facts()
            CHECKPOINT()
        finally:
            super().__exit__(*arguments)

    def call(self, operation, value=None, *, raw=None, cwd=None, direct=False):
        config_raw = self.write_config()
        if raw is None:
            raw = json.dumps(self.packet(operation, {} if value is None else value), ensure_ascii=False) + '\n'
        child, record = observe_process([PY, '-I', '-S', '-B', self.install / 'bin/poise',
                                        '--config', self.path], raw,
                                        cwd or self.state.cwd, self.environment())
        record['config_raw'] = config_raw.decode()
        assert self.path.read_bytes() == config_raw
        return child, record, decode(child, record)

    def event_input(self, kind='SessionStart', identity='native-A', event_id='event-1', **changes):
        native = bind(fixture('events.json')[kind],
                      {'@SESSION@': identity, '@WORKSPACE@': str(self.state.cwd)})
        native.update(changes)
        return {'event_id': event_id, 'event': native}

    def event(self, kind='SessionStart', identity='native-A', event_id='event-1', **changes):
        return self.call('hook.event', self.event_input(kind, identity, event_id, **changes))[2]

    def assert_event(self, body, *, kind='SessionStart', identity='native-A', event_id='event-1',
                     sequence=1, replayed=False, assigned=False, source='startup', binding=None):
        assert body['status'] == 'ok', body
        result = body['result']
        actual_binding = result['binding']
        assert isinstance(actual_binding['id'], str) and actual_binding['id'], actual_binding
        if binding is None:
            binding = actual_binding['id']
        data = bind(fixture('event-data.json')[kind], {'@WORKSPACE@': str(self.state.cwd)})
        if kind == 'SessionStart':
            data['source'] = source
        native = fixture('native-responses.json')[kind]
        # Templates are literal fixture strings, substituted only by test coordinates.
        def text(value):
            if isinstance(value, str):
                return value.replace('@SESSION@', identity).replace('@WORKSPACE@', str(self.state.cwd))
            if isinstance(value, dict):
                return {key: text(item) for key, item in value.items()}
            return value
        target = bind(fixture('event-result.json'), {
            '@SESSION_VALUE@': expected_session(identity, assigned=assigned),
            '@BINDING@': binding, '@SESSION@': identity, '@EVENT_ID@': event_id,
            '@SEQUENCE@': sequence, '@KIND@': kind, '@EVENT_DATA@': data,
            '@NATIVE_RESPONSE@': text(native), '@REPLAYED@': replayed})
        # definition_id is a fixed independent fixture coordinate.
        equal(body, bind(fixture('success.json'), {'@RESULT@': target, '@EVIDENCE@': []}))
        self.assert_session(target['session'])
        rows = self.execute('SELECT session_id,data FROM poise_hook_bindings WHERE id=?', (binding,))
        assert len(rows) == 1 and rows[0][0] == identity, rows
        stored_definition = json.loads(rows[0][1])
        assert isinstance(stored_definition, dict), rows
        assert stored_definition.get('definition_id') == 'example-hooks', stored_definition
        events = self.execute('SELECT seq,event_id,event,data FROM poise_hook_events WHERE binding_id=? AND event_id=?',
                              (binding, event_id))
        assert len(events) == 1 and events[0][:3] == [sequence, event_id, kind], events
        equal(json.loads(events[0][3]), data)
        return binding

    def ensure(self, identity='native-A'):
        self.native = {'CODEX_THREAD_ID': identity}
        body = self.call('session.ensure')[2]
        assert body['status'] == 'ok', body
        return body['result']['session']

    def external(self, variant='accepted', probes=()):
        generic = fixture('generic-config.json')
        self.generic_path.write_text(json.dumps(generic, ensure_ascii=False, indent=2) + '\n')
        self.config['hooks']['generic_config'] = self.generic_path.name
        env = {'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TRACE': str(self.trace),
               'PID_FILE': str(self.pid_file), 'RESPONSES': str(FIX), 'FIXTURE_VALUE': 'explicit'}
        self.config['hooks']['external'] = {
            'argv': [PY, '-I', '-S', '-B', str(PEER), 'primary', variant],
            'cwd': str(self.state.cwd), 'environment': env}
        self.config['hooks']['probes'] = list(probes)
        return generic

    def probe(self, identity='available', mode='available', *, mcp=False, required=True):
        value = bind(fixture('mcp-probe.json' if mcp else 'probe.json'), {
            '@PROBE@': identity, '@PYTHON@': PY,
            '@GENERIC_PEER@': str(Path(__file__).parent / 'fixtures/generic/peer.py'),
            '@PEER@': str(Path(__file__).parent / 'fixtures/generic/peer.py') if mcp else str(PEER),
            '@MODE@': mode, '@TRACE@': str(self.trace), '@PID@': str(self.pid_file)})
        value['required'] = required
        return value

    def intercept(self, value=None):
        return self.call('hook.intercept', value if value is not None else {
            'workspace': str(self.state.cwd), 'request': fixture('request.json')})[2]

    def effects(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def output_snapshot(self):
        snapshot = {'exists': self.results.exists(), 'entries': {}}
        if self.results.exists():
            for path in sorted(self.results.rglob('*')):
                snapshot['entries'][str(path.relative_to(self.results))] = (
                    {'hex': path.read_bytes().hex()} if path.is_file() else {'directory': True})
        OBSERVATIONS.append({'output_root': str(self.results), 'snapshot': snapshot})
        return snapshot

    def outputs(self, body):
        """Keep every referenced complete output, including terminal exceptions."""
        assert body['evidence'], body
        files = {}
        for path in self.results.rglob('*'):
            if path.is_file():
                raw = path.read_bytes()
                files[str(path)] = raw
                self.retained_files[str(path)] = {'hex': raw.hex()}
        assert files, body
        for reference in body['evidence']:
            assert set(reference) == {'kind', 'path'}, reference
            assert reference['path'] in files, reference
            assert reference['kind'] in {'stdout', 'stderr', 'receipt', 'result'}, reference
        return files

    def assert_intercept(self, body, response, *, exit_code=0, summary=None, status='ok', code=None, session=None):
        expected = bind(fixture('intercept-result.json'), {
            '@SESSION_VALUE@': expected_session('native-A') if session is None else session,
            '@EXTERNAL@': {'exit_code': exit_code, 'response': response},
            '@PROBES@': fixture('empty-probes.json') if summary is None else summary})
        if code is None:
            equal(body, bind(fixture('success.json'), {'@RESULT@': expected, '@EVIDENCE@': body['evidence']}))
        else:
            assert_error(body, code, {'exit_code': exit_code}, 'configure:external',
                         result=expected, evidence=body['evidence'], status=status)
        return self.outputs(body)


def concurrent_events(app, value):
    """Dispatch both complete requests and EOF, then collect BOTH raw results before decoding."""
    raw_config = app.write_config()
    raw = (json.dumps(app.packet('hook.event', value), ensure_ascii=False) + '\n').encode()
    argv = [PY, '-I', '-S', '-B', str(app.install / 'bin/poise'), '--config', str(app.path)]
    children, records, completed = [], [], []
    order = {'hook_concurrency': [], 'pids': []}
    OBSERVATIONS.append(order)
    try:
        for _ in range(2):
            record = {'argv': argv, 'input_hex': raw.hex(), 'cwd': str(app.state.cwd),
                      'environment': app.environment()}
            CALLS.append(record)
            child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=app.state.cwd, env=app.environment())
            children.append(child)
            records.append(record)
            order['pids'].append(child.pid)
        for child in children:
            child.stdin.write(raw)
            child.stdin.flush()
            order['hook_concurrency'].append(['input', child.pid])
        for child in children:
            child.stdin.close()
            child.stdin = None
            order['hook_concurrency'].append(['eof', child.pid])
        for child, record in zip(children, records, strict=True):
            out, err = child.communicate()
            from session_support import capture
            result = subprocess.CompletedProcess(argv, child.returncode, out, err)
            capture(result, record)
            completed.append(result)
            order['hook_concurrency'].append(['collected', child.pid])
        assert app.path.read_bytes() == raw_config
        return [decode(child, record) for child, record in zip(completed, records, strict=True)]
    finally:
        for child, record in zip(children, records):
            if child.poll() is None:
                child.kill()
            if 'exit_code' not in record:
                from session_support import capture
                out, err = child.communicate()
                capture(subprocess.CompletedProcess(argv, child.returncode, out, err), record)
