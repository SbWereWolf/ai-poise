"""Real public prerequisites and independent verification/reuse expectations."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

from outcome_checks import OutcomeSubject, DATA as OUTCOME, revision
from lifecycle_checks import bind, current_state
from process_checks import create, installation, write_json
from store_checks import subject

ROOT = Path(__file__).resolve().parents[3]
DATA = json.loads((ROOT / 'apps/execution/fixtures/verification-reuse-contract.json').read_text())
LOG = []


class MissingVerification(Exception):
    """Only the genuine old missing operation establishes initial RED."""


def raw(config, packet, application='execution', *, cwd=None):
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'apps' / application / 'bin' / application),
         '--config', str(config)], input=json.dumps(packet, ensure_ascii=False) + '\n',
        cwd=config.parent if cwd is None else cwd,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        text=True, capture_output=True,
    )
    LOG.append({'application': application, 'config': str(config),
                'caller_cwd': str(config.parent if cwd is None else cwd), 'packet': deepcopy(packet),
                'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    assert result.stderr == '', result.stderr
    assert result.stdout.endswith('\n') and result.stdout.count('\n') == 1, result.stdout
    return result.returncode, json.loads(result.stdout)


def rows(database):
    """Independent explicit-column state reads, not production projections."""
    with sqlite3.connect(database) as db:
        return {
            'revision': db.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone()[0],
            'executions': db.execute('SELECT task_id,generation,contract_revision,contract_json,state,'
                                     'current_stage,iteration,workspace_id,admission_id '
                                     'FROM executions ORDER BY task_id').fetchall(),
            'records': db.execute('SELECT kind,record_id,task_id,generation,record_revision,data_json '
                                  'FROM execution_records ORDER BY seq').fetchall(),
            'requests': db.execute('SELECT request_id,digest,response_json FROM requests '
                                   'ORDER BY request_id').fetchall(),
        }


def current_revision(database):
    with sqlite3.connect(database) as db:
        return db.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone()[0]


def assert_receipt(receipt, method, attempt, run, source, *, mode='good', guard=True):
    """Expected meaning comes from literal command cases, not producer flags."""
    case = DATA['cases'][mode]
    assert set(receipt) == set(DATA['receipt_keys']), receipt
    assert isinstance(receipt['execution_key'], str) and receipt['execution_key']
    directory = Path(receipt['output_directory'])
    assert directory.is_absolute()
    expected_outputs = bind(DATA['captured_outputs'], {
        '@REPORT': str(directory / 'report.txt'), '@BINARY': str(directory / 'payload.bin')})
    outputs = {item['id']: item for item in expected_outputs}
    expected_outputs = [dict(outputs[item['id']], required=item['required'])
                        for item in method['outputs']]
    if case['output_status'] == 'missing':
        expected_outputs = [dict(value, status='missing', path=None, size=None)
                            for value in expected_outputs]
    expected = {
        'id': run['run_id'], 'method': method['id'], 'obligations': [method['id']],
        'argv': method['argv'], 'cwd': method['cwd'], 'environment': method['environment'],
        'actual_exit_code': case['exit'], 'stdout': case['stdout'], 'stderr': case['stderr'],
        'stdout_bytes_hex': case['stdout_hex'], 'stderr_bytes_hex': case['stderr_hex'],
        'capture_complete': case['capture_complete'], 'interpretable': case['interpretable'],
        'timed_out': False, 'guard': guard, 'passed': case['passed'],
        'commit': source['commit'], 'tree': source['tree'],
        'source_before': source, 'source_after': source, 'source_unchanged': True,
        'attempt_id': attempt['attempt_id'], 'run_id': run['run_id'],
        'execution_key': receipt['execution_key'], 'resolved_cwd': attempt['_cwd'],
        'output_directory': str(directory), 'outputs': expected_outputs,
        'termination': DATA['termination'],
    }
    assert receipt == expected, (receipt, expected)
    for item in expected_outputs:
        if item['status'] == 'captured':
            assert Path(item['path']).read_bytes() == bytes.fromhex(DATA['output_bytes'][item['id']])


def assert_attempt(value, *, subject, identifier, frame, schedule, used, maximum,
                   state='reserved', purpose='stage', owner=None):
    assert set(value) == set(DATA['attempt_keys']), value
    expected = {
        'attempt_id': identifier, 'task_id': subject.task_id, 'generation': subject.generation,
        'contract_revision': subject.contract_revision, 'stage_id': frame['stage_id'],
        'iteration': frame['iteration'], 'context_id': frame['context_id'],
        'candidate_id': frame['candidate_id'], 'source': subject.source(),
        'method_ids': schedule, 'purpose': purpose,
        'owner_id': frame['candidate_id'] if owner is None else owner,
        'budget': {'used': used, 'maximum': maximum}, 'state': state,
        'runs': value['runs'], 'evidence_id': value['evidence_id'],
    }
    assert value == expected, (value, expected)
    assert len(value['runs']) == len(schedule)
    assert [r['method_id'] for r in value['runs']] == schedule
    ids = [r['run_id'] for r in value['runs']]
    assert len(set(ids)) == len(ids) and all(isinstance(i, str) and i for i in ids)
    for run in value['runs']:
        assert set(run) == {'run_id', 'method_id', 'started', 'receipt', 'termination'}
        assert type(run['started']) is bool
        if state == 'reserved':
            assert run['started'] is False and run['receipt'] is None and run['termination'] is None
    if state == 'finished':
        assert isinstance(value['evidence_id'], str) and value['evidence_id']
        assert all(run['started'] and isinstance(run['receipt'], dict) for run in value['runs'])
    elif state == 'reserved':
        assert value['evidence_id'] is None


class VerificationSubject(OutcomeSubject):
    """Arrange old public process/proof prerequisites, never future product code."""
    def __init__(self, root, *, extra=False, variant='ordinary', maximum=8,
                 output_policy='required', empty_review=False):
        self.task_id = '0042'
        super().__init__(root / 'prerequisite', variant=variant)
        self.fixture_root = root
        self.counter_file, self.control = root / 'commands.txt', root / 'control'
        self.control.mkdir()
        self.capture_root = root / 'captures'
        self.maximum = maximum
        self.raw_logs = LOG
        (self.git_root / 'outputs/verify_subject.py').write_text('\n'.join(DATA['program_lines']) + '\n')
        (self.git_root / 'outputs/verification-mode.txt').write_text('good\n')
        self.git('add', 'outputs/verify_subject.py', 'outputs/verification-mode.txt')
        self.git('commit', '--only', 'outputs/verify_subject.py', 'outputs/verification-mode.txt',
                 '-m', 'Prepare independent verification command')
        self.baseline = self.source()['tree']
        old_config = json.loads(self.config.read_text())
        self.config = installation(root / 'verification')
        cfg = json.loads(self.config.read_text())
        for key in ('lifecycle', 'gates', 'outcomes'):
            if key in old_config:
                cfg[key] = deepcopy(old_config[key])
        self.file_roots = {scope: root / 'files' / scope for scope in ('runtime', 'task', 'sprint')}
        for path in self.file_roots.values():
            path.mkdir(parents=True)
        cfg['gates'] = {'roots': {key: str(path) for key, path in self.file_roots.items()}}
        write_json(self.config, cfg)
        method = bind(DATA['method'], {'@PY': sys.executable,
            '@COUNTER': str(self.counter_file), '@CONTROL': str(self.control)})
        self.contract = deepcopy(self.contract)
        if output_policy == 'optional':
            for output in method['outputs']:
                output['required'] = False
        elif output_policy == 'empty':
            method['outputs'] = []
        else:
            assert output_policy == 'required'
        self.contract['methods'] = [method]
        if empty_review:
            review = next(row for row in self.contract['stage_expectations']
                          if row['stage_id'] == 'review')
            review['methods'] = []
        if extra:
            second = deepcopy(method)
            second['id'] = 'guard-extra'
            second['environment']['VERIFY_METHOD'] = 'guard-extra'
            second['verification_plan']['green_stages'] = ['write']
            self.contract['methods'].append(second)
            self.contract['stage_expectations'][0]['methods'].append('guard-extra')
        self.contract['workspace_id'] = self.workspace.task()['workspace']['workspace_id']
        code, process = raw(self.config, create(definition=self.definition))
        assert code == 0 and process['status'] == 'published', process
        self.contract['process_ref']['revision'] = process['process_revision']
        code, setup = raw(self.config, {'schema': 'stride-execution/v1', 'operation': 'apply',
            'input': {'request_id': 'verification-setup', 'expected_revision': process['revision'],
                'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': self.contract}]}})
        assert code == 0 and setup['status'] == 'applied', setup
        self.revision, self.generation = setup['revision'], 1
        self.contract_revision = setup['results'][0]['contract_revision']
        self.database = self.config.parent / 'state/execution.sqlite'
        module = subject()
        self.store = module.Store(module.load_config(self.config))
        self.origin, self.origins_by_visit = {}, {}
        self.observed_frames, self.presentations, self.current_proofs = {}, {}, {}
        self.first_evidence = None
        self.prepared()
        self.frame = self.gate_ref('write', tag='verification')

    def gate_ref(self, stage, *, tag='shared', iteration=None, files=None, accepted=True):
        if not hasattr(self, 'artifact_specs'):
            return super().gate_ref(stage, tag=tag, iteration=iteration, files=files)
        state = self.sync()
        iteration = state['iteration'] if iteration is None else iteration
        context, candidate = self.identity('artifact-context'), self.identity('artifact-candidate')
        specifications = deepcopy(self.artifact_specs)
        declaration = {'action': 'declare', 'request_id': self.identity('declare'),
            'expected_revision': self.revision, 'task_id': self.task_id, 'generation': self.generation,
            'context_id': context, 'contract_revision': self.contract_revision,
            'requirement_ids': ['report'],
            'task_content': {'sections': [], 'routes': [], 'requirements': specifications},
            'stage_contracts': [{'stage_id': row['id'], 'allowed_paths': row['allowed_paths'],
                'entry_requirements': [],
                'exit_requirements': ['filled'] + [item['id'] for item in specifications],
                'criteria': {'requirements': [], 'definition_of_ready': [], 'definition_of_done': []}}
                for row in self.definition['stages']]}
        code, declared = raw(self.config, {'schema': 'stride-execution/v1', 'operation': 'gate',
                                          'input': declaration})
        assert code == 0, declared
        evaluation = {'action': 'evaluate', 'request_id': self.identity('evaluate'),
            'expected_revision': declared['revision'], 'task_id': self.task_id,
            'generation': self.generation, 'context_id': context, 'candidate_id': candidate,
            'stage_id': stage, 'iteration': iteration,
            'sections': {'report': 'Фактический результат этапа сохранён.'}, 'trace': {},
            'files': self.selected_files if files is None else files}
        code, evaluated = raw(self.config, {'schema': 'stride-execution/v1', 'operation': 'gate',
                                          'input': evaluation})
        assert code == (0 if accepted else 2) and evaluated['accepted'] is accepted, evaluated
        self.revision = evaluated['revision']
        if not accepted:
            return evaluated
        return {'stage_id': stage, 'context_id': context, 'candidate_id': candidate,
                'iteration': iteration, 'gate_id': evaluated['result_id']}

    def common(self, *, actor='executor-A', request=None):
        self.sync()
        return {'request_id': request or self.identity('verification'),
                'expected_revision': self.revision, 'task_id': self.task_id,
                'expected_generation': self.generation,
                'expected_contract_revision': self.contract_revision, 'actor_id': actor}

    def packet(self, action, **extra):
        return {'schema': 'stride-execution/v1', 'operation': 'lifecycle',
                'input': {**self.common(), 'action': action, **extra}}

    def run(self, packet):
        code, response = raw(self.config, packet)
        assert code == 0 and response['status'] == 'lifecycle_applied', response
        assert response['task_id'] == self.task_id
        assert response['revision'] == packet['input']['expected_revision'] + 1
        self.revision, self.generation = response['revision'], response['generation']
        self.contract_revision = response['contract_revision']
        return response

    def query(self, kind):
        return self.read(kind)['results'][0]

    def physical(self):
        if not hasattr(self, 'target_workspace'):
            return super().physical()
        index = Path(self.git('rev-parse', '--git-path', 'index').strip())
        if not index.is_absolute():
            index = self.git_root / index
        names = set(self.git('ls-files', '--cached', '--others', '--exclude-standard').splitlines())
        workspace = self.workspace.invoke({'schema': 'stride-sequencing/v1', 'operation': 'query',
            'input': {'queries': [{'id': 'target', 'kind': 'task', 'task_id': self.task_id}]}})
        return {'head': self.source(), 'index': index.read_bytes(),
            'files': {name: (self.git_root / name).read_bytes() for name in sorted(names)
                      if (self.git_root / name).is_file()},
            'workspace': workspace['results'][0]['value']['workspace']}

    def records(self):
        return self.store.read_records(self.task_id)

    def configure(self, **changes):
        cfg = json.loads(self.config.read_text())
        settings = {'roles': ['writer', 'reviewer'], 'max_attempts': self.maximum,
            'repository': str(self.git_root), 'base_ref': 'refs/heads/sprint',
            'output_root': str(self.capture_root)}
        settings.update(changes)
        cfg['verification'] = settings
        write_json(self.config, cfg)
        return settings

    def count(self, method=None):
        lines = self.counter_file.read_text().splitlines() if self.counter_file.exists() else []
        return sum(line.split('|')[0] == str(self.git_root)
                   and (method is None or line.split('|')[1] == method) for line in lines)

    def mode(self, value):
        (self.git_root / 'outputs/verification-mode.txt').write_text(value + '\n')
        self.git('add', 'outputs/verification-mode.txt')
        self.git('commit', '--only', 'outputs/verification-mode.txt', '-m', 'Select verification subject case')
        self.frame = self.gate_ref(self.sync()['current_stage'], tag=self.identity('mode'))

    def begin_packet(self, identifier=None, frame=None, actor='executor-A'):
        frame = self.frame if frame is None else frame
        schedule = next(s['methods'] for s in self.contract['stage_expectations']
                        if s['stage_id'] == frame['stage_id'])
        return {'schema': 'stride-execution/v1', 'operation': 'attempt', 'input': {
            **self.common(actor=actor), 'action': 'begin',
            'attempt_id': identifier or self.identity('attempt'),
            'context_id': frame['context_id'], 'candidate_id': frame['candidate_id'],
            'source': self.source(), 'method_ids': schedule}}

    def probe(self):
        packet = self.begin_packet()
        before = rows(self.database)
        code, response = raw(self.config, packet)
        if code == 2 and response == {'schema': 'stride-execution/v1', 'status': 'rejected',
                                    'reason': DATA['missing_operation']}:
            assert rows(self.database) == before
            raise MissingVerification()
        assert code == 2 and response['status'] == 'rejected'
        assert 'verification' in response['reason'].lower(), response
        assert rows(self.database) == before
        self.configure()
        return self

    def call(self, operation, action, *, expected=0, **extra):
        packet = {'schema': 'stride-execution/v1', 'operation': operation,
                  'input': {**self.common(), 'action': action, **extra}}
        code, response = raw(self.config, packet)
        assert code == expected, response
        return packet, response

    def begin(self, identifier=None, *, used=1, frame=None, actor='executor-A'):
        frame = self.frame if frame is None else frame
        packet = self.begin_packet(identifier, frame, actor)
        code, response = raw(self.config, packet)
        assert code == 0 and response['status'] == 'attempt_reserved', response
        assert set(response) == {'schema', 'status', 'revision', 'replayed', 'changed', 'attempt'}
        assert_attempt(response['attempt'], subject=self, identifier=packet['input']['attempt_id'],
            frame=frame, schedule=packet['input']['method_ids'], used=used, maximum=self.maximum)
        assert response['revision'] == current_revision(self.database)
        return packet, response['attempt']

    def step(self, action, attempt, *, expected=0, **fields):
        return self.call('attempt', action, expected=expected,
                         attempt_id=attempt['attempt_id'], **fields)

    def run_all(self, attempt, *, mode='good', frame=None, guard=True, actor='executor-A'):
        frame = self.frame if frame is None else frame
        for run in attempt['runs']:
            _, response = self.step('run', attempt, run_id=run['run_id'], actor_id=actor)
            assert response['status'] == 'run_recorded'
            selected = next(m for m in self.contract['methods'] if m['id'] == run['method_id'])
            observed = next(r for r in response['attempt']['runs'] if r['run_id'] == run['run_id'])
            assert observed['started'] and observed['termination'] == DATA['termination']
            assert_receipt(observed['receipt'], selected,
                dict(attempt, _cwd=str(self.git_root)), run, self.source(), mode=mode, guard=guard)
            assert response['revision'] == current_revision(self.database)
            attempt = response['attempt']
        return attempt

    def work_for(self, frame, receipts, *, verdict='proved'):
        stage = frame['stage_id']
        work = {'phase': 'continue' if receipts else 'prepare', 'arguments': [], 'decisions': []}
        if stage in ('write', 'finish') and self.contract['evidence_plan'][stage]['arguments']:
            identifier = 'witness' if stage == 'write' else 'terminal'
            work['arguments'] = [{'id': identifier, 'kind': 'logical',
                'facts': ['Фактический результат объявленной команды сохранён.'],
                'assumptions': [], 'inference': 'Наблюдение сопоставлено с ожидаемым результатом.',
                'conclusion': 'Зафиксировано заключение о результате проверки.',
                'verdict': verdict,
                'observation_ids': [v['id'] for v in receipts if v['method'] == 'guard']}]
        if stage == 'review':
            work['decisions'] = [{'argument_id': 'witness', 'revision': self.latest_argument(),
                'decision': 'accepted', 'reason': 'Проверено сохранённое заключение автора.'}]
        return work

    def finish(self, attempt, *, frame=None, verdict='proved', actor='executor-A'):
        frame = self.frame if frame is None else frame
        work = self.work_for(frame, [r['receipt'] for r in attempt['runs']], verdict=verdict)
        packet = {'schema': 'stride-execution/v1', 'operation': 'attempt', 'input': {
            **self.common(actor=actor), 'action': 'finish', 'attempt_id': attempt['attempt_id'],
            'evidence_work': work}}
        code, response = raw(self.config, packet)
        assert code == 0 and response['status'] == 'attempt_finished', response
        result = response['attempt']
        assert result['state'] == 'finished' and result['runs'] == attempt['runs']
        assert isinstance(result['evidence_id'], str) and result['evidence_id']
        actual = next(v for v in self.records() if v['kind'] == 'evidence'
                      and v['id'] == result['evidence_id'])
        assert actual['task_id'] == self.task_id and actual['generation'] == self.generation
        assert actual['data']['attempt_id'] == attempt['attempt_id']
        assert actual['data']['evidence_work'] == work
        return packet, result

    def lookup(self, kind, **ids):
        packet = {'schema': 'stride-execution/v1', 'operation': 'query', 'input': {
            'queries': [{'id': 'read', 'kind': kind, 'task_id': self.task_id, **ids}]}}
        before = rows(self.database)
        code, response = raw(self.config, packet)
        assert code == 0 and set(response) == {'schema', 'status', 'revision', 'results'}, response
        assert response['schema'] == 'stride-execution/v1' and response['status'] == 'read_only'
        assert response['revision'] == before['revision']
        assert len(response['results']) == 1
        result = response['results'][0]
        assert set(result) == {'id', 'kind', 'result'} and result['id'] == 'read' and result['kind'] == kind
        assert rows(self.database) == before
        return result['result']

    def deny(self, packet, *words):
        before, count = rows(self.database), self.count()
        code, response = raw(self.config, packet)
        assert code == 2 and set(response) == {'schema', 'status', 'reason'}, response
        assert response['schema'] == 'stride-execution/v1' and response['status'] == 'rejected'
        assert isinstance(response['reason'], str) and response['reason'].strip()
        assert any(word.lower() in response['reason'].lower() for word in words), response
        assert rows(self.database) == before and self.count() == count
        return response

    def observe(self, frame, *, replay=False, **options):
        """Existing public external-observation arrangement with real processes."""
        self.sync()
        source = self.source()
        stage = frame['stage_id']
        selected = next(v['methods'] for v in self.contract['stage_expectations']
                        if v['stage_id'] == stage)
        methods = [m for m in self.contract['methods'] if m['id'] in selected]
        attempt_id = self.identity('external-attempt')
        receipts = []
        for method in methods:
            run_id = self.identity('external-run')
            directory = self.fixture_root / 'external-capture' / run_id
            directory.mkdir(parents=True)
            cwd = Path(method['cwd'])
            cwd = cwd if cwd.is_absolute() else self.git_root / cwd
            process = subprocess.Popen(method['argv'], cwd=cwd, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True,
                env=dict(os.environ, **method['environment'], POISE_RUN_OUTPUT_DIR=str(directory)))
            out, err = process.communicate()
            try:
                os.killpg(process.pid, 0)
                stopped = False
            except ProcessLookupError:
                stopped = True
            try:
                text_out, text_err = out.decode('utf-8'), err.decode('utf-8')
                interpretable = True
            except UnicodeDecodeError:
                text_out, text_err = out.decode('utf-8', errors='replace'), err.decode('utf-8', errors='replace')
                interpretable = False
            outputs = []
            for declaration in method['outputs']:
                path = directory / declaration['path']
                exists = path.is_file()
                outputs.append({'id': declaration['id'], 'declared_path': declaration['path'],
                    'required': declaration['required'], 'status': 'captured' if exists else 'missing',
                    'path': str(path) if exists else None, 'size': path.stat().st_size if exists else None})
            complete = all(not v['required'] or v['status'] == 'captured' for v in outputs)
            passed = (complete and interpretable and process.returncode == method['expected_exit_code']
                and all(v in text_out for v in method['stdout_contains'])
                and all(v in text_err for v in method['stderr_contains']) and source == self.source())
            receipt = {'id': run_id, 'method': method['id'], 'obligations': [method['id']],
                'argv': method['argv'], 'cwd': method['cwd'], 'environment': method['environment'],
                'actual_exit_code': process.returncode, 'stdout': text_out, 'stderr': text_err,
                'stdout_bytes_hex': out.hex(), 'stderr_bytes_hex': err.hex(),
                'capture_complete': complete, 'interpretable': interpretable, 'timed_out': False,
                'guard': method['id'] not in self.contract['evidence_plan'][stage]['subject_methods'],
                'passed': passed, 'commit': source['commit'], 'tree': source['tree'],
                'source_before': source, 'source_after': self.source(), 'source_unchanged': source == self.source(),
                'attempt_id': attempt_id, 'run_id': run_id, 'execution_key': attempt_id,
                'resolved_cwd': str(cwd), 'output_directory': str(directory), 'outputs': outputs,
                'termination': {'schema': 'run-termination-1', 'child_reaped': True,
                                'process_group_stopped': stopped, 'capture_complete': True}}
            assert stopped and process.returncode in (0, 7), receipt
            receipts.append(receipt)
            LOG.append({'prerequisite_actual_external_command': receipt})
        evidence_id = self.identity('external-evidence')
        records = [
            {'kind': 'attempt', 'id': attempt_id, 'task_id': self.task_id,
             'generation': self.generation, 'revision': 1, 'data': {
                'status': 'completed', 'stage_id': stage, 'iteration': frame['iteration'],
                'context_id': frame['context_id'], 'candidate_id': frame['candidate_id'],
                'source': source, 'methods': methods, 'execution_key': attempt_id, 'receipts': receipts}},
            {'kind': 'evidence', 'id': evidence_id, 'task_id': self.task_id,
             'generation': self.generation, 'revision': 1, 'data': {
                'attempt_id': attempt_id, 'evidence_work': self.work_for(frame, receipts)}},
        ]
        result = self.store.write_records(request_id=self.identity('external-facts'),
                                          expected_revision=self.revision, records=records)
        self.revision = result['revision']
        observed = dict(frame, evidence_id=evidence_id)
        self.observed_frames[(self.task_id, self.generation, stage, frame['context_id'],
                              frame['candidate_id'], evidence_id)] = deepcopy(observed)
        LOG.append({'prerequisite_public_records': records, 'result': result})
        return observed

    def expected(self, packet, *, kind='positive'):
        value = super().expected(packet, kind=kind)
        value['task_id'] = self.task_id
        value['requirements_snapshot_revision'] = self.initial_snapshot['snapshot_revision']
        return value

    def finish_route(self):
        """Current existing public ordinary route, not future reuse implementation."""
        while True:
            state = self.sync()
            stage = state['current_stage']
            frame = self.gate_ref(stage, tag=self.identity('ordinary'))
            actor = 'reviewer-B' if stage == 'review' else 'executor-A'
            self.act('present', actor_id=actor,
                     context_id=frame['context_id'], candidate_id=frame['candidate_id'])
            frame = self.observe(frame)
            handler = next(v['handler'] for v in self.definition['stages'] if v['id'] == stage)
            outcome = {'produce': 'complete', 'inspect': 'clear', 'check': 'satisfied'}[handler]
            result = self.next(frame, outcome=outcome)
            if result['status'] == 'route_end':
                self.frame = frame
                return self

    def restart_intent(self):
        proposal = self.identity('restart-proposal')
        self.act('propose_restart', actor_id='executor-C', proposal_id=proposal,
                 contract=self.contract, requirements_snapshot=self.initial_snapshot,
                 reason='Согласовать новую работу с сохранением истории.')
        response = self.act('decide_restart', actor_id='reviewer-D', proposal_id=proposal,
                            decision='approved', reason='Проверены условия новой работы.')
        return proposal, response['result']['approval_id']

    def restart_current(self):
        proposal, approval = self.restart_intent()
        result = self.act('restart', proposal_id=proposal, approval_id=approval)
        self.freeze()
        self.enter()
        self.frame = self.gate_ref('write', tag=self.identity('restarted'))
        return result

    def rework_current(self):
        state = self.sync()
        actor = 'reviewer-B' if state['current_stage'] == 'review' else 'executor-A'
        if self.lifecycle()['execution']['state'] == 'active':
            self.act('present', actor_id=actor, context_id=self.frame['context_id'],
                     candidate_id=self.frame['candidate_id'])
        result = self.act('rework', actor_id=actor, target_stage_id=state['current_stage'],
                          entry_tree=self.source()['tree'], reason='Повторить текущую работу.')
        self.baseline = self.lifecycle()['entry_tree']
        self.frame = self.gate_ref(state['current_stage'], tag=self.identity('reworked'))
        return result

    def fault(self, name, table, when):
        """One real SQLite boundary fault; caller removes it before continuation."""
        assert table in ('execution_records', 'requests')
        with sqlite3.connect(self.database) as db:
            db.execute(f'CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN {when} '
                       "BEGIN SELECT RAISE(ABORT, 'T056 injected record boundary'); END")

    def clear_fault(self, name):
        assert name.startswith('T056_')
        with sqlite3.connect(self.database) as db:
            db.execute(f'DROP TRIGGER {name}')

    def live_run(self, attempt, run):
        return LiveRun(self, attempt, run)


class LiveRun:
    """A real owned command waits for this fixture's explicit release."""
    def __init__(self, subject, attempt, run):
        self.subject = subject
        (subject.control / 'release').unlink(missing_ok=True)
        (subject.control / 'ready').unlink(missing_ok=True)
        (subject.control / 'hold').write_text('hold\n')
        self.packet = {'schema': 'stride-execution/v1', 'operation': 'attempt', 'input': {
            **subject.common(), 'action': 'run', 'attempt_id': attempt['attempt_id'],
            'run_id': run['run_id']}}
        self.process = subprocess.Popen(
            [sys.executable, '-B', str(ROOT / 'apps/execution/bin/execution'),
             '--config', str(subject.config)], cwd=subject.config.parent,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.process.stdin.write(json.dumps(self.packet) + '\n')
        self.process.stdin.close()
        self.process.stdin = None

    def __enter__(self):
        while not (self.subject.control / 'ready').exists():
            if self.process.poll() is not None:
                (self.subject.control / 'release').write_text('release\n')
                out, err = self.process.communicate()
                (self.subject.control / 'hold').unlink(missing_ok=True)
                raise AssertionError(('Run ended before actual command readiness', out, err))
            time.sleep(0.01)
        assert self.process.poll() is None
        return self

    def __exit__(self, kind, value, trace):
        (self.subject.control / 'release').write_text('release\n')
        try:
            out, err = self.process.communicate()
            LOG.append({'actual_live_packet': self.packet, 'exit': self.process.returncode,
                        'stdout': out, 'stderr': err})
            assert err == ''
            self.response = json.loads(out)
        finally:
            (self.subject.control / 'hold').unlink(missing_ok=True)
        return False


class ReuseFamily:
    """Two independent publications, real related Tasks and physical worktrees."""
    def __init__(self, root, *, probe=True, maximum=8, target_change=None,
                 source_kind='positive', result_commit=True, related=True,
                 target_contract_change=None, deliver_source=True, artifact_case=False,
                 output_policy='required', target_process_change=None, same_parent_change=None,
                 variant='ordinary'):
        self.root = root
        self.source = VerificationSubject(root / 'source', maximum=maximum,
            output_policy=output_policy, variant=variant)
        if probe:
            self.source.probe()
        s = self.source
        if artifact_case:
            s.artifact_specs = deepcopy(DATA['artifact_rules'])
            s.selected_files = []
            for scope, suffix in [('task', 'txt'), ('sprint', 'bin')]:
                path = s.file_roots[scope] / s.task_id / ('result.' + suffix)
                path.parent.mkdir(parents=True)
                path.write_bytes(bytes.fromhex(DATA['artifact_bytes'][scope]))
                s.selected_files.append(str(path))
        s.finish_route()
        packet = s.outcome_packet(intent='cancel' if source_kind == 'cancelled' else 'accept',
                                  result=result_commit)
        self.source_fact = s.record(packet, kind=source_kind)['fact']
        # Explicit callers deliver the accepted source, never Execution foreign DB writes.
        from outcome_checks import requirements_packet, Receiver, sequencing_packet
        s.requirements.invoke(requirements_packet(s, self.source_fact))
        work = s.workspace
        work.apply([{'id': 'release-source', 'kind': 'release_claim', 'task_id': s.task_id,
            'claim_id': work.claim_id, 'claimant': work.owner, 'reason': 'Передать принятый результат.'}])
        self.receiver = Receiver.__new__(Receiver)
        self.receiver.client, self.receiver.config = work.client, work.client.config
        self.receiver.database, self.receiver.serial = work.database, 0
        if deliver_source:
            self.receiver.client.invoke(sequencing_packet(self.receiver, self.source_fact))
        work.git(work.repo, 'merge', '--ff-only', s.source()['commit'])
        # Workspace's independent fixture has registered 0043/0044. Allocate past
        # those two addresses through Requirements, never patch its allocator.
        for identifier in ('0043', '0044', '0045'):
            result = s.requirements.invoke({'operation': 'create_task', 'input': {
                'request_id': 'family-create-' + identifier,
                'goal': s.initial_snapshot['statement']['goal'],
                'work_choice': {'kind': 'standalone'}}})
            assert result['status'] == 'created' and result['task_id'] == identifier, result
        if target_change is not None and getattr(target_change, 'parent_case', False):
            s.requirements.invoke({'operation': 'apply', 'input': {
                'request_id': 'family-add-alternative-parent',
                'expected_revision': revision(s.requirements.database, 'requirements'),
                'operations': [{'kind': 'put_requirement', 'requirement': {
                    'id': 'APP-OTHER', 'level': 'application', 'status': 'future',
                    'text': 'Другой результат приложения.'}},
                    {'kind': 'link', 'system': 'SYS-REPORT', 'application': 'APP-OTHER'}]}})
        if same_parent_change is not None:
            parent = deepcopy(s.initial_snapshot['requirements']['APP-REPORT'])
            assert parent == DATA['same_parent_baseline']
            parent[same_parent_change['field']] = same_parent_change['value']
            s.requirements.invoke({'operation': 'apply', 'input': {
                'request_id': 'family-revise-same-parent',
                'expected_revision': revision(s.requirements.database, 'requirements'),
                'operations': [{'kind': 'put_requirement', 'requirement': parent}]}})
        statement = deepcopy(OUTCOME['statement'])
        statement['goal'] = s.initial_snapshot['statement']['goal']
        if target_change is not None:
            target_change(statement)
        s.requirements.invoke({'operation': 'apply', 'input': {
            'request_id': 'family-revise-target',
            'expected_revision': revision(s.requirements.database, 'requirements'),
            'operations': [{'kind': 'revise_task', 'task_id': '0045',
                'expected_statement_revision': 1, 'statement': statement}]}})
        s.requirements.invoke({'operation': 'apply', 'input': {
            'request_id': 'family-publish-target',
            'expected_revision': revision(s.requirements.database, 'requirements'),
            'operations': [{'kind': 'publish_task', 'task_id': '0045', 'statement_revision': 2}]}})
        snapshot = s.requirements.invoke({'operation': 'query', 'input': {'queries': [{
            'id': 'target', 'kind': 'snapshot', 'task_id': '0045', 'snapshot_revision': 1}]}})
        self.target_snapshot = snapshot['results'][0]['value']
        assert self.target_snapshot['task_id'] == '0045'
        if related:
            work.apply([{'id': 'sprint', 'kind': 'register_sprint', 'sprint_id': 'SPR-REUSE',
                'members': ['0043'], 'dependencies': []}])
            work.apply([{'id': 'duplicate', 'kind': 'register_duplicate', 'task_id': '0045',
                'parent_id': '0042', 'sprint_id': 'SPR-REUSE', 'claimant': 'SESSION-TARGET',
                'requirements_snapshot_revision': 1}])
        else:
            work.apply([{'id': 'unrelated', 'kind': 'register_task', 'task_id': '0045',
                         'requirements_snapshot_revision': 1}])
        self.claimant = 'SESSION-TARGET'
        self.claim_id = work.claim('0045', self.claimant)
        packet = work.packet()
        packet['input'].update(task_id='0045', claim_id=self.claim_id, claimant=self.claimant)
        self.workspace = work.invoke(packet)['workspace']
        target = VerificationSubject.__new__(VerificationSubject)
        target.__dict__ = dict(s.__dict__)
        target.task_id, target.counter = '0045', 10000
        target.target_workspace = deepcopy(self.workspace)
        target.fixture_root = root / 'target'
        target.fixture_root.mkdir()
        target.git_root = Path(self.workspace['path'])
        target.initial_snapshot = deepcopy(self.target_snapshot)
        target.snapshots = [target.initial_snapshot]
        target.version_file = target.git_root / 'source-version.txt'
        target.payload = target.git_root / 'outputs/subject.bin'
        target.baseline = target.source()['tree']
        target.capture_root = root / 'target-capture'
        target.config = installation(root / 'target-config')
        cfg = json.loads(s.config.read_text())
        cfg['paths']['state'] = str(s.database.parent)
        cfg['verification'] = {'roles': ['writer', 'reviewer'], 'max_attempts': maximum,
            'repository': str(target.git_root), 'base_ref': 'refs/heads/sprint',
            'output_root': str(target.capture_root)}
        if not probe:
            del cfg['verification']
        write_json(target.config, cfg)
        target.contract = deepcopy(s.contract)
        target.contract.update(task_id='0045', requirements_snapshot_revision=1,
                               workspace_id=self.workspace['workspace_id'], admission_id='admit-0045')
        if target_contract_change is not None:
            target_contract_change(target.contract)
            if target.contract['restart_policy_id'] not in cfg['lifecycle']['policies']:
                cfg['lifecycle']['policies'][target.contract['restart_policy_id']] = deepcopy(
                    cfg['lifecycle']['policies'][s.contract['restart_policy_id']])
                write_json(target.config, cfg)
        if target_process_change is not None:
            code, head = raw(target.config, {'schema': 'stride-execution/v1',
                'operation': 'process', 'input': {'action': 'status', 'goal_type': 'writing'}})
            assert code == 0, head
            code, published = raw(target.config, {'schema': 'stride-execution/v1',
                'operation': 'process', 'input': {'action': 'edit',
                    'request_id': 'family-change-target-process', 'expected_revision': head['revision'],
                    'goal_type': 'writing', 'expected_process_revision': head['live_revision'],
                    'changes': [{'op': 'patch_stage', 'id': 'write', 'set': target_process_change}]}})
            assert code == 0 and published['status'] == 'published', published
            target.definition = deepcopy(s.definition)
            target.definition['stages'][0].update(target_process_change)
            target.contract['process_ref'] = {'goal_type': 'writing',
                                             'revision': published['process_revision']}
        code, setup = raw(target.config, {'schema': 'stride-execution/v1', 'operation': 'apply',
            'input': {'request_id': 'family-setup-target',
                'expected_revision': current_revision(s.database), 'operations': [
                    {'id': 'setup', 'kind': 'setup_execution', 'contract': target.contract}]}})
        assert code == 0 and setup['status'] == 'applied', setup
        target.revision, target.generation = setup['revision'], 1
        target.contract_revision = setup['results'][0]['contract_revision']
        module = subject()
        target.store = module.Store(module.load_config(target.config))
        target.origin, target.origins_by_visit = {}, {}
        target.observed_frames, target.presentations, target.current_proofs = {}, {}, {}
        target.first_evidence = None
        if artifact_case:
            target.selected_files = []
            for scope, suffix in [('task', 'txt'), ('sprint', 'bin')]:
                source_path = s.file_roots[scope] / s.task_id / ('result.' + suffix)
                target_path = s.file_roots[scope] / target.task_id / ('result.' + suffix)
                target_path.parent.mkdir(parents=True)
                shutil.copyfile(source_path, target_path)
                target.selected_files.append(str(target_path))
        target.prepared()
        target.frame = target.gate_ref('write', tag='reuse-target')
        self.target = target
        self.source_records = deepcopy(s.records())
        self.source_head = s.source()
        self.source_publication = deepcopy(s.initial_snapshot)

    def admission(self):
        packet = {'schema': 'stride-sequencing/v1', 'operation': 'query', 'input': {
            'queries': [{'id': 'family', 'kind': 'duplicate_family', 'task_id': '0045'},
                {'id': 'eligibility', 'kind': 'eligibility', 'task_id': '0045',
                 'claimant': self.claimant}]}}
        response = self.source.workspace.invoke(packet)
        assert response['status'] == 'read_only' and len(response['results']) == 2
        return {'claimant': self.claimant, 'family': response['results'][0]['value'],
                'eligibility': response['results'][1]['value']}

    def prepare_packet(self, identifier=None, mapping=None):
        s = self.target
        return {'schema': 'stride-execution/v1', 'operation': 'reuse', 'input': {
            **s.common(), 'action': 'prepare', 'reuse_id': identifier or s.identity('reuse'),
            'source_task_id': '0042', 'source_outcome_id': self.source_fact['outcome_id'],
            'source': s.source(), 'context_id': s.frame['context_id'],
            'candidate_id': s.frame['candidate_id'], 'admission': self.admission(),
            'artifact_mapping': [] if mapping is None else mapping}}

    def unchanged_source(self):
        assert self.source.records() == self.source_records
        assert self.source.source() == self.source_head
        assert self.source.initial_snapshot == self.source_publication
