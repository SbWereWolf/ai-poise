"""Independent literal lifecycle checks; all canonical setup uses public owners."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from process_checks import create, installation, invoke, write_json
from store_checks import subject
from apps.sequencing.tests.test_registration import Client, CASES, current_state
from apps.sequencing.tests.test_workspace import Work

APP = Path(__file__).resolve().parents[1]
DATA = json.loads((APP / 'fixtures/lifecycle-contract.json').read_text())


def bind(value, mapping):
    if isinstance(value, dict):
        return {k: bind(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [bind(v, mapping) for v in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def facts(history):
    """Public semantic facts may retain their ordinary record envelope."""
    assert isinstance(history, list) and history
    assert all(isinstance(item, dict) for item in history)
    return [item["data"] if "data" in item else item for item in history]


class Scenario:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.counter = root, 0
        self.requirements = Client(root / 'requirements', 'requirements')
        cfg = json.loads(self.requirements.config.read_text())
        cfg['task_ids'] = {'namespace': {'minimum': 42, 'maximum': 99}, 'width': 4,
                           'progression': {'first': 42, 'step': 1}}
        write_json(self.requirements.config, cfg)
        for step in CASES['published']['req_steps'][:4]:
            packet = bind(step['packet'], {'000017': '0042'})
            if packet['operation'] == 'create_task':
                packet['input']['work_choice'] = {'kind': 'standalone'}
            response = self.requirements.invoke(packet)
            assert response['status'] in ('created', 'applied')
        self.req_revision, self.statement_revision = 4, 2
        self.snapshots = [self.read_snapshot(1)]
        self.initial_snapshot = self.publish('Начальные проверяемые ожидания.')
        assert self.initial_snapshot['snapshot_revision'] == 2
        assert self.initial_snapshot['task_id'] == '0042'
        assert self.initial_snapshot['statement']['goal'] == DATA['goal']
        # This existing normal arrangement registers real snapshot2 and claims
        # through CLI, then prepares an actual linked Git worktree through CLI.
        self.workspace = Work(root / 'sequencing', reference='snapshot')
        self.workspace_response = self.workspace.invoke(self.workspace.packet())
        task = self.workspace.task()
        assert task['requirements_snapshot_revision'] == self.initial_snapshot['snapshot_revision']
        assert task['claim']['claim_id'] == self.workspace.claim_id
        assert task['claim']['claimant'] == self.workspace.owner
        assert task['workspace'] == self.workspace_response['workspace']
        self.git_root = self.workspace.target
        assert self.git_root.is_dir() and (self.git_root / '.git').is_file()
        (self.git_root / 'payload.txt').write_text(DATA['expected']['staged'])
        self.git('add', 'payload.txt')
        for name, content in DATA['expected']['wip'].items():
            (self.git_root / name).write_text(content)
        self.baseline = self.git('rev-parse', 'HEAD^{tree}').strip()
        self.config = installation(root / 'execution')
        cfg = json.loads(self.config.read_text())
        cfg.update(deepcopy(DATA['configuration']))
        cfg['gates'] = {'roots': {'runtime': 'files/runtime', 'task': 'files/task', 'sprint': 'files/sprint'}}
        write_json(self.config, cfg)
        code, process = invoke(self.config, create(definition=deepcopy(DATA['definition'])))
        assert code == 0 and process['status'] == 'published', process
        self.contract = bind(DATA['contract'], {'@PROCESS': process['process_revision'],
                             '@WORKSPACE': self.workspace_response['workspace']['workspace_id']})
        code, setup = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'apply', 'input': {
            'request_id': 'setup', 'expected_revision': 1, 'operations': [
                {'id': 'setup', 'kind': 'setup_execution', 'contract': self.contract}]}})
        assert code == 0 and setup['status'] == 'applied', setup
        self.revision = setup['revision']
        self.generation = 1
        self.contract_revision = setup['results'][0]['contract_revision']
        self.database = root / 'execution/state/execution.sqlite'
        module = subject()
        self.store = module.Store(module.load_config(self.config))
        self.store.write_records(request_id='prior-records', expected_revision=self.revision,
                                 records=deepcopy(DATA['old_records']))
        self.revision += 1
        self.original_records = self.records()
        self.original_workspace = self.physical()
        self.neighbor_before = self.neighbors()
        assert self.execution()['state'] == 'draft' and self.execution()['current_stage'] is None

    def git(self, *args):
        run = subprocess.run(['git', '-C', str(self.git_root), *args], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        return run.stdout

    def physical(self):
        index = Path(self.git('rev-parse', '--git-path', 'index').strip())
        if not index.is_absolute():
            index = self.git_root / index
        return {'head': self.git('rev-parse', 'HEAD'),
                'branch': self.git('symbolic-ref', '--short', 'HEAD').strip(),
                'index': index.read_bytes(),
                'files': {name: (self.git_root / name).read_bytes() for name in DATA['expected']['wip']},
                'workspace': self.workspace.task()['workspace'],
                'neighbor': (self.workspace.repo / 'payload.txt').read_bytes()}

    def neighbors(self):
        return {'requirements': current_state(self.requirements.database),
                'sequencing': current_state(self.workspace.database)}

    def records(self):
        return [deepcopy(record) for record in self.store.read_records('0042')]

    def state(self):
        return {'database': current_state(self.database), 'physical': self.physical(),
                'neighbors': self.neighbors()}

    def query(self, kind):
        query = {'id': 'read', 'kind': kind, 'task_id': '0042'}
        code, response = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'query',
                                             'input': {'queries': [query]}})
        assert code == 0 and response['status'] == 'read_only', response
        return response['results'][0]

    def execution(self):
        return self.query('execution')['value']

    def lifecycle(self):
        value = self.query('lifecycle')
        assert set(value) == {'id', 'kind', 'result'}
        return value['result']

    def read_snapshot(self, revision):
        response = self.requirements.invoke({'operation': 'query', 'input': {'queries': [
            {'id': 'snapshot', 'kind': 'snapshot', 'task_id': '0042', 'snapshot_revision': revision}]}})
        value = response['results'][0]['value']
        assert value['task_id'] == '0042' and value['snapshot_revision'] == revision
        return value

    def publish(self, text, goal=None):
        statement = {'goal': DATA['goal'] if goal is None else goal,
                     'task_requirements': [{'key': 'report', 'status': 'current', 'text': text,
                                            'applications': ['APP-REPORT']}],
                     'definition_of_done': [text]}
        self.requirements.invoke({'operation': 'apply', 'input': {
            'request_id': 'revise-' + str(self.req_revision), 'expected_revision': self.req_revision,
            'operations': [{'kind': 'revise_task', 'task_id': '0042',
                            'expected_statement_revision': self.statement_revision, 'statement': statement}]}})
        self.req_revision += 1
        self.statement_revision += 1
        self.requirements.invoke({'operation': 'apply', 'input': {
            'request_id': 'publish-' + str(self.req_revision), 'expected_revision': self.req_revision,
            'operations': [{'kind': 'publish_task', 'task_id': '0042',
                            'statement_revision': self.statement_revision}]}})
        self.req_revision += 1
        value = self.read_snapshot(len(self.snapshots) + 1)
        self.snapshots.append(value)
        return value

    def packet(self, action, **extra):
        self.counter += 1
        return {'schema': 'stride-execution/v1', 'operation': 'lifecycle', 'input': {
            'action': action, 'request_id': 'lifecycle-' + str(self.counter),
            'expected_revision': self.revision, 'task_id': '0042',
            'expected_generation': self.generation, 'expected_contract_revision': self.contract_revision,
            'actor_id': 'executor-A', **extra}}

    def run(self, packet):
        code, response = invoke(self.config, packet)
        assert code == 0, ('Missing or incorrect public lifecycle behavior', response)
        assert set(response) == set(DATA['expected']['mutation_keys']), response
        assert response['status'] == 'lifecycle_applied' and response['task_id'] == '0042'
        assert response['replayed'] is False
        assert response['revision'] == packet['input']['expected_revision'] + 1
        assert response['result']['action'] == packet['input']['action']
        assert isinstance(response['result']['id'], str) and response['result']['id']
        self.revision, self.generation = response['revision'], response['generation']
        self.contract_revision = response['contract_revision']
        return response

    def act(self, action, **extra):
        return self.run(self.packet(action, **extra))

    def freeze(self, snapshot=None):
        return self.act('freeze', requirements_snapshot=snapshot or self.initial_snapshot)

    def enter(self):
        return self.act('enter', entry_tree=self.baseline)

    def prepared(self):
        self.freeze()
        self.enter()
        return self

    def gate(self, report='Готовый результат.', *, stage=None, iteration=None, generation=None):
        current = self.execution()
        stage = current['current_stage'] if stage is None else stage
        iteration = current['iteration'] if iteration is None else iteration
        generation = self.generation if generation is None else generation
        self.counter += 1
        context_id, candidate_id = 'context-' + str(self.counter), 'candidate-' + str(self.counter)
        declaration = {'action': 'declare', 'request_id': 'declare-' + str(self.counter),
                       'expected_revision': self.revision, 'task_id': '0042', 'generation': generation,
                       'context_id': context_id, 'contract_revision': self.contract_revision,
                       'requirement_ids': ['report'], 'task_content': {'sections': [], 'routes': [], 'requirements': []},
                       'stage_contracts': [{'stage_id': name, 'allowed_paths': [] if name == 'review' else ['outputs/**'],
                           'entry_requirements': [], 'exit_requirements': ['filled'],
                           'criteria': {'requirements': [], 'definition_of_ready': [], 'definition_of_done': []}}
                           for name in ['write', 'review']]}
        code, response = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'gate',
                                             'input': declaration})
        assert code == 0 and response['status'] == 'declared', response
        self.revision = response['revision']
        value = {'action': 'evaluate', 'request_id': 'evaluate-' + str(self.counter),
                 'expected_revision': self.revision, 'task_id': '0042', 'generation': generation,
                 'context_id': context_id, 'candidate_id': candidate_id,
                 'stage_id': stage, 'iteration': iteration,
                 'sections': {'report': report}, 'trace': {}, 'files': []}
        before = self.execution()
        code, response = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'gate', 'input': value})
        assert code == (0 if report.strip() else 2), response
        assert response['accepted'] == bool(report.strip()), response
        assert self.execution() == before, 'Gate success alone changed canonical stage'
        self.revision = response['revision']
        return context_id, candidate_id, response

    def presented(self):
        self.prepared()
        context, candidate, result = self.gate()
        self.act('present', context_id=context, candidate_id=candidate)
        return result

    def proposal(self, snapshot=None, contract=None, actor='executor-A'):
        snapshot = self.initial_snapshot if snapshot is None else snapshot
        proposed = deepcopy(self.contract if contract is None else contract)
        proposed['requirements_snapshot_revision'] = snapshot['snapshot_revision']
        proposed['stage_expectations'][0]['values'] = [{'name': 'report', 'type': 'string', 'required': True}]
        response = self.act('propose_restart', actor_id=actor, proposal_id='proposal-1', contract=proposed,
                            requirements_snapshot=snapshot, reason='Согласовать текущие ожидания.')
        assert response['result']['proposal_id'] == 'proposal-1'
        return proposed

    def approve(self, decision='approved', actor='reviewer-B'):
        response = self.act('decide_restart', actor_id=actor, proposal_id='proposal-1',
                            decision=decision, reason='Проверены границы пересмотра.')
        assert response['result']['decision'] == decision
        return response['result']['approval_id']

    def reject(self, packet, *reason_tokens):
        before = self.state()
        code, response = invoke(self.config, packet)
        assert code == 2 and response['status'] == 'rejected', response
        assert isinstance(response['reason'], str) and response['reason']
        for token in reason_tokens:
            assert token.lower() in response['reason'].lower(), response
        assert self.state() == before
        return response

    def replay(self, packet, original):
        before = self.state()
        code, result = invoke(self.config, packet)
        assert code == 0 and result == dict(original, replayed=True)
        assert self.state() == before

    def preserved(self):
        after = self.records()
        assert all(record in after for record in self.original_records)
        assert self.physical() == self.original_workspace
        assert self.neighbors() == self.neighbor_before


def install_restart_fault(database):
    """Fail the second substantive effect, for either record-first or row-first order."""
    with sqlite3.connect(database) as db:
        for trigger in DATA['restart_fault_triggers']:
            db.execute(trigger['sql'])


def remove_restart_fault(database):
    with sqlite3.connect(database) as db:
        for trigger in DATA['restart_fault_triggers']:
            db.execute('DROP TRIGGER ' + trigger['name'])
