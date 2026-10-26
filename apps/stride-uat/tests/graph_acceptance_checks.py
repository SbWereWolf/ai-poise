"""Test-owned public CLI arrangement and independent route/verdict observations."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'apps/stride-uat'
EXECUTION = ROOT / 'apps/execution/bin/execution'
CASE = APP / 'acceptance/graph_cases.py'
FIX = APP / 'tests/fixtures/graph-acceptance'
LOG = []
GROUPS = ['full_graph', 'template_graph', 'edited_graph', 'invalid_graph', 'false_effect']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def bind(value, coordinates):
    if isinstance(value, str):
        return coordinates.get(value, value)
    if isinstance(value, list):
        return [bind(item, coordinates) for item in value]
    if isinstance(value, dict):
        return {key: bind(item, coordinates) for key, item in value.items()}
    return value


def invoke(argv, packet, *, cwd=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    env['PATH'] = str(Path(sys.executable).parent) + ':/usr/bin:/bin'
    proc = subprocess.run(list(map(str, argv)), input=json.dumps(packet) + '\n',
                          text=True, capture_output=True, check=False, cwd=cwd, env=env)
    LOG.append({'argv': list(map(str, argv)), 'input': packet,
                'exit_code': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
    # Decoder/transport failures are ERROR, never an intended graph rejection.
    if proc.stderr or not proc.stdout.endswith('\n'):
        raise RuntimeError('Public CLI transport failed: ' + repr(LOG[-1]))
    body = json.loads(proc.stdout)
    if not isinstance(body, dict):
        raise ValueError('Public response is not an object')
    return proc.returncode, body


def public(config, packet):
    return invoke([sys.executable, '-B', EXECUTION, '--config', config], packet)


class Installation:
    """Only own temporary roots; current generic UAT provider stays unchanged."""

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='t058-graph-')
        self.root = Path(self.temp.name)
        self.root.chmod(0o755)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir(mode=0o755)
        self.test = self.root / 'test'
        self.test.mkdir(mode=0o711)
        self.protected = self.root / 'protected'
        self.protected.mkdir(mode=0o700)
        self.program_path = self.inputs / 'program.json'
        self.graph_path = self.inputs / 'graph.json'
        self.plan_path = self.inputs / 'plan.json'
        self.config_path = self.root / 'uat.json'
        self.state = self.test / 'state'
        self.workspace = self.test / 'workspace'
        return self

    def __exit__(self, *args):
        self.temp.cleanup()

    def arrange(self, scenario='full', *, prerequisite=False, false_effect=False):
        self.plan = deepcopy(read(FIX / 'plans.json')[scenario])
        write(self.plan_path, self.plan)
        write(self.graph_path, read(FIX / 'graphs.json')[scenario])
        adapter = FIX / 'prerequisite_case.py' if prerequisite else CASE
        arguments = ['${context}'] if prerequisite else [
            '--context', '${context}', '--scenario', scenario, '--plan', str(self.plan_path)]
        public_argv = [sys.executable, '-B', str(EXECUTION)]
        if false_effect:
            public_argv = [sys.executable, '-B', str(FIX / 'cli_forwarder.py'), str(EXECUTION)]
        release = platform.freedesktop_os_release()
        roots = [str(self.inputs), str(ROOT / 'apps'), str(Path(sys.executable).parent),
                 str(Path(sys._base_executable).parent), '/usr/bin']
        graphs = [] if prerequisite else [
            {'id': 'selected', 'input_id': 'selected', 'required_step_ids': ['case']}]
        coordinates = {
            '@STATE@': str(self.state), '@WORKSPACE@': str(self.workspace),
            '@INPUT_ROOTS@': list(dict.fromkeys(roots)),
            '@OS_ID@': release['ID'], '@OS_VERSION@': release['VERSION_ID'],
            '@PATH@': str(Path(sys.executable).parent) + ':/usr/bin:/bin',
            '@PYTHON@': sys.executable, '@ADAPTER@': str(adapter),
            '@PUBLIC_ARGV@': public_argv, '@PLAN@': str(self.plan_path),
            '@EXECUTION@': str(EXECUTION.parents[1]),
            '@FORWARDER@': str(FIX / 'cli_forwarder.py'), '@GRAPH@': str(self.graph_path),
            '@TEST@': str(self.test), '@PROTECTED@': str(self.protected),
            '@LEASE@': str(self.test / 'leases'), '@GRAPHS@': graphs,
            '@ARGUMENTS@': arguments, '@GRAPH_ID@': None if prerequisite else 'selected'}
        self.config = bind(read(FIX / 'uat-config.json'), coordinates)
        self.program = bind(read(FIX / 'program.json'), coordinates)
        write(self.config_path, self.config)
        write(self.program_path, self.program)
        LOG.append({'arrangement': {'scenario': scenario, 'prerequisite': prerequisite,
                                    'false_effect': false_effect, 'config': self.config,
                                    'program': self.program, 'plan': self.plan,
                                    'graph': read(self.graph_path)}})

    def run(self):
        code, result = invoke([sys.executable, '-B', APP / 'bin/stride-uat',
                               '--config', self.config_path],
                              {'schema': 'stride-uat/v1', 'operation': 'run', 'input': {
                                  'run_id': 'graph-case', 'program_path': str(self.program_path),
                                  'profile_id': 'local', 'step_limit': 0}})
        if result.get('status') != 'reported':
            raise RuntimeError('UAT preparation/transport was rejected: ' + repr(result))
        protocol = read(result['protocol_path'])
        LOG.append({'uat_protocol': protocol})
        for step in protocol['steps']:
            for channel, item in step['outputs'].items():
                if item is not None:
                    LOG.append({'captured_channel': channel, 'path': item['path'],
                                'content': Path(item['path']).read_text()})
            if step.get('graph'):
                for check in step['graph']['checks']:
                    for evidence in check['evidence_paths']:
                        LOG.append({'graph_evidence': evidence['path'],
                                    'content': Path(evidence['path']).read_text()})
        return code, result, protocol

    @property
    def subject(self):
        return self.workspace / 'graph-case/case' / self.plan['instance_directory'] / 'execution.json'


def prerequisites():
    if sys.version_info[:2] != (3, 13) or os.geteuid() != 0:
        raise RuntimeError('Declared Ubuntu root / CPython3.13 test runtime is required')
    for entry in [EXECUTION, APP / 'bin/stride-uat', Path('/usr/bin/git'), Path('/usr/bin/setpriv')]:
        if not entry.is_file():
            raise RuntimeError('Missing existing runtime prerequisite: ' + str(entry))
    with Installation() as case:
        # Actual existing Execution query qualifies the public wire separately.
        config = case.root / 'execution.json'
        write(config, read(FIX / 'plans.json')['full']['execution_config'])
        code, result = public(config, {'schema': 'stride-execution/v1', 'operation': 'query',
                                     'input': {'queries': [{'id': 'absent', 'kind': 'execution',
                                                            'task_id': '2999'}]}})
        if code != 2 or result.get('status') != 'rejected' or 'execution_not_found' not in str(result):
            raise RuntimeError('Existing Execution wire prerequisite failed: ' + repr(result))
        case.arrange(prerequisite=True)
        code, result, protocol = case.run()
        if code != 0 or result.get('verdict') != 'PASS' or result['graph_results'] != []:
            raise RuntimeError('Actual non-graph UAT setup prerequisite failed: ' + repr(result))
        stdout = Path(protocol['steps'][0]['outputs']['stdout']['path']).read_text()
        if json.loads(stdout) != {'uid': 62058, 'gid': 1000, 'graph_acceptance': False}:
            raise RuntimeError('Expected actual separate test identity was not observed')
    qualify_literal_inputs()
    return {'passed': True,
            'scope': 'existing public wire, non-graph UAT setup and literal input validity; graph cases unexecuted'}


def qualify_literal_inputs():
    """Qualify test inputs at the existing owner, not the undelivered cases."""
    graphs = read(FIX / 'graphs.json')
    plans = read(FIX / 'plans.json')
    for name in ['full', 'template', 'edited', 'invalid']:
        with tempfile.TemporaryDirectory(prefix='t058-input-') as directory:
            config = Path(directory) / 'execution.json'
            write(config, plans[name]['execution_config'])
            source = {'kind': 'definition', 'definition': graphs[name]}
            if name == 'edited':
                source = {'kind': 'definition', 'definition': plans[name]['baseline']}
            if name == 'template':
                write(Path(directory) / 'state/templates/chosen.json', graphs[name])
                source = {'kind': 'template', 'template': {'id': 'chosen'}}
            code, value = public(config, {'schema': 'stride-execution/v1', 'operation': 'process',
                                         'input': {'action': 'create', 'request_id': 'literal-input',
                                                   'expected_revision': 0, 'goal_type': 'casework',
                                                   'source': source, 'changes': []}})
            expected = 'rejected' if name == 'invalid' else 'published'
            if value.get('status') != expected or code != (2 if name == 'invalid' else 0):
                raise RuntimeError('Literal input qualification failed: ' + repr((name, value)))
            if name == 'invalid' and 'absent-phase' not in value.get('reason', ''):
                raise RuntimeError('Invalid literal did not reach its intended graph-semantic refusal')
            if name == 'edited':
                code, changed = public(config, {
                    'schema': 'stride-execution/v1', 'operation': 'process', 'input': {
                        'action': 'edit', 'request_id': 'literal-edit',
                        'expected_revision': value['revision'],
                        'expected_process_revision': value['process_revision'],
                        'goal_type': 'casework', 'changes': plans[name]['changes']}})
                if code != 0 or changed.get('status') != 'published':
                    raise RuntimeError('Literal named edit is not valid: ' + repr(changed))
                # This is fixture qualification only. The acceptance oracle below
                # observes the actual scenario through independent public queries.
                if read(Path(directory) / 'state/processes/casework.json') != graphs[name]:
                    raise RuntimeError('Declared literal final graph differs from its named edit')


def check_routes(case, expected):
    observed = {}
    for task_id, route in expected.items():
        code, value = public(case.subject, {'schema': 'stride-execution/v1', 'operation': 'query',
                                          'input': {'queries': [
                                              {'id': 'execution', 'kind': 'execution', 'task_id': task_id},
                                              {'id': 'progression', 'kind': 'progression', 'task_id': task_id}]}})
        assert code == 0 and value['status'] == 'read_only', value
        state = value['results'][0]['value']
        progression = value['results'][1]['result']
        assert state['state'] == 'verified' and state['current_stage'] == route[-1], state
        visits = [item['stage_id'] for item in progression['accepted_visits']]
        assert visits == route, (visits, route)
        assert [item['stage_id'] for item in progression['traversals']] == route
        observed[task_id] = {'state': state, 'visits': visits}
    return observed


def run_case(scenario, *, false_effect=False):
    with Installation() as case:
        case.arrange(scenario, false_effect=false_effect)
        code, response, protocol = case.run()
        expected = 'FAIL' if false_effect else case.plan['expected_verdict']
        assert response['verdict'] == expected and code == (0 if expected == 'PASS' else 1), response
        assert response['completed_steps'] == response['total_steps'] == 1
        assert response['graph_results'][0]['graph_id'] == 'selected'
        assert response['graph_results'][0]['verdict'] == expected
        checks = protocol['steps'][0]['graph']['checks']
        assert checks and all(item['evidence_paths'] for item in checks)
        if expected == 'PASS':
            assert all(item['status'] == 'PASS' for item in checks)
        else:
            assert any(item['status'] == 'FAIL' for item in checks)
        workspace = case.workspace / 'graph-case/case'
        observations = workspace / case.plan['observation_file']
        calls = [json.loads(line) for line in observations.read_text().splitlines()]
        assert calls and all({'argv', 'input', 'exit_code', 'stdout', 'stderr'} <= call.keys() for call in calls)
        LOG.append({'actual_case_calls': calls})
        operations = {call['input']['operation'] for call in calls}
        assert 'process' in operations
        if false_effect:
            forwarded = [json.loads(line) for line in (case.subject.parent / 'forwarder.jsonl').read_text().splitlines()]
            assert any(item['changed'] for item in forwarded), 'sensitivity control did not reach real public state'
            assert any(item['changed'] and item['exit_code'] == 0 for item in forwarded)
            LOG.append({'forwarding_control': forwarded})
            routes = None
        elif scenario == 'invalid':
            rejected = [call for call in calls if call['input']['operation'] == 'process'
                        and call['exit_code'] == 2]
            assert rejected and any('absent-phase' in call['stdout'] for call in rejected)
            before = [json.loads(call['stdout']) for call in calls
                      if call['input']['operation'] == 'process'
                      and call['input']['input']['action'] == 'status' and call['exit_code'] == 0]
            assert before, 'missing recorded public prior valid state'
            status_code, status = public(case.subject, {'schema': 'stride-execution/v1', 'operation': 'process',
                                                       'input': {'action': 'status', 'goal_type': 'casework'}})
            assert status_code == 0 and status == before[0], (status, before[0])
            routes = None
        else:
            assert {'apply', 'lifecycle', 'gate', 'attempt', 'advance', 'query'} <= operations, operations
            actions = [call['input']['input']['action'] for call in calls if call['input']['operation'] == 'process']
            assert 'create' in actions
            if scenario == 'template':
                assert any(call['input']['operation'] == 'process' and
                           call['input']['input'].get('source', {}).get('kind') == 'template' for call in calls)
            if scenario == 'edited':
                assert 'edit' in actions
            routes = check_routes(case, case.plan['expected_routes'])
        return {'scenario': scenario, 'false_effect': false_effect, 'passed': True,
                'verdict': response['verdict'], 'independent_public_routes': routes}
