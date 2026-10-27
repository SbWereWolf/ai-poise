"""External test clients and literal fixtures, never an output implementation."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name('fixtures') / 'tool_results'
REMOVED = {'PYTHONPATH', 'POISE_CONFIG', 'POISE_SESSION', 'POISE_CALLER_BINDING',
           'CODEX_THREAD_ID', 'CODEX_SESSION_ID'}


class ToolResultUnavailable(AssertionError):
    """Only the exact genuinely missing public command, not arbitrary CLI errors."""


def literal(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def wire(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wire(value))
    return path


class Scenario:
    def __init__(self, project, parent, *, python=None, source=True):
        self.project = project
        self.checkout = project['app']
        self.config = project['config_path']
        from tests.hook_transport.helpers import harness_profile
        self.config = harness_profile(project, parent, task_adapter=False)
        self.parent = parent
        self.root = parent / 'tool-results'
        self.root.mkdir()
        self.out = parent / 'producer.stdout'
        self.err = parent / 'producer.stderr'
        self.out.write_bytes((FIXTURES / 'short.stdout.log').read_bytes())
        self.err.write_bytes((FIXTURES / 'short.stderr.log').read_bytes())
        self.producer_count = parent / 'producer-count.txt'
        self.policy_path = parent / 'tool-policy.json'
        self.placement = parent / 'placement-policy.json'
        self.response_path = None
        self.python = sys.executable if python is None else str(python)
        self.env = {k: v for k, v in os.environ.items() if k not in REMOVED}
        binding = write_json(parent / 'tool-caller.json',
                             {'schema': 'poise-caller-binding-1', 'caller_id': 'tool-result-test'})
        self.env.update(POISE_CALLER_BINDING=str(binding), PYTHONDONTWRITEBYTECODE='1')
        if source:
            self.env['PYTHONPATH'] = str(ROOT / 'src')
        self.tokens = {'@RESULTS@': str(self.root), '@PLACEMENT@': str(self.placement),
                      '@HARNESS@': str(project['root'] / project['cfg']['paths']['state']),
                      '@STDOUT@': str(self.out), '@STDERR@': str(self.err),
                      '@CHECKOUT@': str(self.checkout)}
        write_json(self.placement, self.fill(literal('placement.template.json')))
        self.policy = self.fill(literal('policy.template.json'))
        self.save_policy()
        self.calls = []

    def fill(self, value):
        if isinstance(value, str):
            for key, replacement in self.tokens.items():
                value = value.replace(key, replacement)
            return value
        if isinstance(value, dict):
            return {key: self.fill(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.fill(item) for item in value]
        return value

    def save_policy(self):
        write_json(self.policy_path, self.policy)

    def request(self, name='capture.template.json', **fields):
        result = self.fill(literal(name))
        if self.response_path is not None:
            result = json.loads(json.dumps(result).replace('@RESPONSE@', str(self.response_path)))
        result.update(deepcopy(fields))
        return result

    def invoke(self, request, audience='agent'):
        argv = [self.python, '-B', '-m', 'poise', 'tool-result', '--config', str(self.config),
                '--checkout', str(self.checkout), '--policy', str(self.policy_path),
                '--audience', audience]
        process = subprocess.run(argv, input=wire(request), capture_output=True, check=False,
                                 cwd=self.parent, env=self.env)
        stdout, stderr = process.stdout.decode(), process.stderr.decode()
        self.calls.append({'argv': argv, 'cwd': str(self.parent), 'request': request,
                           'exit_code': process.returncode, 'stdout': stdout, 'stderr': stderr})
        write_json(self.parent / 'tool-invocations.json', self.calls)
        print(stdout, end=''); print(stderr, end='', file=sys.stderr)
        if (process.returncode == 2 and not stdout and 'usage:' in stderr
                and "argument command: invalid choice: 'tool-result'" in stderr):
            raise ToolResultUnavailable('Exact public tool-result command is unavailable')
        return process.returncode, stdout, stderr

    def success(self, request=None, audience='agent'):
        code, text, err = self.invoke(self.request() if request is None else request, audience)
        assert code == 0, (code, text, err)
        assert err == ''
        value = json.loads(text)
        if audience == 'agent':
            self.bounded(text)
        return value

    def bounded(self, text):
        assert text.endswith('\n')
        assert len(text) <= self.policy['display']['output_chars']
        assert len(text.encode()) <= self.policy['display']['output_bytes']

    def capture(self, request=None, audience='agent'):
        request = self.request() if request is None else request
        value = self.success(request, audience)
        assert value['status'] == 'stored'
        assert value['capture'] == request['capture']
        assert type(value['exit_code']) is type(request['exit_code'])
        assert value['exit_code'] == request['exit_code']
        self.response_path = Path(value['response_path'])
        assert self.response_path.is_relative_to(self.root)
        assert self.response_path.is_file()
        saved = json.loads(self.response_path.read_bytes())
        assert saved['schema'] == 'poise-tool-result-1'
        assert wire(saved['response']) == wire(request['response'])
        assert saved['tool'] == request['tool']
        assert saved['capture'] == request['capture']
        assert type(saved['exit_code']) is type(request['exit_code'])
        assert saved['exit_code'] == request['exit_code']
        for name in ('stdout', 'stderr'):
            original = request['streams'][name]
            item = saved['streams'][name]
            if original is None:
                assert item is None
                continue
            expected = Path(original).read_bytes()
            path = Path(item['path'])
            assert path != Path(original) and path.is_relative_to(self.root)
            assert path.read_bytes() == expected
            assert type(item['bytes']) is int and type(item['lines']) is int
            assert type(item['binary']) is bool
            assert item['bytes'] == len(expected)
            assert item['sha256'] == sha(expected)
        if audience == 'machine':
            assert wire(value['response']) == wire(request['response'])
            for name in ('stdout', 'stderr'):
                if request['streams'][name] is not None:
                    assert Path(value['streams'][name]['path']).read_bytes() == Path(request['streams'][name]).read_bytes()
        return value, saved

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def refusal(self, request, code, action):
        exit_code, text, err = self.invoke(request)
        assert exit_code != 0
        assert err == ''
        value = json.loads(text)
        assert value['status'] == 'rejected'
        assert set(value['error']) == {'code', 'field', 'action'}
        assert isinstance(value['error']['field'], str) and value['error']['field']
        assert value['error']['code'] == code
        assert value['error']['action'] == action
        assert 'response_path' not in value
        return value

    def bash_producer(self, size='short', exit_code=7):
        script = FIXTURES / 'producer.py'
        result = subprocess.run([sys.executable, '-B', str(script), '--kind', 'bash',
                                 '--size', size, '--counter', str(self.producer_count),
                                 '--exit-code', str(exit_code)], capture_output=True, check=False)
        assert result.returncode == exit_code
        self.out.write_bytes(result.stdout); self.err.write_bytes(result.stderr)
        assert self.producer_count.read_text() == '1'
        return result
