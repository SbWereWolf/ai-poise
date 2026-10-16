"""Test-owned inputs and literal oracles for the public instruction consumer."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tests.projects.helpers import setup_case
from tests.conftest import write_json
from poise.composition import project_tools

FIXTURES = Path(__file__).parent / 'fixtures/general_instructions'
SOURCE = Path(__file__).resolve().parents[2] / 'src'


class PreparationUnavailable(AssertionError):
    code = 'PREPARE_UNSUPPORTED'


def fixture(name):
    return json.loads((FIXTURES / name).read_bytes().decode('utf-8'))


def body(name):
    return (FIXTURES / (name + '.md')).read_bytes()


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


def decoded(code, stdout, stderr):
    value = json.loads(stdout)
    if code == 2 and value.get('reason') == 'Unknown skills operation':
        raise PreparationUnavailable(stdout + stderr)
    return code, value


class Scenario:
    def __init__(self, base, parent, name):
        self.name = name
        self.root = parent / name
        self.root.mkdir()
        self.checkout = self.root / 'checkout'
        shutil.copytree(base['app'], self.checkout)
        self.write('AGENTS.md', body('root' if name == 'alpha' else 'beta-root'))
        self.write('src/billing/invoice.py', b'VALUE = 1\n')
        self.write('docs/guide.md', b'# Guide\n')
        if name == 'alpha':
            for path, key in [('src/AGENTS.md', 'src'),
                              ('src/billing/AGENTS.md', 'billing-rules'),
                              ('docs/AGENTS.md', 'docs-rules')]:
                self.write(path, body(key))
        for skill in ['core', 'billing', 'writing', 'audit', 'unused']:
            content = body('beta-core' if name == 'beta' and skill == 'writing' else skill)
            self.write('.agents/skills/' + skill + '/SKILL.md', content)
        self.catalog = write_json(self.root / 'catalog.json', fixture('catalog.json'))
        self.policy = write_json(self.root / 'policy.json',
                                 fixture('policy.json' if name == 'alpha' else 'beta-policy.json'))
        config = deepcopy(base['cfg'])
        config['project'] = name
        config['git']['repository'] = str(self.checkout)
        config['paths']['state'] = 'state'
        config['paths']['requirements_database'] = 'requirements/requirements.sqlite'
        config['paths']['requirements_lock'] = 'requirements/requirements.lock'
        write_json(self.root / 'config/workflows/development.json', base['workflow'])
        settings, _, request = setup_case({'root': self.root, 'cfg': config,
                                          'workflow': base['workflow'], 'app': self.checkout})
        request['edits'][0]['value'] = name
        created = project_tools(settings).apply(request)
        self.config = Path(created['config_path'])
        self.calls = []

    def write(self, path, data):
        target = self.checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def request(self, paths=None, previous=None):
        value = fixture('request.json')
        value.update(project_config=str(self.config), checkout=str(self.checkout),
                     policy=str(self.policy), previous=previous)
        if paths is not None:
            value['paths'] = paths
        return value

    def argv(self):
        return [sys.executable, '-B', '-m', 'poise', 'skills', '--catalog',
                str(self.catalog), '--root', str(self.checkout)]

    def invoke(self, request=None, raw=None):
        packet = self.request() if request is None else request
        command = self.argv()
        self.calls.append(command)
        result = subprocess.run(command, input=json.dumps(packet) if raw is None else raw,
                                text=True, capture_output=True, cwd=self.checkout,
                                env={**os.environ, 'PYTHONPATH': str(SOURCE),
                                     'PYTHONDONTWRITEBYTECODE': '1'}, check=False)
        # Pytest captures the complete consumer streams; the RED reporter retains them.
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        return decoded(result.returncode, result.stdout, result.stderr)

    def assert_response(self, actual, materials, *, status='ready', paths=None,
                        diagnostics=None, delta=None):
        code, value = actual
        assert code == (0 if status == 'ready' else 1)
        expected = fixture('response-envelope.json')
        expected.update(project=self.name, checkout=str(self.checkout),
                        instruction_root=str(self.checkout),
                        policy_sha256=hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                        materials=materials, status=status,
                        diagnostics=[] if diagnostics is None else diagnostics,
                        delta=delta)
        if paths is not None:
            expected['paths'] = paths
        assert value == expected
        return value

    def profiles(self):
        write_json(self.policy, fixture('profile-policy.json'))
        self.write('.agents/profiles/billing.md', body('billing-profile'))
        self.write('.agents/profiles/writing.md', body('writing-profile'))


def unavailable(records, kind, id):
    records = deepcopy(records)
    selected = next(v for v in records if v['kind'] == kind and v['id'] == id)
    selected.update(available=False, content=None, sha256=None)
    return records


def changed(records, kind, id, content):
    records = deepcopy(records)
    selected = next(v for v in records if v['kind'] == kind and v['id'] == id)
    selected.update(content=content.decode('utf-8'), sha256=hashlib.sha256(content).hexdigest())
    return records
