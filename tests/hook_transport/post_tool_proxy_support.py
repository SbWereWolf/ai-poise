"""Native fixture arrangement and actual process observations; no proxy logic."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

from poise.interfaces.hook_transport import execute
from .initial_proxy_support import invoke, write
from .pre_tool_proxy_support import PreToolScene

FIXTURES = Path(__file__).parent / 'fixtures' / 'post_tool_proxy'


class PostToolProxyUnavailable(AssertionError):
    """Only the exact absent public operation, never an environment failure."""


def require_proxy_operation(code, body):
    if code == 2 and body.get('reason') == 'Unknown runtime-config operation':
        raise PostToolProxyUnavailable('POST_TOOL_PROXY_PUBLICATION_MISSING: post-tool-use-proxy')


class PostToolScene(PreToolScene):
    def __init__(self, project, parent):
        super().__init__(project, parent)
        self.metric_options = self.root / 'metric-options.json'
        self.metric_script = self.root / 'native-metric.py'
        self.metric_script.write_bytes((FIXTURES / 'metric.py').read_bytes())
        self.metric_command = shlex.join([
            sys.executable, '-B', str(self.metric_script), str(self.metric_options)])
        self.metric_outcome()
        probe_source = Path(__file__).parent / 'fixtures' / 'initial_proxy' / 'child_probe.py'
        for probe in self.probes:
            probe.write_text('#!' + sys.executable + '\n' +
                             (FIXTURES / 'interpreter_probe.py').read_text())
            options = json.loads(probe.with_suffix('.json').read_text())
            options.update(probe_source=str(probe_source), metric_script=str(self.metric_script),
                           member_probes=list(map(str, self.probes[:2])))
            write(probe.with_suffix('.json'), options)
        for number in (0, 1):
            value = deepcopy(self.definitions[number])
            next(item for item in value['events'] if item['event'] == 'PostToolUse')['matcher'] = ''
            self.install_member(number, value, 'fixture-post-tool-' + str(number))
        # The foreign command has no HookSettings. Arrange only our private
        # host file, not managed product configuration or a real ERP collector.
        document = json.loads(self.hooks.read_text())
        assert document['hooks']['PostToolUse'][0]['matcher'] == 'sentinel'
        document['hooks']['PostToolUse'][0] = {
            'matcher': '*', 'hooks': [{'type': 'command', 'command': self.metric_command,
                                      'async': True, 'timeout': 30}]}
        write(self.hooks, document)
        self.before = deepcopy(document)
        self.post_members = deepcopy(document['hooks']['PostToolUse'])
        assert len(self.post_members) == 3

    def metric_outcome(self, *, stdout=b'{"continue":true}\n', stderr=b'', code=0, barrier=False):
        write(self.metric_options, {'trace': str(self.trace), 'barrier': barrier,
            'stdout': base64.b64encode(stdout).decode(),
            'stderr': base64.b64encode(stderr).decode(), 'exit_code': code})

    def tool_raw(self, *, tool='Bash', repository='poise', session='proxy-fixture-session',
                 large=False, oversized=False):
        value = json.loads((FIXTURES / 'native-event.json').read_text())
        value.update(tool_name=tool, cwd=str(self.repositories[repository]), session_id=session)
        if large or oversized:
            value['fixture_extension']['large'] = ((Path(__file__).parent / 'fixtures' / 'pre_tool_proxy' / 'large-input.txt').read_text() *
                (100 if oversized else 1)) + 'EXACT-TAIL-MARKER'
        return ('  ' + json.dumps(value, ensure_ascii=False, indent=1) + '\n').encode()

    def operation(self, action='publish', *, event='PostToolUse', request_id=None, revision=None):
        if event != 'PostToolUse':
            return super().operation(action, event=event, request_id=request_id, revision=revision)
        owner = self.services[1]
        packet = {'operation': 'post-tool-use-proxy', 'input': {
            'action': action, 'request_id': request_id or ('post-tool-proxy-' + action),
            'expected_revision': owner.revision() if revision is None else revision}}
        stdout, stderr = io.StringIO(), io.StringIO()
        code = execute('runtime-config', SimpleNamespace(settings=str(owner.settings.path)),
                       io.BytesIO(json.dumps(packet).encode()), stdout, stderr)
        text = stdout.getvalue()
        envelope = json.loads(text) if text else {}
        body = json.loads(Path(envelope['response_path']).read_text()) if envelope else {}
        require_proxy_operation(code, body)
        return code, body, stderr.getvalue()

    def publish(self, *, event='PostToolUse'):
        if event != 'PostToolUse':
            return super().publish(event=event)
        result = self.operation()
        groups = json.loads(self.hooks.read_text())['hooks']['PostToolUse']
        assert result[0] == 0 and len(groups) == 1, (
            'POST_TOOL_PROXY_PUBLICATION_MISSING', result, len(groups))
        assert len(groups[0]['hooks']) == 1
        return groups[0]['hooks'][0]

    def change_post(self, number, *, matcher=None, timeout=None):
        value = deepcopy(self.definitions[number])
        event = next(item for item in value['events'] if item['event'] == 'PostToolUse')
        if matcher is not None:
            event['matcher'] = matcher
        if timeout is not None:
            event['timeout_seconds'] = timeout
        return self.install_member(number, value, 'post-edit-' + str(number))

    def assert_metric(self, raw):
        self.wait_file('metric-started')
        row = json.loads((self.trace / 'metric-input.json').read_text())
        assert base64.b64decode(row['stdin']) == raw
        assert row['argv'] == [str(self.metric_options)]
        assert row['cwd'] == str(self.root)
        assert row['marker'] == 'literal inherited marker'
        return row

    def wait_file(self, name):
        deadline = time.monotonic() + 5
        path = self.trace / name
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert path.exists(), ('NATIVE_METRIC_WITNESS_MISSING', name)
        return path

    def release_metric(self):
        self.trace.mkdir(parents=True, exist_ok=True)
        (self.trace / 'metric-release').touch()
        if (self.trace / 'metric-started').exists():
            self.wait_file('metric-finished')

    def start_old_metric(self, raw):
        from .helpers import host_environment
        env = host_environment('proxy-fixture-session')
        env['INITIAL_PROXY_MARKER'] = 'literal inherited marker'
        return subprocess.Popen(['/bin/sh', '-c', self.metric_command, 'native-host'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.root, env=env, start_new_session=True)

    def stop_owned_metric(self):
        self.release_metric()

    def old_sync(self, raw):
        return [invoke(group['hooks'][0]['command'], raw, self.root)
                for group in self.post_members[1:]]

    def post_deliveries(self, number, session='proxy-fixture-session'):
        return [value for path in self.services[number].settings.observations.glob('*/response.json')
                if (value := json.loads(path.read_text())).get('event', {}).get('session_id') == session
                and value.get('event_receipt', {}).get('event') == 'PostToolUse']


def actual_output_case(project, parent, *, proxy, enforce=False):
    """Use the real native builtin, its independent wire fixture and saved full file."""
    from .architecture_support import Chain
    from .session_output_support import configure_chain, scene_for_chain
    from tests.runtime_services.native_wire_support import select_text, wire_case
    chain = Chain(project, parent, 'INITIAL_POST_NATIVE_OUTPUT')
    output = scene_for_chain(chain, project, parent)
    select_text(output)
    if enforce:
        output.policy['hook']['mode'] = 'enforce'
        output.save_policy()
    configure_chain(chain, output)
    expected = wire_case('healthy')
    event = output.native(expected['raw'], key='initial-post-output')['event']
    event['session_id'] = chain.session
    raw = ('  ' + json.dumps(event, ensure_ascii=False, indent=1) + '\n').encode()
    before = json.loads(chain.service.settings.hooks_file.read_text())
    command = before['hooks']['PostToolUse'][0]['hooks'][0]['command']
    if proxy:
        packet = {'operation': 'post-tool-use-proxy', 'input': {
            'action': 'publish', 'request_id': 'actual-output-proxy',
            'expected_revision': chain.service.revision()}}
        stdout, stderr = io.StringIO(), io.StringIO()
        code = execute('runtime-config', SimpleNamespace(settings=str(chain.service.settings.path)),
            io.BytesIO(json.dumps(packet).encode()), stdout, stderr)
        envelope = json.loads(stdout.getvalue())
        body = json.loads(Path(envelope['response_path']).read_text())
        require_proxy_operation(code, body)
        assert code == 0, ('POST_TOOL_PROXY_PUBLICATION_MISSING', stdout.getvalue(), stderr.getvalue())
        groups = json.loads(chain.service.settings.hooks_file.read_text())['hooks']['PostToolUse']
        assert len(groups) == 1 and len(groups[0]['hooks']) == 1
        command = groups[0]['hooks'][0]['command']
    result = invoke(command, raw, parent, chain.session)
    assert result.returncode == 0 and result.stderr == b''
    response = json.loads(result.stdout)
    answers = json.loads((FIXTURES / 'responses.json').read_text())
    assert response == answers['enforce-native' if enforce else 'audit-native']
    answer = output.service('full')
    assert Path(answer['full_path']).read_bytes() == expected['raw'].encode()
    records = [json.loads(path.read_text()) for path in
               chain.service.settings.observations.glob('*/response.json')]
    deliveries = [row for row in records if row.get('event_receipt', {}).get('event') == 'PostToolUse']
    assert len(deliveries) == 1
    assert deliveries[0]['event']['tool_response'] == expected['raw']
    return response


def wait_diagnostic(root, text):
    """Bound observation of actual saved failure, without delaying the hook reply."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(text in path.read_text() for path in root.rglob('*.json')):
            return
        time.sleep(.01)
    raise AssertionError(('SAVED_ASYNC_DIAGNOSTIC_MISSING', text))


def wait_metric_capture(scene, stdout, stderr, code):
    """Observe a terminal common-runner record, not the child's finish marker."""
    from .pre_tool_proxy_support import runner_records
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for record in runner_records(scene):
            if record['actual_exit_code'] != code or not record.get('capture_complete'):
                continue
            out, err = Path(record['stdout']), Path(record['stderr'])
            if out.is_file() and err.is_file() and (out.read_bytes(), err.read_bytes()) == (stdout, stderr):
                assert record['termination']['child_reaped'] is True
                assert record['termination']['capture_complete'] is True
                assert out != err
                assert out.is_relative_to(scene.capture_roots['proxy-fixture-session'])
                assert err.is_relative_to(scene.capture_roots['proxy-fixture-session'])
                return record
        time.sleep(.01)
    raise AssertionError(('LATE_METRIC_CAPTURE_MISSING', code, stdout, stderr))
