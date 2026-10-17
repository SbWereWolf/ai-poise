"""Public consumer arrangements and independent literal placement oracles."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

from tests.conftest import write_json

FIXTURES = Path(__file__).parent / 'fixtures'
SOURCE = Path(__file__).resolve().parents[2] / 'src'
IDENTITY = 'placement-fixture-caller'
REMOVED_ENV = {'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING',
               'POISE_CONFIG', 'POISE_SESSION'}


def literal(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def canonical(value):
    # This frozen test encoding never invokes the product serializer.
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class PlacementUnavailable(AssertionError):
    code = 'PLACEMENT_UNSUPPORTED'


def decoded(code, stdout, stderr):
    if (code == 2 and stdout == '' and 'usage:' in stderr
            and "argument command: invalid choice: 'placement'" in stderr):
        raise PlacementUnavailable(stderr)
    # Import, configuration and other CLI errors remain their actual failure.
    return code, json.loads(stdout)


class Scenario:
    def __init__(self, project, parent, *, beta=False):
        self.base = project
        self.beta = beta
        self.checkout = project['app']
        self.root = project['root']
        self.config = project['config_path']
        self.project = project['cfg']['project']
        self.binding = parent / 'placement-caller.json'
        write_json(self.binding, {'schema': 'poise-caller-binding-1', 'caller_id': IDENTITY})
        self.env = {k: v for k, v in os.environ.items() if k not in REMOVED_ENV}
        self.env.update(POISE_CALLER_BINDING=str(self.binding), PYTHONPATH=str(SOURCE),
                        PYTHONDONTWRITEBYTECODE='1')
        (self.checkout / 'docs').mkdir(exist_ok=True)
        assert sorted(v.name for v in self.checkout.iterdir()) == ['.git', 'AGENTS.md', 'docs', 'src']
        # Independently specified existing CallerIdentity wire algorithm, not its factory.
        caller_key = digest(json.dumps([self.project, 'generated', IDENTITY],
                                      ensure_ascii=False, separators=(',', ':')).encode())
        harness_data = self.root / project['cfg']['paths']['state']
        self.tokens = {'@PROJECT@': self.project, '@CHECKOUT@': str(self.checkout),
                       '@CALLER_KEY@': caller_key, '@HARNESS_DATA@': str(harness_data),
                       '@PRODUCT_DATA@': str(parent / 'product-local'),
                       '@WORK_ROOT@': str(harness_data / 'agent-work'),
                       '@HANDOFF_ROOT@': str(parent / 'recipient-outbox')}
        self.state = (harness_data / 'placement' / caller_key
                      / digest(str(self.checkout).encode()) / 'placement.json')
        self.tokens['@STATE_PATH@'] = str(self.state)
        self.policy = parent / 'placement-policy.json'
        self.policy_data = self.fill(literal('policy-beta.template.json' if beta else 'policy-alpha.template.json'))
        self.save_policy()

    def fill(self, value):
        if isinstance(value, str):
            for key, replacement in self.tokens.items():
                value = value.replace(key, replacement)
            return value
        if isinstance(value, dict):
            return {k: self.fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.fill(v) for v in value]
        return value

    def fixture(self, name):
        return self.fill(literal(name))

    def save_policy(self):
        write_json(self.policy, self.policy_data)

    def request(self, *, operation='prepare', items=None, authorizations=None):
        request = self.fixture('request-prepare.json')
        request['operation'] = operation
        if items is not None:
            request['items'] = deepcopy(items)
        request['authorizations'] = [] if authorizations is None else deepcopy(authorizations)
        if operation == 'check':
            for item in request['items']:
                if item['path'] is None:
                    item['path'] = 'src/' + item['name']
        return request

    def invoke(self, request=None, *, raw=None, env=None, checkout=None):
        argv = [sys.executable, '-B', '-m', 'poise', 'placement', '--config', str(self.config),
                '--checkout', str(self.checkout if checkout is None else checkout), '--policy', str(self.policy)]
        result = subprocess.run(argv, input=canonical(self.request() if request is None else request)
                                if raw is None else raw, capture_output=True, cwd=self.checkout,
                                env=self.env if env is None else env, check=False)
        stdout, stderr = result.stdout.decode(), result.stderr.decode()
        print(stdout, end='')
        print(stderr, end='', file=sys.stderr)
        return decoded(result.returncode, stdout, stderr)

    def transport(self, monkeypatch, request=None):
        # Every caller proves a successful real CLI before importing this future module.
        for key in REMOVED_ENV:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv('POISE_CALLER_BINDING', str(self.binding))
        from poise.interfaces.placement import execute
        output = io.StringIO()
        code = execute(self.config, self.checkout, self.policy,
                       io.BytesIO(canonical(self.request() if request is None else request)), output)
        return code, json.loads(output.getvalue())

    def initial(self):
        assert not self.state.exists()
        expected = self.fixture('state-beta-source.template.json' if self.beta else 'state-source.template.json')
        raw = canonical(expected)
        envelope = self.fixture('expected-beta-prepare.template.json' if self.beta else 'expected-prepare.template.json')
        envelope['policy_sha256'] = digest(self.policy.read_bytes())
        envelope['state']['after_sha256'] = digest(raw)
        envelope['state']['observed_sha256'] = digest(raw)
        actual = self.invoke()
        assert actual == (0, envelope)
        assert self.state.read_bytes() == raw
        return raw

    def work(self, task=None):
        packet = literal('work-bootstrap.json')
        packet['input']['task'] = task
        env = {**self.env, 'POISE_CONFIG': str(self.config)}
        result = subprocess.run([sys.executable, '-B', '-m', 'poise', 'work'],
                                input=canonical(packet), capture_output=True, env=env,
                                cwd=self.checkout, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        envelope = json.loads(result.stdout)
        return json.loads(Path(envelope['response_path']).read_bytes())

    def read(self):
        return json.loads(self.state.read_bytes())

    def seed(self, state):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_bytes(canonical(state))

    def row(self, **changes):
        row = self.fixture('expected-prepare.template.json')['items'][0]
        row.update(changes)
        return row

    def assert_result(self, actual, *, status='ready', rows=None, diagnostics=None):
        code, value = actual
        assert code == (0 if status == 'ready' else 1)
        assert set(value) == {'schema', 'status', 'project', 'checkout', 'policy_sha256',
                              'state', 'items', 'diagnostics'}
        assert value['schema'] == 'poise-placement-result-1'
        assert value['status'] == status
        assert value['project'] == self.project and value['checkout'] == str(self.checkout)
        assert value['policy_sha256'] == digest(self.policy.read_bytes())
        assert value['items'] == ([self.row()] if rows is None else rows)
        assert value['diagnostics'] == ([] if diagnostics is None else diagnostics)
        assert set(value['state']) == {'path', 'before_sha256', 'after_sha256',
                                      'observed_sha256', 'effect', 'confirmation'}
        assert value['state']['path'] == str(self.state)
        return value

    def assert_rejected(self, actual, code, field, action):
        expected = literal('rejected.json')
        expected['error'].update(code=code, field=field, action=action)
        assert actual == (2, expected)

    def assert_effect(self, value, before, after, effect, confirmation):
        expected = self.fixture('state-observation.template.json')
        expected.update(before_sha256=None if before is None else digest(before),
                        after_sha256=None if after is None else digest(after),
                        observed_sha256=digest(self.state.read_bytes()) if self.state.exists() else None,
                        effect=effect, confirmation=confirmation)
        assert value['state'] == expected


def diagnostic(code, key, field, action):
    return {'key': key, 'code': code, 'field': field, 'action': action}


def item(key='source-report', *, purpose='source', recipient='product', lifetime='versioned',
         name='report.py', path=None):
    row = literal('request-prepare.json')['items'][0]
    row.update(key=key, purpose=purpose, recipient=recipient, lifetime=lifetime, name=name, path=path)
    return row
