"""Real public prerequisites and independent terminal-fact expectations."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from advance_checks import AdvanceSubject, LOG as ADVANCE_LOG
from lifecycle_checks import Scenario, bind, Client, current_state
from process_checks import create, installation, invoke, write_json
from store_checks import subject

ROOT = Path(__file__).resolve().parents[3]
DATA = json.loads((ROOT / 'apps/execution/fixtures/outcome-facts-contract.json').read_text())
LOG = []


class MissingOutcome(Exception):
    """Only the exact old public missing-operation response establishes RED."""


def mark(name):
    LOG.append({'subcheck': name})


def raw(config, packet, application='execution'):
    actual = subprocess.run([sys.executable, '-B', str(ROOT / 'apps' / application / 'bin' / application),
        '--config', str(config)], input=json.dumps(packet, ensure_ascii=False),
        cwd=config.parent, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        text=True, capture_output=True, timeout=30)
    LOG.append({'application': application, 'config': str(config), 'packet': deepcopy(packet),
        'exit_code': actual.returncode, 'stdout': actual.stdout, 'stderr': actual.stderr})
    assert actual.stderr == '', actual.stderr
    response = json.loads(actual.stdout)
    if (application == 'execution' and packet['operation'] == 'outcome'
            and actual.returncode == 2 and response == {
                'schema': 'stride-execution/v1', 'status': 'rejected',
                'reason': DATA['unknown_operation']}):
        raise MissingOutcome()
    return actual.returncode, response


def revision(database, application):
    with sqlite3.connect(database) as db:
        row = (db.execute('SELECT revision FROM requirements_meta').fetchone()
               if application == 'requirements' else
               db.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone())
    return row[0]


class OutcomeSubject(AdvanceSubject):
    def __init__(self, root, *, variant='ordinary', future=False, capability=True):
        # Reuse genuine public prerequisite owners, then arrange an independent
        # standalone Execution. The prerequisite Workspace is not its contract.
        Scenario.__init__(self, root / 'prerequisites')
        self.task_id, self.counter = '0042', 0
        statement = deepcopy(DATA['future_statement' if future else 'statement'])
        statement['goal'] = self.initial_snapshot['statement']['goal']
        self.requirements.invoke({'operation': 'apply', 'input': {
            'request_id': 'outcome-revise', 'expected_revision': self.req_revision,
            'operations': [{'kind': 'revise_task', 'task_id': self.task_id,
                'expected_statement_revision': self.statement_revision, 'statement': statement}]}})
        self.req_revision += 1
        self.statement_revision += 1
        self.requirements.invoke({'operation': 'apply', 'input': {
            'request_id': 'outcome-publish', 'expected_revision': self.req_revision,
            'operations': [{'kind': 'publish_task', 'task_id': self.task_id,
                'statement_revision': self.statement_revision}]}})
        self.req_revision += 1
        self.initial_snapshot = self.read_snapshot(3)
        self.snapshots.append(self.initial_snapshot)
        assert self.initial_snapshot['statement']['task_requirements'] == statement['task_requirements']
        self.variant = variant
        self.definition = deepcopy(DATA['definitions'][variant])
        self.config = installation(root / 'outcomes')
        cfg = json.loads(self.config.read_text())
        old = json.loads((root / 'prerequisites/execution/execution.json').read_text())
        for key in ('lifecycle', 'gates'):
            cfg[key] = deepcopy(old[key])
        cfg['lifecycle']['actors']['reviewer-D'] = 'reviewer'
        if capability:
            cfg['outcomes'] = {'roles': ['writer']}
        write_json(self.config, cfg)
        self.version_file = self.git_root / 'source-version.txt'
        self.version_file.write_text(DATA['source_initial'])
        self.git('add', 'source-version.txt')
        self.git('commit', '--only', 'source-version.txt', '-m', 'Prepare outcome source')
        self.baseline = self.source()['tree']
        self.payload = self.git_root / 'outputs/subject.bin'
        self.payload.parent.mkdir(parents=True, exist_ok=True)
        self.payload.write_bytes(b'OK\n')
        code, process = invoke(self.config, create(definition=self.definition))
        assert code == 0 and process['status'] == 'published', process
        method = bind(DATA['method'], {'@PY': sys.executable, '@WORK': str(self.git_root)})
        stages = [s['id'] for s in self.definition['stages']]
        if variant == 'check':
            method['verification_plan']['green_stages'].append('finish')
        self.contract = deepcopy(self.contract)
        self.contract.update(requirements_snapshot_revision=3, workspace_id=None,
            process_ref={'goal_type': 'writing', 'revision': process['process_revision']},
            stage_expectations=[{'stage_id': stage, 'values': [], 'files': [],
                'methods': ['guard'] if stage != 'finish' or variant == 'check' else []} for stage in stages],
            methods=[method], evidence_plan=deepcopy(DATA['evidence_plans'][variant]))
        code, setup = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'apply', 'input': {
            'request_id': 'outcome-setup', 'expected_revision': process['revision'],
            'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': self.contract}]}})
        assert code == 0 and setup['status'] == 'applied', setup
        self.revision, self.generation = setup['revision'], 1
        self.contract_revision = setup['results'][0]['contract_revision']
        self.database = self.config.parent / 'state/execution.sqlite'
        module = subject()
        self.store = module.Store(module.load_config(self.config))
        self.origin, self.origins_by_visit, self.observed_frames = {}, {}, {}
        self.presentations, self.current_proofs, self.first_evidence = {}, {}, None
        self.verdict = 'proved'
        self.expected_keys = [] if future else ['report', 'zeta']

    def observe(self, frame, **options):
        if self.variant != 'check' or frame['stage_id'] != 'finish':
            return super().observe(frame, **options)
        self.sync()
        before = self.source()
        method = self.contract['methods'][0]
        actual = subprocess.run(method['argv'], cwd=method['cwd'],
            env=dict(os.environ, **method['environment']), capture_output=True, timeout=30)
        after = self.source()
        assert actual.returncode in (0, 7) and actual.stderr == b'' and before == after
        receipt = {'id': self.identity('subject'), 'method': 'guard', 'obligations': ['guard'],
            'argv': method['argv'], 'cwd': method['cwd'], 'environment': method['environment'],
            'actual_exit_code': actual.returncode, 'stdout': actual.stdout.decode(), 'stderr': '',
            'stdout_bytes_hex': actual.stdout.hex(), 'stderr_bytes_hex': '', 'capture_complete': True,
            'interpretable': True, 'timed_out': False, 'guard': False,
            'passed': actual.returncode == 0 and b'OK' in actual.stdout,
            'commit': before['commit'], 'tree': before['tree'],
            'source_before': before, 'source_after': after, 'source_unchanged': True}
        work = {'phase': 'continue', 'arguments': [{'id': 'terminal', 'kind': 'logical',
            'facts': [actual.stdout.decode()], 'assumptions': [],
            'inference': 'Вывод объявленной команды рассмотрен в текущих условиях.',
            'conclusion': 'Зафиксирован фактический результат проверки.', 'verdict': self.verdict,
            'observation_ids': [receipt['id']]}], 'decisions': []}
        attempt, evidence = self.identity('attempt'), self.identity('evidence')
        records = [{'kind': 'attempt', 'id': attempt, 'task_id': self.task_id,
            'generation': self.generation, 'revision': 1, 'data': {
                'status': 'completed', 'stage_id': 'finish', 'iteration': frame['iteration'],
                'context_id': frame['context_id'], 'candidate_id': frame['candidate_id'],
                'source': before, 'methods': [method], 'execution_key': attempt, 'receipts': [receipt]}},
            {'kind': 'evidence', 'id': evidence, 'task_id': self.task_id,
             'generation': self.generation, 'revision': 1,
             'data': {'attempt_id': attempt, 'evidence_work': work}}]
        result = self.store.write_records(request_id=self.identity('proof'),
            expected_revision=self.revision, records=records)
        self.revision = result['revision']
        LOG.append({'actual_child': receipt, 'records': records, 'result': result})
        observed = dict(frame, evidence_id=evidence)
        self.observed_frames[(self.task_id, self.generation, 'finish', frame['context_id'],
            frame['candidate_id'], evidence)] = deepcopy(observed)
        return observed

    def terminal(self, outcome='complete'):
        self.prepared()
        self.perform('write')
        self.perform('review')
        if self.variant != 'review_terminal':
            if outcome == 'not_satisfied':
                self.payload.write_bytes(b'BAD\n')
                self.verdict = 'disproved'
            elif outcome == 'inconclusive':
                self.verdict = 'inconclusive'
            self.perform('finish', outcome=outcome)
        return self

    def next(self, frame, **options):
        previous = self.sync_revision()
        result = super().next(frame, **options)
        self.previous_revision = previous
        return result

    def outcome_packet(self, *, intent='accept', identifier=None, result=True, actor='executor-A'):
        self.sync()
        source = self.source() if intent == 'accept' else None
        return {'schema': 'stride-execution/v1', 'operation': 'outcome', 'input': {
            'request_id': self.identity('outcome-request'), 'expected_revision': self.revision,
            'task_id': self.task_id, 'expected_generation': self.generation,
            'expected_contract_revision': self.contract_revision, 'actor_id': actor,
            'outcome_id': identifier or self.identity('outcome'), 'intent': intent,
            'reason': 'Завершить текущую работу.', 'source': source,
            'result_commit': source['commit'] if source is not None and result else None}}

    def expected(self, packet, *, kind='positive'):
        """Freeze expectations from preparation BEFORE invoking the new subject."""
        state = self.sync()
        value = packet['input']
        if value['intent'] == 'cancel':
            provenance, handler = deepcopy(DATA['cancel_provenance']), None
        else:
            stage = state['current_stage']
            origin, frame = self.origin[stage], self.current_proofs[stage]
            provenance = {k: deepcopy(origin[k]) for k in (
                'visit_id', 'source_generation', 'source_actor_ids', 'gate_id', 'evidence_id')}
            provenance.update(current_gate_id=frame['gate_id'], current_evidence_id=frame['evidence_id'])
            handler = origin['outcome']
        return bind(DATA['expected_fact'], {'@ID': value['outcome_id'], '@GEN': self.generation,
            '@CONTRACT': self.contract_revision, '@KIND': kind, '@STAGE': state['current_stage'],
            '@ITERATION': state['iteration'], '@HANDLER': handler, '@SOURCE': value['source'],
            '@PROVENANCE': provenance, '@KEYS': self.expected_keys if kind == 'positive' else [],
            '@COMMIT': value['result_commit'], '@INTENT': value['intent']})

    def record(self, packet=None, *, kind='positive'):
        packet = self.outcome_packet() if packet is None else packet
        expected = self.expected(packet, kind=kind)
        cursor, physical, neighbors = self.sync(), self.physical(), self.neighbors()
        code, response = raw(self.config, packet)
        assert code == 0, response
        assert response == {'schema': 'stride-execution/v1', 'status': 'outcome_recorded',
            'revision': packet['input']['expected_revision'] + 1,
            'replayed': False, 'changed': True, 'fact': expected}, response
        self.revision = response['revision']
        assert self.sync() == cursor and self.physical() == physical and self.neighbors() == neighbors
        self.read_fact(value=expected)
        return response

    def read_fact(self, *, value, task=None):
        packet = {'schema': 'stride-execution/v1', 'operation': 'query', 'input': {'queries': [{
            'id': 'read', 'kind': 'outcome', 'task_id': task or self.task_id,
            'outcome_id': value['outcome_id']}]}}
        before = current_state(self.database)
        code, response = raw(self.config, packet)
        assert code == 0 and response == {'schema': 'stride-execution/v1', 'status': 'read_only',
            'revision': self.revision, 'results': [{'id': 'read', 'kind': 'outcome', 'result': value}]}, response
        assert current_state(self.database) == before
        return response

    def reject(self, packet, *alternatives):
        before = self.state()
        code, response = raw(self.config, packet)
        assert code == 2 and set(response) == {'schema', 'status', 'reason'}, response
        assert response['schema'] == 'stride-execution/v1' and response['status'] == 'rejected'
        assert any(text.lower() in response['reason'].lower() for text in alternatives), response
        assert self.state() == before
        return response


class Receiver:
    """Register-only real Sequencing: no invented release history or Workspace."""
    def __init__(self, root, snapshot=3):
        self.client = Client(root)
        self.config, self.database, self.serial = self.client.config, self.client.database, 0
        self.apply([{'id': 'register', 'kind': 'register_task', 'task_id': '0042',
            'requirements_snapshot_revision': snapshot}], initial=True)
        value = self.query('task')
        assert value['claim'] is None and value['workspace'] is None and value['state'] == 'available'

    def packet(self, operations, *, initial=False):
        self.serial += 1
        return {'schema': 'stride-sequencing/v1', 'operation': 'apply', 'input': {
            'request_id': 'receiver-' + str(self.serial),
            'expected_revision': 0 if initial else revision(self.database, 'sequencing'),
            'operations': operations}}

    def apply(self, operations, **options):
        return self.client.invoke(self.packet(operations, **options))

    def query(self, kind, task='0042'):
        return self.client.invoke({'schema': 'stride-sequencing/v1', 'operation': 'query',
            'input': {'queries': [{'id': 'read', 'kind': kind, 'task_id': task}]}})['results'][0]['value']


class Delivery:
    def __init__(self, root, s, receiver):
        root.mkdir(parents=True)
        self.config, self.serial = root / 'orchestrator.json', 0
        config = deepcopy(DATA['orchestrator'])
        config['paths']['state'] = 'state'
        config['commands'] = {}
        for name, path in [('execution', s.config), ('requirements', s.requirements.config),
                           ('sequencing', receiver.config)]:
            config['commands'][name] = {'protocol': 'owner-json-v1',
                'argv': [sys.executable, '-B', str(ROOT / 'apps' / name / 'bin' / name),
                         '--config', str(path)], 'cwd': str(path.parent), 'env': {
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'PATH': str(Path(sys.executable).parent) + ':/usr/local/bin:/usr/bin:/bin'},
                'timeout_seconds': 30.0, 'accepted_exit_codes': [0, 2]}
        write_json(self.config, config)

    def invoke(self, command, packet, *, request=None):
        self.serial += 1
        outer = {'schema': 'stride-orchestrator/v1', 'operation': 'invoke', 'input': {
            'request_id': request or 'delivery-' + str(self.serial),
            'command_id': command, 'packet': packet}}
        code, result = raw(self.config, outer, 'orchestrator')
        assert code == 0 and result['status'] == 'delivered', result
        return result


def requirements_packet(s, fact):
    return {'operation': 'apply', 'input': {'request_id': s.identity('requirements-outcome'),
        'expected_revision': revision(s.requirements.database, 'requirements'), 'operations': [{
            'kind': 'record_outcome', 'outcome': {'outcome_id': fact['outcome_id'], 'task_id': fact['task_id'],
            'snapshot_revision': fact['requirements_snapshot_revision'], 'kind': fact['kind'],
            'satisfied_task_requirements': fact['satisfied_task_requirements']}}]}}


def sequencing_packet(receiver, fact):
    return receiver.packet([{'id': 'outcome', 'kind': 'record_execution_outcome',
        'outcome': {'outcome_id': fact['outcome_id'], 'task_id': fact['task_id'], 'kind': fact['kind'],
        'result_commit': fact['result_commit'], 'source_ref': {'owner': 'execution', 'id': fact['outcome_id']}}}])
