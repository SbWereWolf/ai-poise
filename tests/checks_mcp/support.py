"""Only public clients, fixture arrangements and real-process observations."""
from copy import deepcopy
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from tests.named_checks.support import ROOT, Scene, canonical, snapshot, write_json

FIXTURES = Path(__file__).parent / 'fixtures'


class McpEntryUnavailable(AssertionError):
    """Only the observed missing public entry is the initial intended RED."""


class CheckHookUnavailable(AssertionError):
    """Only the actual new builtin admission boundary is the intended RED."""


def literal(name):
    return json.loads((FIXTURES / name).read_text())


def wait_for(predicate, label, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError('F13_OBSERVATION_TIMEOUT:' + label)


class Peer:
    def __init__(self, arrangement, label, *, python=None, isolated=False, environment=None, observer=None):
        self.arrangement = arrangement
        self.directory = arrangement.root / label
        self.directory.mkdir()
        self.calls = []
        self.pending = {}
        self.received = []
        self.buffer = b''
        self.stderr_path = self.directory / 'stderr.log'
        self.stdout_path = self.directory / 'stdout.log'
        self.input_path = self.directory / 'stdin.jsonl'
        self.stderr = self.stderr_path.open('wb')
        self.stdout = self.stdout_path.open('wb')
        argv = [python or sys.executable, *(['-I'] if isolated else []), '-B', '-m', 'poise',
                'checks-mcp', '--config', str(arrangement.scene.config),
                '--checkout', str(arrangement.scene.checkout), '--policy', str(arrangement.scene.policy_path),
                '--transport-policy', str(arrangement.policy_path)]
        if observer is not None:
            argv = [python or sys.executable, '-B', str(FIXTURES / 'observed_entry.py'),
                    str(arrangement.root), observer, *argv[argv.index('checks-mcp'):]]
        environment = dict(arrangement.environment if environment is None else environment)
        if isolated:
            environment.pop('PYTHONPATH', None)
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.stderr, cwd=self.directory, env=environment,
                                        start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        write_json(self.directory / 'invocation.json', {
            'argv': argv, 'cwd': str(self.directory), 'pid': self.process.pid,
            'pythonpath': environment.get('PYTHONPATH'), 'isolated': isolated})

    def entry_failure(self):
        code = self.process.wait(timeout=5)
        self.stderr.flush()
        error = self.stderr_path.read_bytes()
        if (code == 2 and not self.stdout_path.read_bytes()
                and b'usage:' in error and b"invalid choice: 'checks-mcp'" in error):
            raise McpEntryUnavailable('F13_MCP_ENTRY_MISSING: actual CLI rejected checks-mcp')
        raise AssertionError(('F13_UNEXPECTED_PEER_EXIT', code, error, self.stdout_path.read_bytes()))

    def send(self, message):
        raw = canonical(message).encode()
        with self.input_path.open('ab') as stream:
            stream.write(raw)
        try:
            self.process.stdin.write(raw)
            self.process.stdin.flush()
        except BrokenPipeError:
            self.entry_failure()

    def request(self, identity, method, params):
        self.send({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params})

    def line(self, seconds=20):
        deadline = time.monotonic() + seconds
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            assert remaining > 0, 'F13_OBSERVATION_TIMEOUT: protocol response'
            ready = self.selector.select(remaining)
            assert ready, 'F13_OBSERVATION_TIMEOUT: protocol response'
            raw = os.read(self.process.stdout.fileno(), 65536)
            if not raw:
                self.entry_failure()
            self.stdout.write(raw)
            self.stdout.flush()
            self.buffer += raw
        line, self.buffer = self.buffer.split(b'\n', 1)
        value = json.loads(line)
        assert value['jsonrpc'] == '2.0'
        self.received.append(value)
        return value

    def response(self, identity):
        if identity in self.pending:
            return self.pending.pop(identity)
        while True:
            value = self.line()
            assert 'id' in value, ('unexpected server message', value)
            if type(value['id']) is type(identity) and value['id'] == identity:
                return value
            assert value['id'] not in self.pending, 'duplicate peer response'
            self.pending[value['id']] = value

    def initialize(self):
        self.request('initialize', 'initialize', literal('initialize.json'))
        result = self.response('initialize')['result']
        assert result['protocolVersion'] == '2025-11-25'
        assert 'tools' in result['capabilities']
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        return result

    def call(self, identity, tool, arguments):
        self.request(identity, 'tools/call', {'name': tool, 'arguments': arguments})

    def outcome(self, identity):
        response = self.response(identity)
        assert 'error' not in response, response
        result = response['result']
        assert type(result['isError']) is bool
        structured = result['structuredContent']
        assert len(result['content']) == 1 and result['content'][0]['type'] == 'text'
        assert json.loads(result['content'][0]['text']) == structured
        return result, structured

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
            raise AssertionError('F13_TEARDOWN_SERVER_FAILED_TO_FINISH')
        finally:
            self.selector.close()
            self.process.stdout.close()
            self.stdout.close()
            self.stderr.close()
        write_json(self.directory / 'terminal.json', {'pid': self.process.pid, 'exit_code': code})


class Arrangement:
    def __init__(self, project, parent, monkeypatch):
        self.scene = Scene(project, parent, monkeypatch)
        self.root = parent / 'mcp-observation'
        self.root.mkdir()
        self.events = self.root / 'child-events'
        self.events.mkdir()
        self.peers = []
        self.external = []
        self.cleanup_callbacks = []
        self.environment = dict(self.scene.general.env)
        self.environment['F07_FIXTURE_EVENTS'] = str(self.scene.events)
        self.policy_path = self.root / 'transport-policy.json'
        self.policy = literal('transport-policy.json')
        write_json(self.policy_path, self.policy)
        self.body = literal('payload.json')
        self.stdout_file = self.root / 'fixture.stdout'
        self.stderr_file = self.root / 'fixture.stderr'
        self.stdout_file.write_text(self.body['stdout'])
        self.stderr_file.write_bytes(self.body['stderr'].encode())
        # These are current public owner controls, before any new feature RED.
        self.scene.profile('f13-health-precondition')
        health_selection = self.scene.selection('f13-health-precondition')
        self.scene.save()
        code, preview, _ = self.scene.invoke(self.scene.request('preview', selection=health_selection))
        assert (code, preview['status']) == (0, 'planned'), preview
        healthy = self.scene.run(selection=health_selection, fresh=True)
        assert healthy['status'] == 'passed'
        self.health_marker_count = len(self.scene.markers())
        write_json(self.root / 'healthy-preconditions.json', {
            'native_receipt': False, 'config': str(self.scene.config),
            'preview': preview, 'run': healthy, 'markers': self.scene.markers()})
        self.child()

    def save_policy(self):
        write_json(self.policy_path, self.policy)

    def child(self, marker='subject', mode='exit', code=0, *, name='sample', descendant=False, extra=()):
        argv = [sys.executable, '-B', str(FIXTURES / 'child.py'), '--events', str(self.events),
                '--marker', marker, '--mode', mode, '--code', str(code),
                '--stdout-file', str(self.stdout_file), '--stderr-file', str(self.stderr_file), *extra]
        if descendant:
            argv.append('--descendant')
        self.scene.profile(name, marker=marker, argv=argv, require_parser_confirmation=False)
        self.scene.save()

    def peer(self, label='peer', **options):
        peer = Peer(self, label, **options)
        self.peers.append(peer)
        return peer

    def arguments(self, *, preview=False, binding=None, name='sample', **options):
        result = literal('run-arguments.json')
        result.update(binding=binding, selection=self.scene.selection(name), preview=preview)
        result['options'].update(options)
        return result

    def cancel(self, *, identity=None, preview=False, binding=None):
        result = literal('cancel-arguments.json')
        result.update(binding=binding, invocation_id=identity, preview=preview)
        return result

    def rows(self, marker='subject'):
        path = self.events / (marker + '.jsonl')
        return [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]

    def started(self, marker='subject'):
        return wait_for(lambda: next((row for row in self.rows(marker) if row['event'] == 'STARTED'), None), marker)

    def release(self, marker='subject'):
        (self.events / (marker + '.release')).write_text('release\n')

    def full_run(self, peer, identity='run', arguments=None):
        peer.call(identity, 'run', self.arguments() if arguments is None else arguments)
        envelope, body = peer.outcome(identity)
        assert set(body) == set(self.body['run_fields'])
        assert body['schema'] == 'poise-mcp-check-result-1'
        assert set(body['invocation']) == set(self.body['invocation_fields'])
        result = body['result']
        assert result['schema'] == 'poise-project-checks-result-1'
        assert result['project'] == self.scene.project['cfg']['project']
        assert result['checkout'] == str(self.scene.checkout)
        return envelope, body

    def record(self, body):
        return self.scene.record(body['result']['items'][0])

    def cancel_outcome(self, peer, request='cancel', **options):
        peer.call(request, 'cancel', self.cancel(**options))
        envelope, body = peer.outcome(request)
        assert body['schema'] == 'poise-mcp-check-cancel-result-1'
        assert set(body) == set(self.body['cancel_fields'])
        assert set(body['termination']) == set(self.body['termination_fields'])
        return envelope, body

    def spawn_cli(self, request=None):
        folder = self.root / ('cli-' + str(len(self.external)))
        folder.mkdir()
        argv = [sys.executable, '-B', '-m', 'poise', 'checks',
                '--config', str(self.scene.config), '--checkout', str(self.scene.checkout),
                '--policy', str(self.scene.policy_path), '--view', 'full',
                '--transport-policy', str(self.policy_path)]
        out, err = (folder / 'stdout.log').open('wb'), (folder / 'stderr.log').open('wb')
        process = subprocess.Popen(argv, cwd=folder, env=self.environment,
                                   stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True)
        self.external.append((process, out, err))
        body = self.scene.request(fresh=True) if request is None else request
        process.stdin.write(canonical(body).encode()); process.stdin.close()
        write_json(folder / 'invocation.json', {'argv': argv, 'pid': process.pid, 'request': body})
        return process

    def assert_streams(self, record):
        assert Path(record['stdout']['path']).read_bytes() == self.stdout_file.read_bytes()
        assert Path(record['stderr']['path']).read_bytes() == self.stderr_file.read_bytes()

    def close(self):
        failures = []
        for peer in self.peers:
            try:
                peer.close()
            except Exception as error:
                failures.append(str(error))
        for process, out, err in self.external:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                failures.append('F13_TEARDOWN_CLI_FAILED_TO_FINISH')
            finally:
                out.close(); err.close()
        for callback in self.cleanup_callbacks:
            try:
                callback()
            except Exception as error:
                failures.append(str(error))
        # Emergency test cleanup uses only our actually emitted fixture identities.
        # It never implements the product cancel API or turns failed cleanup GREEN.
        for path in self.events.glob('*.jsonl'):
            for row in [json.loads(line) for line in path.read_text().splitlines()]:
                if row['event'] != 'STARTED':
                    continue
                try:
                    os.killpg(row['pid'], 0)
                except ProcessLookupError:
                    continue
                os.killpg(row['pid'], signal.SIGKILL)
                failures.append('F13_TEARDOWN_OWNED_CHECK_GROUP_SURVIVED:' + str(row['pid']))
        assert not failures, failures
