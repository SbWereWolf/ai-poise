"""Test-owned orchestration and literal oracles; never import product modules."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'apps/poise'
FIX = APP / 'tests/fixtures/generic'
PEER = FIX / 'peer.py'
PY = sys.executable
LOG = []


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def bind(value, coordinates):
    if isinstance(value, str):
        return coordinates.get(value, value)
    if isinstance(value, list):
        return [bind(item, coordinates) for item in value]
    if isinstance(value, dict):
        return {key: bind(item, coordinates) for key, item in value.items()}
    return value


def process(argv, value=None, *, raw=None, cwd=None, environment=None):
    payload = raw if raw is not None else json.dumps(value, ensure_ascii=False) + '\n'
    child = subprocess.run(list(map(str, argv)), input=payload, text=True,
                           capture_output=True, cwd=cwd, env=environment, check=False)
    record = {'argv': list(map(str, argv)), 'input': payload, 'cwd': str(cwd),
              'environment': environment, 'exit_code': child.returncode,
              'stdout': child.stdout, 'stderr': child.stderr}
    LOG.append(record)
    return child, record


def peer_argv(mode, *arguments):
    return [PY, '-I', '-S', '-B', str(PEER), mode, *map(str, arguments)]


class ExecutionFixture:
    """Prepare only a test-owned public Execution consumer, never inspect its DB."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir()
        self.repository = self.root / 'repository'
        self.repository.mkdir()
        for arguments in [('init', '-b', 'case-base'),
                          ('-c', 'user.name=Generic fixture', '-c', 'user.email=fixture@example.invalid',
                           'commit', '--allow-empty', '-m', 'Initialize public fixture')]:
            result, _ = process(['git', '-C', self.repository, *arguments], raw='')
            if result.returncode:
                raise RuntimeError('Own external Execution repository prerequisite failed')
        self.tree = subprocess.check_output(['git', '-C', self.repository, 'rev-parse',
                                             'HEAD^{tree}'], text=True).strip()
        config = bind(read(FIX / 'execution-config.json'), {
            '@REPOSITORY@': str(self.repository), '@ATTEMPTS@': str(self.root / 'attempts')})
        self.config = self.root / 'execution.json'
        write(self.config, config)
        self.argv = [PY, '-I', '-S', '-B', str(ROOT / 'apps/execution/bin/execution'),
                     '--config', str(self.config)]
        self.revision = 0
        created = self.call('process', {'action': 'create', 'goal_type': 'generic-context',
            'request_id': str(uuid4()), 'expected_revision': self.revision,
            'source': {'kind': 'definition', 'definition': read(FIX / 'execution-graph.json')},
            'changes': []})
        contract = bind(read(FIX / 'execution-contract.json'), {'@PROCESS@': created['process_revision']})
        self.call('apply', {'request_id': str(uuid4()), 'expected_revision': self.revision,
            'operations': [{'id': 'own-context', 'kind': 'setup_execution', 'contract': contract}]})
        self.action('freeze', requirements_snapshot=read(FIX / 'execution-snapshot.json'))
        self.action('enter', entry_tree=self.tree)
        self.query_packet = {'schema': 'stride-execution/v1', 'operation': 'query',
            'input': {'queries': [{'id': 'context', 'kind': 'execution', 'task_id': '6142'}]}}
        self.expected = self.call('query', self.query_packet['input'])
        state = self.expected['results'][0]['value']
        if state['task_id'] != '6142' or state['current_stage'] != 'observe':
            raise RuntimeError('Literal external context fixture did not establish its selected state')

    def call(self, operation, value):
        child, _ = process(self.argv, {'schema': 'stride-execution/v1',
                                     'operation': operation, 'input': value})
        if child.returncode or child.stderr:
            raise RuntimeError('Public Execution fixture prerequisite: ' + child.stdout + child.stderr)
        body = json.loads(child.stdout)
        self.revision = body.get('revision', self.revision)
        return body

    def action(self, action, **fields):
        state = self.call('query', {'queries': [{'id': 'own', 'kind': 'execution', 'task_id': '6142'}]})
        current = state['results'][0]['value']
        return self.call('lifecycle', {'action': action, 'request_id': str(uuid4()),
            'expected_revision': self.revision, 'task_id': '6142',
            'expected_generation': current['generation'],
            'expected_contract_revision': current['contract_revision'],
            'actor_id': 'observer-A', **fields})


class Installation:
    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='t059-generic-')
        self.root = Path(self.temporary.name)
        self.install = self.root / 'standalone-app'
        shutil.copytree(APP, self.install,
                        ignore=shutil.ignore_patterns('tests', 'docs', 'fixtures', 'config', '__pycache__'))
        self.project = self.root / 'ordinary-project'
        self.project.mkdir()
        shutil.copyfile(FIX / 'sample.py', self.project / 'sample.py')
        self.trace = self.root / 'peer.jsonl'
        self.config = bind(read(FIX / 'config.json'), {'@RESULTS@': str(self.root / 'results')})
        self.config_path = self.root / 'generic.json'
        self.instructions = self.root / 'instruction-root'
        self.catalog = self.root / 'catalog.json'
        self.policy = self.root / 'policy.json'
        write(self.catalog, read(FIX / 'catalog.json'))
        write(self.policy, read(FIX / 'policy.json'))
        mapping = read(FIX / 'material-files.json')
        for target, source in mapping['checkout'].items():
            path = self.project / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((FIX / 'materials' / source).read_bytes())
        for target, source in mapping['instructions'].items():
            path = self.instructions / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((FIX / 'materials' / source).read_bytes())
        self.calls = []
        return self

    def __exit__(self, *args):
        self.temporary.cleanup()

    def call(self, operation, value, *, raw=None):
        write(self.config_path, self.config)
        packet = {'schema': 'poise-generic/v1', 'operation': operation, 'input': value}
        child, record = process([PY, '-I', '-S', '-B', self.install / 'bin/poise-generic',
                                 '--config', self.config_path], packet, raw=raw, cwd=self.project,
                                environment={'PATH': str(Path(PY).parent) + ':/usr/bin:/bin',
                                             'LANG': 'C.UTF-8'})
        body = json.loads(child.stdout)
        # Capture complete actual files before assertions and temporary retirement.
        record['decoded'] = body
        record['captured_evidence'] = []
        for item in body.get('evidence', []):
            assert set(item) == {'kind', 'path'}, item
            assert item['kind'] in ('stdout', 'stderr', 'receipt', 'result')
            path = Path(item['path'])
            assert path.is_absolute() and path.is_file(), item
            record['captured_evidence'].append({**item, 'content': path.read_bytes().decode('utf-8')})
        if self.trace.exists():
            record['peer_transcript'] = self.trace.read_text()
        self.calls.append(record)
        assert child.stderr == '' and child.stdout.endswith('\n'), record
        assert set(body) == {'schema', 'status', 'result', 'error', 'evidence'}, body
        assert body['schema'] == 'poise-generic/v1'
        assert body['result'] is None or isinstance(body['result'], dict)
        outcomes = {'ok': 0, 'unavailable': 1, 'needs_inputs': 1, 'rejected': 2}
        assert body['status'] in outcomes, body
        assert child.returncode == outcomes[body['status']], record
        if body['status'] == 'ok':
            assert body['error'] is None and isinstance(body['result'], dict), body
        else:
            assert isinstance(body['error'], dict), body
        if body['error'] is not None:
            assert set(body['error']) == {'code', 'details', 'action'}
            assert isinstance(body['error']['details'], dict)
            assert body['error']['code'] and body['error']['action']
        return child.returncode, body

    def context(self, external=None):
        return {'checkout': str(self.project), 'facts': ['selected-project'], 'external': external}

    def external(self, mode):
        return {'argv': peer_argv(mode), 'input': {}, 'cwd': str(self.project),
                'environment': {'TRACE': str(self.trace)}}

    def probe(self, identity='working', mode='command'):
        value = bind(read(FIX / 'probe.json'), {'@PYTHON@': PY, '@PEER@': str(PEER),
                                              '@TRACE@': str(self.trace)})
        value['id'] = identity
        if mode != 'command':
            value['argv'] = peer_argv(mode)
        return value

    def mcp(self, *, call=True, mode=None):
        value = self.probe('mcp')
        value.update(kind='mcp_stdio', argv=peer_argv('mcp', *([mode] if mode else [])),
                     stdout_contains=[], stderr_contains=[], project_bound=call,
                     json_assertions=[], tool_ref='fixture.mcp', mcp=read(FIX / 'mcp.json'))
        if call:
            value['mcp']['call'] = {'name': 'find_symbol', 'arguments': {
                'checkout': '${workspace}', 'name': 'candidate'}, 'read_only': True}
            value['json_assertions'] = read(FIX / 'mcp-assertions.json')
        return value

    def skills(self):
        return {'catalog': str(self.catalog), 'instruction_root': str(self.instructions)}

    def preparation(self, paths=None, previous=None):
        return {**self.skills(), 'project': 'project-A', 'checkout': str(self.project),
                'policy': str(self.policy), 'paths': paths or ['src/billing/invoice.py'],
                'previous': previous}

    def provider(self, kind, operation='symbol', *, available=True):
        probe = self.probe(kind)
        probe['argv'] = peer_argv('navigation', '${workspace}', operation, kind) if available else peer_argv('refuse')
        probe.update(stdout_contains=[], stderr_contains=[], json_assertions=[], project_bound=False)
        return {'id': kind, 'kind': kind, 'operations': ['symbol', 'structural'],
                'identity_path': ['checkout'], 'probe': probe}

    def evidence_text(self, kind):
        return '\n'.join(item['content'] for item in self.calls[-1]['captured_evidence']
                         if item['kind'] == kind)

    def error(self, body, code, *, path=None):
        expected = bind(read(FIX / 'errors.json')[code], {'@PATH@': path})
        assert body['error'] == expected, (body, expected)


def prerequisites():
    if sys.version_info[:2] != (3, 13) or not shutil.which('git'):
        raise RuntimeError('Actual Python3.13/Git prerequisites required')
    for name in ['config.json', 'catalog.json', 'policy.json', 'materials-expected.json',
                 'deltas.json', 'errors.json', 'execution-graph.json', 'execution-contract.json']:
        read(FIX / name)
    with tempfile.TemporaryDirectory(prefix='t059-prerequisites-') as directory:
        root = Path(directory)
        child, _ = process(peer_argv('command', root), raw='',
                           environment={'FIXTURE_VALUE': 'explicit'})
        value = json.loads(child.stdout)
        if child.returncode or value['checkout'] != str(root) or value['environment'] != 'explicit':
            raise RuntimeError('Actual command fixture did not qualify')
        if len(child.stdout.encode()) <= 5000 or 'COMMAND-DETAIL' not in child.stderr:
            raise RuntimeError('Literal complete-stream fixture did not qualify')
        requests = bind(read(FIX / 'mcp-exchange.json'), {'@CHECKOUT@': str(root)})
        child, _ = process(peer_argv('mcp'), raw=''.join(json.dumps(v) + '\n' for v in requests))
        replies = [json.loads(line) for line in child.stdout.splitlines()]
        if child.returncode or [v['id'] for v in replies] != [1, 2, 3, 4]:
            raise RuntimeError('Actual MCP exchange prerequisite failed')
        if replies[-1]['result']['structuredContent']['symbol'] != {'name': 'candidate', 'line': 1}:
            raise RuntimeError('Literal MCP call result prerequisite failed')
        execution = ExecutionFixture(root / 'external-owner')
        return {'python': sys.version, 'command': 'actual qualified', 'mcp': 'actual qualified',
                'external_state': execution.expected, 'future_semantic_groups': 'not executed by prerequisites'}


def context_checks():
    with Installation() as app:
        code, body = app.call('context', app.context())
        assert code == 0 and body['status'] == 'ok' and body['error'] is None, body
        assert body['result']['checkout'] == str(app.project)
        assert body['result']['facts'] == ['selected-project']
        assert body['result']['installation_source'] == str(app.install)
        assert body['result']['external'] is None
        other = app.root / 'second-project'
        other.mkdir()
        request = {'checkout': str(other), 'facts': ['other-project'], 'external': None}
        code, second = app.call('context', request)
        assert code == 0 and second['result']['checkout'] == str(other)
        assert second['result']['facts'] == ['other-project']
        owner = ExecutionFixture(app.root / 'explicit-external-owner')
        external = {'argv': owner.argv, 'input': owner.query_packet,
                    'cwd': str(owner.root), 'environment': {}}
        code, body = app.call('context', app.context(external))
        assert code == 0 and body['result']['external']['response'] == owner.expected, body
        assert body['result']['external']['exit_code'] == 0
        assert json.loads(app.evidence_text('stdout'))['results'][0]['value']['current_stage'] == 'observe'
        assert app.evidence_text('receipt')
        code, body = app.call('context', app.context(app.external('refuse')))
        assert code == 1 and body['status'] == 'unavailable' and body['error'], body
        assert body['result']['checkout'] == str(app.project)
        assert body['result']['external']['exit_code'] == 7
        assert body['result']['external']['response']['status'] == 'ordinary-unavailable'
        assert 'REFUSAL-DETAIL' in app.evidence_text('stderr')
        code, body = app.call('context', app.context())
        assert code == 0 and body['result']['external'] is None


def command_checks():
    with Installation() as app:
        request = {'workspace': str(app.project), 'probes': [app.probe()]}
        code, body = app.call('capabilities', request)
        assert code == 0 and body['result']['ready'] is True, body
        observed = body['result']['observations'][0]
        assert observed['status'] == 'available' and observed['observation'] == 'command_executed'
        output = json.loads(app.evidence_text('stdout'))
        assert output['checkout'] == str(app.project) and output['environment'] == 'explicit'
        assert output['body'] == 'Данные' * 1200
        assert 'COMMAND-DETAIL\n' + 'e' * 2000 in app.evidence_text('stderr')
        failed = app.probe('missing', 'refuse')
        failed.update(stdout_contains=[], stderr_contains=[], json_assertions=[], project_bound=False)
        request['probes'] = [failed, app.probe('available')]
        code, body = app.call('capabilities', request)
        assert code == 1 and body['result']['ready'] is False
        assert body['result']['missing_required'] == ['missing']
        assert [v['status'] for v in body['result']['observations']] == ['unavailable', 'available']
        assert 'REFUSAL-DETAIL' in app.evidence_text('stderr')
        failed['required'] = False
        code, body = app.call('capabilities', {'workspace': str(app.project), 'probes': [failed]})
        assert code == 0 and body['result']['ready'] is True
        assert body['result']['observations'][0]['status'] == 'unavailable'
        mismatch = app.probe('different-observation')
        mismatch['project_bound'] = False
        mismatch['json_assertions'] = [{'path': ['checkout'], 'equals': str(app.root / 'other')}]
        code, body = app.call('capabilities', {'workspace': str(app.project), 'probes': [mismatch]})
        assert code == 1 and body['result']['observations'][0]['status'] == 'unavailable'


def mcp_checks():
    with Installation() as app:
        for called, expected in [(False, 'mcp_tools_listed'), (True, 'mcp_smoke_passed')]:
            app.trace.unlink(missing_ok=True)
            code, body = app.call('capabilities', {'workspace': str(app.project),
                                                  'probes': [app.mcp(call=called)]})
            assert code == 0 and body['result']['ready'] is True, body
            value = body['result']['observations'][0]
            assert value['observation'] == expected and value['version'] == '2.4'
            assert value['tools'] == ['find_symbol', 'describe_project']
            requests = [v['rpc'] for v in map(json.loads, app.trace.read_text().splitlines()) if 'rpc' in v]
            methods = [v['method'] for v in requests]
            assert methods[:4] == ['initialize', 'notifications/initialized', 'tools/list', 'tools/list']
            assert ('tools/call' in methods) is called
            if called:
                actual = next(v for v in requests if v['method'] == 'tools/call')
                assert actual['params'] == {'name': 'find_symbol', 'arguments': {
                    'checkout': str(app.project), 'name': 'candidate'}}
                assert 'structuredContent' in app.evidence_text('stdout')
        for mode in ['missing', 'call-fails']:
            code, body = app.call('capabilities', {'workspace': str(app.project),
                                                  'probes': [app.mcp(mode=mode)]})
            assert code == 1 and body['result']['ready'] is False
            assert body['result']['observations'][0]['status'] == 'unavailable'
            assert body['error'] and app.evidence_text('receipt')


def catalogue_checks():
    with Installation() as app:
        expected = read(FIX / 'catalog-expected.json')
        code, body = app.call('skills.list', app.skills())
        assert code == 0 and body['result']['skills'] == expected
        raw = read(app.catalog)
        raw['skills'].reverse()
        write(app.catalog, raw)
        code, other = app.call('skills.list', app.skills())
        assert code == 0 and other['result'] == body['result']
        for ids, answer in [(['workflow', 'backend'], [expected[0], expected[2]]), ([], [])]:
            code, selected = app.call('skills.read', {**app.skills(), 'skill_ids': ids})
            assert code == 0 and selected['result']['skills'] == answer
        code, body = app.call('skills.read', {**app.skills(), 'skill_ids': ['unknown']})
        assert code == 2 and body['status'] == 'rejected' and body['error']
        alternate = deepcopy(raw)
        alternate['skills'][0]['purpose'] = 'Distinct second catalogue.'
        second = app.root / 'second-catalog.json'
        write(second, alternate)
        code, body = app.call('skills.list', {**app.skills(), 'catalog': str(second)})
        assert code == 0 and any(v['purpose'] == 'Distinct second catalogue.' for v in body['result']['skills'])
        # Changing an instruction body leaves catalogue metadata unchanged.
        (app.instructions / '.agents/skills/backend/SKILL.md').write_text('Changed body only.\n')
        code, body = app.call('skills.list', app.skills())
        assert code == 0 and body['result']['skills'] == expected


def instruction_checks():
    with Installation() as app:
        expected = read(FIX / 'materials-expected.json')
        deltas = read(FIX / 'deltas.json')
        code, body = app.call('skills.prepare', app.preparation())
        assert code == 0 and body['result']['status'] == 'ready', body
        before = body['result']
        assert before['materials'] == expected['billing']
        assert len(next(v['content'] for v in before['materials'] if v['id'] == 'AGENTS.md')) > 5000
        assert '\r\n' in before['materials'][0]['content']
        paths = ['src/billing/invoice.py', 'docs/guide.md', 'src/billing/invoice.py']
        code, body = app.call('skills.prepare', app.preparation(paths))
        assert code == 0 and body['result']['materials'] == expected['multi']
        code, body = app.call('skills.prepare', app.preparation(['docs/guide.md'], before))
        assert code == 0 and body['result']['materials'] == expected['docs']
        assert body['result']['delta'] == deltas['docs']
        code, repeated = app.call('skills.prepare', app.preparation(['docs/guide.md'], body['result']))
        assert code == 0 and repeated['result']['delta'] == deltas['unchanged']
        backend = app.instructions / '.agents/skills/backend/SKILL.md'
        backend.write_bytes((FIX / 'materials/backend-changed.md').read_bytes())
        code, changed = app.call('skills.prepare', app.preparation(previous=before))
        assert code == 0 and changed['result']['delta'] == deltas['content']
        actual = next(v for v in changed['result']['materials'] if v['id'] == 'backend')
        assert actual['content'] == (FIX / 'materials/backend-changed.md').read_bytes().decode()
        (app.instructions / '.agents/skills/testing/SKILL.md').unlink()
        code, optional = app.call('skills.prepare', app.preparation())
        assert code == 0 and optional['result']['status'] == 'ready'
        assert any(not v['available'] and not v['required'] for v in optional['result']['materials'])
        backend.unlink()
        code, missing = app.call('skills.prepare', app.preparation())
        assert code == 1 and missing['status'] == 'needs_inputs'
        assert missing['result']['status'] == 'needs_inputs' and missing['result']['diagnostics']
        other = app.root / 'other-project'
        shutil.copytree(app.project, other)
        (other / 'AGENTS.md').write_text('Distinct second project rule.\n')
        request = {**app.preparation(['docs/guide.md']), 'project': 'project-B', 'checkout': str(other)}
        code, second = app.call('skills.prepare', request)
        assert code == 0 and second['result']['project'] == 'project-B'
        assert next(v['content'] for v in second['result']['materials'] if v['id'] == 'AGENTS.md') == 'Distinct second project rule.\n'


def navigation_checks():
    with Installation() as app:
        request = {'checkout': str(app.project), 'operation': 'symbol', 'files': ['sample.py'],
                   'providers': [app.provider('ide'), app.provider('ast')]}
        assert not (app.project / 'pyproject.toml').exists()
        code, body = app.call('navigation', request)
        assert code == 0 and body['result']['status'] == 'proved' and body['result']['provider'] == 'ide'
        assert body['result']['checkout'] == str(app.project) and body['result']['scope'] == ['sample.py']
        assert json.loads(app.evidence_text('stdout'))['symbol'] == {'name': 'candidate', 'line': 1}
        assert [v['provider'] for v in body['result']['observations']] == ['ide']
        (app.project / 'sample.py').write_bytes(b'\n' + (FIX / 'sample.py').read_bytes())
        code, body = app.call('navigation', request)
        assert code == 0 and json.loads(app.evidence_text('stdout'))['symbol'] == {'name': 'candidate', 'line': 2}
        shutil.copyfile(FIX / 'sample.py', app.project / 'sample.py')
        request['providers'][0] = app.provider('ide', available=False)
        code, body = app.call('navigation', request)
        assert code == 0 and body['result']['provider'] == 'ast'
        assert [v['status'] for v in body['result']['observations']] == ['unavailable', 'available']
        request.update(operation='structural', providers=[app.provider('ide', 'structural'), app.provider('ast', 'structural')])
        code, body = app.call('navigation', request)
        assert code == 0 and body['result']['provider'] == 'ast'
        assert [v['provider'] for v in body['result']['observations']] == ['ast']
        for operation in ['inspection', 'documentation']:
            request.update(operation=operation, providers=[app.provider('ast', operation)])
            request['providers'][0]['operations'].append(operation)
            code, body = app.call('navigation', request)
            assert code == 1 and body['result']['status'] == 'unavailable'
            assert body['result']['observations'] == []
        assert (app.project / 'sample.py').read_bytes() == (FIX / 'sample.py').read_bytes()


def boundary_checks():
    with Installation() as app:
        original = deepcopy(app.config)
        app.config['operator_note'] = {'ordinary': 'retained'}
        code, body = app.call('context', app.context())
        assert code == 0 and read(app.config_path) == app.config
        variants = [('provider_output_bytes', None), ('batch_count', True), ('batch_count', 0)]
        for field, value in variants:
            app.config = deepcopy(original)
            if value is None:
                del app.config['limits'][field]
            else:
                app.config['limits'][field] = value
            code, body = app.call('context', app.context())
            assert code == 2 and body['result'] is None and body['evidence'] == []
            app.error(body, 'invalid_config', path='/limits/' + field)
        app.config = deepcopy(original)
        cases = [('invalid_json', '{', None),
                 ('unsupported_schema', '{"schema":"other/v1","operation":"context","input":{}}', None),
                 ('invalid_request', '{"schema":"poise-generic/v1","operation":"unknown","input":{}}', '/operation')]
        for error, raw, path in cases:
            code, body = app.call('context', app.context(), raw=raw)
            assert code == 2 and body['result'] is None and body['evidence'] == []
            app.error(body, error, path=path)
        request = app.context()
        del request['checkout']
        code, body = app.call('context', request)
        assert code == 2
        app.error(body, 'invalid_request', path='/input/checkout')
        code, body = app.call('context', {**app.context(), 'unknown': 'value'})
        assert code == 2
        app.error(body, 'invalid_request', path='/input')
        app.config['limits']['packet_bytes'] = 32
        code, body = app.call('context', app.context())
        assert code == 2
        app.error(body, 'packet_limit')
        app.config = deepcopy(original)
        unicode_request = app.context()
        unicode_request['facts'] = ['кириллица-🧪']
        packet = {'schema': 'poise-generic/v1', 'operation': 'context', 'input': unicode_request}
        exact = json.dumps(packet, ensure_ascii=False, separators=(',', ':')) + '\n'
        byte_count = len(exact.encode('utf-8'))
        assert byte_count > len(exact)
        app.config['limits']['packet_bytes'] = byte_count
        code, body = app.call('context', unicode_request, raw=exact)
        assert code == 0 and body['result']['facts'] == ['кириллица-🧪']
        one_more = exact[:-1] + ' \n'
        assert len(one_more.encode('utf-8')) == byte_count + 1
        code, body = app.call('context', unicode_request, raw=one_more)
        assert code == 2
        app.error(body, 'packet_limit')
        app.config = deepcopy(original)
        app.config['limits']['input_bytes'] = 32
        code, body = app.call('skills.list', app.skills())
        assert code == 2
        app.error(body, 'input_limit', path=str(app.catalog))
        app.config = deepcopy(original)
        app.config['limits']['batch_count'] = 2
        app.trace.unlink(missing_ok=True)
        code, body = app.call('capabilities', {'workspace': str(app.project),
                              'probes': [app.probe(str(i)) for i in range(3)]})
        assert code == 2 and not app.trace.exists()
        app.error(body, 'batch_limit')
        for field, ceiling in [('timeout_seconds', 'provider_timeout_seconds'),
                               ('max_output_bytes', 'provider_output_bytes')]:
            app.config = deepcopy(original)
            probe = app.probe()
            probe[field] = app.config['limits'][ceiling] + 1
            code, body = app.call('capabilities', {'workspace': str(app.project), 'probes': [probe]})
            assert code == 2 and not app.trace.exists()
            app.error(body, 'invalid_request', path='/input/probes/0/' + field)
        app.config = deepcopy(original)
        later = app.probe('invalid-later')
        later['max_output_bytes'] = app.config['limits']['provider_output_bytes'] + 1
        code, body = app.call('capabilities', {'workspace': str(app.project),
                              'probes': [app.probe('would-run-first'), later]})
        assert code == 2 and not app.trace.exists()
        app.error(body, 'invalid_request', path='/input/probes/1/max_output_bytes')
        first, later = app.provider('ide'), app.provider('ast')
        later['probe']['max_output_bytes'] = app.config['limits']['provider_output_bytes'] + 1
        navigation = {'checkout': str(app.project), 'operation': 'symbol',
                      'files': ['sample.py'], 'providers': [first, later]}
        code, body = app.call('navigation', navigation)
        assert code == 2 and not app.trace.exists()
        app.error(body, 'invalid_request', path='/input/providers/1/probe/max_output_bytes')
        app.config['limits']['batch_count'] = 1
        navigation['providers'] = [app.provider('ide'), app.provider('ast')]
        code, body = app.call('navigation', navigation)
        assert code == 2 and not app.trace.exists()
        app.error(body, 'batch_limit')
        app.config = deepcopy(original)
        app.config['limits']['provider_timeout_seconds'] = 0.05
        code, body = app.call('context', app.context(app.external('timeout')))
        assert code == 1 and body['status'] == 'unavailable'
        app.error(body, 'provider_timeout')
        assert 'START\n' in app.evidence_text('stdout')
        assert 'TIMEOUT-DETAIL' in app.evidence_text('stderr')
        assert 'UNREACHED-END' not in app.evidence_text('stdout')
        entries = [json.loads(line) for line in app.trace.read_text().splitlines()]
        timeout_pid = next(v['pid'] for v in entries if v.get('mode') == 'timeout')
        try:
            os.kill(timeout_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError('Timed-out fixture process remains live')
        app.config = deepcopy(original)
        app.config['limits']['provider_output_bytes'] = 256
        code, body = app.call('context', app.context(app.external('large')))
        assert code == 1 and body['status'] == 'unavailable'
        app.error(body, 'provider_output_limit')
        output = app.evidence_text('stdout')
        assert json.loads(output) == {'body': 'Ж' * 3000, 'end': 'OUTPUT-END'}
        assert 'OUTPUT-DETAIL-END' in app.evidence_text('stderr')
        app.config = deepcopy(original)
        app.config['limits']['result_bytes'] = 32
        code, body = app.call('skills.list', app.skills())
        assert code == 1 and body['status'] == 'unavailable'
        app.error(body, 'result_limit')
        assert json.loads(app.evidence_text('result')) == {'skills': read(FIX / 'catalog-expected.json')}


GROUPS = [('context', context_checks), ('command', command_checks), ('mcp', mcp_checks),
          ('catalog', catalogue_checks), ('instructions', instruction_checks),
          ('navigation', navigation_checks), ('boundary', boundary_checks)]
