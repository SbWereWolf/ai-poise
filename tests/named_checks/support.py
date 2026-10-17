"""Test arrangements and independent assertions; no named-check implementation."""
from copy import deepcopy
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import sqlite3
import tomllib

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures'


class NamedChecksUnavailable(AssertionError):
    """The exact intended pre-implementation entry failure, inside CALL only."""


def literal(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'


def digest(body):
    return hashlib.sha256(body).hexdigest()


def entry_result(result):
    """Recognize missing entry only; every other failure retains its identity."""
    if (result.returncode == 2 and result.stdout == b''
            and b'usage:' in result.stderr
            and b"argument command: invalid choice: 'checks'" in result.stderr):
        raise NamedChecksUnavailable('The actual CLI has no checks entry.')
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stdout and not result.stderr, (result.stdout, result.stderr)


def entry():
    import poise
    assert Path(poise.__file__).is_relative_to(ROOT / 'src')
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-B', '-m', 'poise', 'checks', '--help'],
                            cwd=ROOT, env=env, capture_output=True, check=False)
    entry_result(result)


def schema(value, spec):
    """Assert the literal test-owned JSON grammar, independent of product parsing."""
    for union in ('oneOf', 'anyOf'):
        if union in spec:
            accepted = 0
            for branch in spec[union]:
                try:
                    schema(value, branch)
                except AssertionError:
                    pass
                else:
                    accepted += 1
            assert accepted == 1 if union == 'oneOf' else accepted >= 1, (value, union)
    for branch in spec.get('allOf', []):
        schema(value, branch)
    if 'not' in spec:
        try:
            schema(value, spec['not'])
        except AssertionError:
            pass
        else:
            raise AssertionError(('forbidden shape', value))
    if 'if' in spec:
        try:
            schema(value, spec['if'])
        except AssertionError:
            if 'else' in spec:
                schema(value, spec['else'])
        else:
            if 'then' in spec:
                schema(value, spec['then'])
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'number': (int, float), 'boolean': bool, 'null': type(None)}
    if 'type' in spec:
        expected = spec['type']
        assert isinstance(value, types[expected]), (expected, value)
        if expected in ('integer', 'number'):
            assert not isinstance(value, bool) and math.isfinite(value)
    if 'const' in spec:
        assert type(value) is type(spec['const']) and value == spec['const']
    if 'enum' in spec:
        assert any(type(value) is type(v) and value == v for v in spec['enum'])
    if isinstance(value, dict):
        assert set(spec.get('required', [])) <= set(value)
        props = spec.get('properties', {})
        extra = spec.get('additionalProperties', True)
        if extra is False:
            assert not set(value) - set(props), (set(value), set(props))
        for key, item in value.items():
            if key in props:
                schema(item, props[key])
            elif isinstance(extra, dict):
                schema(item, extra)
    if isinstance(value, list):
        assert len(value) >= spec.get('minItems', 0)
        if 'maxItems' in spec:
            assert len(value) <= spec['maxItems']
        if spec.get('uniqueItems'):
            assert len({canonical(v) for v in value}) == len(value)
        for item in value:
            schema(item, spec.get('items', {}))
    if isinstance(value, str):
        assert len(value) >= spec.get('minLength', 0)
        if 'pattern' in spec:
            assert re.search(spec['pattern'], value), (value, spec['pattern'])
    if type(value) in (int, float):
        if 'minimum' in spec:
            assert value >= spec['minimum']
        if 'exclusiveMinimum' in spec:
            assert value > spec['exclusiveMinimum']


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_toml(path, data):
    """Serialize fixture inputs only; never generate a functional expected result."""
    lines = []

    def scalar(value):
        if isinstance(value, dict):
            return '{' + ', '.join(json.dumps(k) + ' = ' + scalar(v) for k, v in value.items()) + '}'
        if isinstance(value, list):
            return '[' + ', '.join(scalar(v) for v in value) + ']'
        assert value is not None, 'TOML absence must be omitted explicitly'
        return json.dumps(value, ensure_ascii=False)

    def table(value, prefix):
        for key, item in value.items():
            if not isinstance(item, dict) and not (isinstance(item, list) and item and isinstance(item[0], dict)):
                lines.append(json.dumps(key) + ' = ' + scalar(item))
        for key, item in value.items():
            name = '.'.join([*prefix, json.dumps(key)])
            if isinstance(item, dict):
                lines.append('[' + name + ']')
                table(item, [*prefix, json.dumps(key)])
            elif isinstance(item, list) and item and isinstance(item[0], dict):
                for child in item:
                    lines.append('[[' + name + ']]')
                    table(child, [*prefix, json.dumps(key)])
    table(data, [])
    text = '\n'.join(lines) + '\n'
    assert tomllib.loads(text) == data
    path.write_text(text, encoding='utf-8')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def snapshot(root):
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob('*')) if p.is_file()}


def task_projection(project):
    """Read only Task/Sprint ownership data; optional caller telemetry is separate."""
    database = project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']
    queries = {'tasks': 'SELECT id,status,stage_index,iteration,claimed_by,version,metadata FROM tasks ORDER BY id',
               'sprints': 'SELECT id,project,state,revision,data FROM sprints ORDER BY id',
               'session_sprints': 'SELECT session_id,sprint_id FROM session_sprints ORDER BY session_id'}
    result = {key: [] for key in queries}
    if database.exists():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            names = {v[0] for v in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, query in queries.items():
                if table in names:
                    result[table] = connection.execute(query).fetchall()
    return result


class FakeClock:
    def __init__(self, observations):
        self.values = iter(observations)
        self.calls = 0

    def observe(self):
        from poise.modules.accounting.clock import ClockObservation
        self.calls += 1
        return ClockObservation(**next(self.values))


def clock_pair(seconds=2, *, finish_domain='fixture-A', finish_ns=None):
    return FakeClock([
        {'audit_utc': '2026-10-17T00:00:00+00:00', 'monotonic_ns': 1000000000,
         'comparison_domain': 'fixture-A'},
        {'audit_utc': '2026-10-16T23:59:00+00:00',
         'monotonic_ns': int((1 + seconds) * 1000000000) if finish_ns is None else finish_ns,
         'comparison_domain': finish_domain}])


class Scene:
    def __init__(self, project, tmp_path, monkeypatch):
        # Called inside the test body, before imports of the future product entry.
        entry()
        self.project = project
        self.checkout = project['app']
        self.config = project['config_path']
        self.root = tmp_path / 'named-arrangement'
        self.root.mkdir()
        self.events = self.root / 'events'
        self.events.mkdir()
        self.monkeypatch = monkeypatch
        monkeypatch.setenv('F07_FIXTURE_EVENTS', str(self.events))
        self.policy_path = self.root / 'policy.json'
        self.profiles_path = self.root / 'profiles.toml'
        self.packages_path = self.root / 'packages.toml'
        self.suites_path = self.root / 'suites.json'
        self.state = project['root'] / project['cfg']['paths']['state'] / 'named-checks'
        substitutions = {'@PYTHON@': sys.executable,
                         '@CHILD@': str(Path(__file__).with_name('fixture_child.py'))}
        self.profiles = tomllib.loads((FIXTURES / 'input-profiles.toml').read_text())
        for v in self.profiles['commands']:
            v['argv'] = [substitutions.get(a, a) for a in v['argv']]
        self.packages = tomllib.loads((FIXTURES / 'input-packages.toml').read_text())
        self.suites = literal('input-suites.json')
        self.policy = literal('input-policy.json')
        paths = {'@PROFILES@': str(self.profiles_path), '@PACKAGES@': str(self.packages_path),
                 '@SUITES@': str(self.suites_path), '@STATE@': str(self.state),
                 '@REPORTS@': str(self.root / 'reports'), **substitutions}
        self.policy = self._replace(self.policy, paths)
        # All fixture suite commands have real, unique configured profiles.
        for name in ['core-1', 'core-2', 'ui-1', 'release-1', 'fast-1']:
            self.profile(name, marker=name + ':1')
        for name in ['src/core/a.py', 'src/core/sub/a.py', 'src/core/sub/b.py',
                     'src/ui/view.py', 'src/security/check.py', 'tests/base.py',
                     'tests/core/check.py']:
            target = self.checkout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('# independent fixture input\n')
        git(self.checkout, 'add', '.')
        git(self.checkout, 'commit', '-m', 'Arrange named-check input membership')
        self.save()
        self.contract = literal('contract.json')

    @staticmethod
    def _replace(value, paths):
        if isinstance(value, str):
            return paths.get(value, value)
        if isinstance(value, list):
            return [Scene._replace(v, paths) for v in value]
        if isinstance(value, dict):
            return {k: Scene._replace(v, paths) for k, v in value.items()}
        return value

    def profile(self, name='sample', marker=None, **changes):
        matches = [v for v in self.profiles['commands'] if v['command_id'] == name]
        if matches:
            value = matches[0]
        else:
            value = deepcopy(self.profiles['commands'][0])
            value['command_id'] = name
            self.profiles['commands'].append(value)
        if marker is not None:
            value['argv'] = [sys.executable, str(Path(__file__).with_name('fixture_child.py')),
                             '--marker', marker]
        value.update(changes)
        return value

    def child(self, *arguments, name='sample', **changes):
        value = self.profile(name, **changes)
        value['argv'] = [sys.executable, str(Path(__file__).with_name('fixture_child.py')),
                         '--marker', name, *arguments]
        return value

    def save(self):
        write_json(self.policy_path, self.policy)
        write_toml(self.profiles_path, self.profiles)
        write_toml(self.packages_path, self.packages)
        write_json(self.suites_path, self.suites)

    @staticmethod
    def selection(name='sample', *, kind='profile', workload='unit', runtime='python'):
        return {'kind': kind, 'id': name, **({'workload_class': workload, 'runtime_class': runtime}
                                          if kind == 'profile' else {})}

    def request(self, operation='run', selection=None, *, items=None, **options):
        if items is None:
            selected = self.selection() if selection is None else selection
            if operation in ('preview', 'run'):
                items = [{'selection': selected, 'options': {
                    'fresh': False, 'scope_paths': None, 'timeout_override': None, **options}}]
            elif operation == 'describe':
                items = [selected]
            elif operation == 'list':
                items = [{'kind': 'profiles', 'platform': None}]
            else:
                raise AssertionError('Arrange explicit items for ' + operation)
        return {'schema': 'poise-project-checks-request-1', 'operation': operation, 'items': items}

    def invoke(self, request=None, *, clock=None, config=None, checkout=None,
               policy=None, output=None, raw=None):
        module = importlib.import_module('poise.interfaces.project_checks')
        assert Path(module.__file__).is_relative_to(ROOT / 'src')
        from poise.infrastructure.clock import SystemClock
        sink = io.StringIO() if output is None else output
        errors = io.StringIO()
        body = canonical(self.request() if request is None else request).encode() if raw is None else raw
        code = module.execute(self.config if config is None else config,
                              self.checkout if checkout is None else checkout,
                              self.policy_path if policy is None else policy,
                              io.BytesIO(body), sink, errors,
                              SystemClock() if clock is None else clock)
        if output is not None:
            return code, sink, errors.getvalue()
        text = sink.getvalue()
        value = json.loads(text)
        assert text == canonical(value)
        schema(value, self.contract['schemas']['raw_response'])
        return code, value, errors.getvalue()

    def run(self, *, selection=None, clock=None, status='passed', **options):
        self.save()
        code, value, error = self.invoke(self.request(selection=selection, **options), clock=clock)
        assert value['status'] == status, value
        assert code == (0 if status in ('passed', 'ready') else 3 if status == 'unavailable' else 2 if status == 'invalid' else 1)
        assert not error
        return value['items'][0] if value['items'] else value

    def record(self, item):
        execution = item['source'] if item['kind'] == 'reuse' else item
        assert execution['kind'] == 'execution'
        return execution['steps'][0]['record']

    def markers(self):
        path = self.events / 'markers.jsonl'
        return [] if not path.exists() else [json.loads(v) for v in path.read_text().splitlines()]

    def outputs(self, record):
        result = []
        for name in ['stdout', 'stderr']:
            item = record[name]
            body = Path(item['path']).read_bytes()
            assert len(body) == item['bytes'] and digest(body) == item['sha256']
            result.append(body)
        return result

    def error(self, request, code, *, field=None, raw=None):
        before = (self.markers(), snapshot(self.state / 'cache'), self.profiles_path.read_bytes())
        exit_code, value, _ = self.invoke(request, raw=raw)
        assert value['error']['code'] == code, value
        assert exit_code in (1, 2, 3) and value['status'] not in ('passed', 'published')
        if field is not None:
            assert value['error']['field'] == field
        assert value['error']['cause'] and value['error']['reason'] and value['error']['recovery']
        assert (self.markers(), snapshot(self.state / 'cache'), self.profiles_path.read_bytes()) == before
        return value['error']

    def declaration(self, paths, required=None):
        head = git(self.checkout, 'rev-parse', 'HEAD')
        return {'version': 1, 'repository_root': str(self.checkout),
                'baseline_commit': git(self.checkout, 'rev-parse', 'HEAD^'),
                'candidate_commit': head, 'candidate_tree': git(self.checkout, 'rev-parse', 'HEAD^{tree}'),
                'paths': paths, 'required_paths': paths if required is None else required}

    def documentation(self, mode='committed', name='sample'):
        value = self.profile(name, marker=name, cache={'enabled': False, 'input_paths': ['docs'],
                     'environment': [], 'input_selection': 'filesystem-glob'},
                     subject={'kind': 'documentation', 'version': 1, 'mode': mode,
                              'mode_argument': '--subject-mode',
                              'environment_key': 'POISE_CHECK_DOCUMENTATION_SCOPE_JSON'})
        value['argv'] += ['--subject-mode', mode]
        return value
