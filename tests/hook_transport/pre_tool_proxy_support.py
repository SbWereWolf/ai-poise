"""Private native registrations and process probes, never a proxy implementation."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace

from poise.interfaces.hook_transport import execute
from .initial_proxy_support import Scene, invoke, write


FIXTURES = Path(__file__).parent / 'fixtures' / 'pre_tool_proxy'


class PreToolScene(Scene):
    def __init__(self, project, parent):
        super().__init__(project, parent)
        for number, matcher in enumerate(('', 'Bash|mcp__codex_app__list_projects')):
            value = deepcopy(self.definitions[number])
            for event in ('PreToolUse', 'PostToolUse'):
                value['events'].append({'event': event, 'matcher': matcher,
                    'timeout_seconds': 30, 'async': False, 'context_limit': 8192,
                    'status_message': 'Observe private tool event'})
                value['routes'][event] = []
            self.install_member(number, value, 'fixture-private-tool-' + str(number))
        self.refresh()

    def refresh(self):
        self.before = json.loads(self.hooks.read_text())
        self.members = deepcopy(self.before['hooks']['SessionStart'])
        self.tool_members = deepcopy(self.before['hooks']['PreToolUse'])

    def install_member(self, number, value, request):
        service = self.services[number]
        receipt = service.install({'request_id': request,
            'expected_revision': service.revision(), 'definition': value})
        self.definitions[number], self.receipts[number] = value, receipt
        return receipt

    def change_tool(self, number, *, matcher=None, timeout=None):
        value = deepcopy(self.definitions[number])
        event = next(row for row in value['events'] if row['event'] == 'PreToolUse')
        if matcher is not None:
            event['matcher'] = matcher
        if timeout is not None:
            event['timeout_seconds'] = timeout
        event['status_message'] = 'Changed private PreToolUse callback'
        return self.install_member(number, value, 'fixture-tool-edit-' + str(number))

    def tool_raw(self, *, tool='Bash', source='startup', repository='poise',
                 session='proxy-fixture-session', large=False):
        value = json.loads((FIXTURES / 'native-event.json').read_text())
        value.update(tool_name=tool, source=source,
                     cwd=str(self.repositories[repository]), session_id=session)
        if large:
            value['fixture_extension']['large'] = (FIXTURES / 'large-input.txt').read_text()
        return ('  ' + json.dumps(value, ensure_ascii=False, indent=1) + '\n').encode()

    def old(self, raw, selected):
        # Selection is an explicit test-owned oracle. No product matcher/helper
        # is used to determine which old callbacks should have been selected.
        packet = json.loads(raw)
        session = packet['session_id'] if isinstance(packet, dict) else 'proxy-fixture-session'
        return [invoke(self.tool_members[number]['hooks'][0]['command'], raw,
                       self.root, session) for number in selected]

    def reset_calls(self):
        (self.trace / 'calls.jsonl').unlink(missing_ok=True)

    def bind(self, *, repository='poise', session='proxy-fixture-session'):
        number = 1 if repository == 'poise' else 0
        self.outcome(number, mode='delegate')
        command = self.services[number].settings.command(
            'hook', '--definition', self.receipts[number]['definition_path'])
        result = invoke(command,
                        self.raw_event(repository=repository, session=session),
                        self.root, session)
        assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b'')
        service = self.services[number]
        binding = service.latest_binding(session, 'primary')
        runtime = service.bound_runtime(binding['binding_path'])
        self.bindings[session] = binding
        self.capture_roots[session] = Path(runtime.runtime)
        self.outcome(number)
        self.reset_calls()
        return Path(runtime.runtime)

    def operation(self, action='publish', *, event='PreToolUse', request_id=None,
                  revision=None):
        owner = self.services[1]
        operation = {'SessionStart': 'session-start-proxy',
                     'PreToolUse': 'pre-tool-use-proxy'}[event]
        packet = {'operation': operation, 'input': {
            'action': action, 'request_id': request_id or (operation + '-' + action),
            'expected_revision': owner.revision() if revision is None else revision}}
        stdout, stderr = io.StringIO(), io.StringIO()
        code = execute('runtime-config', SimpleNamespace(settings=str(owner.settings.path)),
                       io.BytesIO(json.dumps(packet).encode()), stdout, stderr)
        envelope = json.loads(stdout.getvalue())
        body = json.loads(Path(envelope['response_path']).read_text())
        return code, body, stderr.getvalue()

    def publish(self, *, event='PreToolUse'):
        result = self.operation(event=event)
        groups = json.loads(self.hooks.read_text())['hooks'][event]
        assert len(groups) == 1, ('PRE_TOOL_PROXY_SINGLE_ENTRY', event, len(groups), result)
        assert len(groups[0]['hooks']) == 1
        assert result[0] == 0, result
        return groups[0]['hooks'][0]

    def assert_calls(self, raw, selected):
        calls = self.calls()
        assert [row['id'] for row in calls] == selected
        for row in calls:
            assert base64.b64decode(row['stdin']) == raw
            assert row['cwd'] == str(self.root)
            assert row['marker'] == 'literal inherited marker'
            number = row['id']
            assert row['argv'] == ['-P', '-B', '-X', 'pycache_prefix=/dev/null',
                '-m', 'poise', 'hook', '--settings', str(self.services[number].settings.path),
                '--definition', self.receipts[number]['definition_path']]

    def deliveries(self, number, session='proxy-fixture-session'):
        values = []
        for path in self.services[number].settings.observations.glob('*/response.json'):
            value = json.loads(path.read_text())
            if (value.get('event', {}).get('session_id') == session and
                    value.get('event_receipt', {}).get('event') == 'PreToolUse'):
                values.append(value)
        return values


def baseline_cases():
    return json.loads((FIXTURES / 'baseline-cases.json').read_text())


def arrange_case(scene, case):
    if case['bound']:
        scene.bind(repository=case['repository'])
    for number in (0, 1):
        scene.outcome(number, mode='delegate')
    return b'[]\n' if case['invalid'] else scene.tool_raw(
        tool=case['tool'], repository=case['repository'])


def assert_deliveries(scene, case):
    for number in (0, 1):
        rows = scene.deliveries(number)
        if number in case['successes']:
            assert len(rows) == 1
            assert rows[0]['status'] == 'completed'
            assert rows[0]['response'] == {}
            assert rows[0]['invocations'] == []
        else:
            assert rows == []


def runner_records(scene, session='proxy-fixture-session'):
    """Read complete runner references only under the actual owned session root."""
    def visit(value):
        if isinstance(value, dict):
            if {'stdout', 'stderr', 'actual_exit_code'} <= set(value):
                yield value
            for child in value.values():
                yield from visit(child)
        elif isinstance(value, list):
            for child in value:
                yield from visit(child)
    for path in scene.capture_roots[session].rglob('*.json'):
        yield from visit(json.loads(path.read_text()))


def captured(scene, stdout, stderr, code, session='proxy-fixture-session'):
    matches = set()
    root = scene.capture_roots[session]
    for row in runner_records(scene, session):
        if row['actual_exit_code'] != code:
            continue
        out, err = Path(row['stdout']), Path(row['stderr'])
        if out.is_file() and err.is_file() and (out.read_bytes(), err.read_bytes()) == (stdout, stderr):
            assert out.is_relative_to(root) and err.is_relative_to(root)
            assert out.is_absolute() and err.is_absolute() and out != err
            assert row['capture_complete'] is True
            matches.add((str(out), str(err)))
    assert matches, 'Complete callback streams absent from owned runner records'
    return matches
