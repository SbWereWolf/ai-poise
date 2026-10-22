"""Transparent public-API arrangements; independent proof inputs live in fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import sys

from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.batch.helpers import request, result
from tests.runner.helpers import stage


FIXTURES = Path(__file__).parent / 'fixtures' / 'file_proofs'
OLD_FIELDS = json.loads((FIXTURES / 'old-contract-error.json').read_text())


class ProofFileContractUnavailable(RuntimeError):
    """Only the precisely observed superseded public contract is acceptable RED."""


class ProofArrangementError(RuntimeError):
    """An invalid owner/phase/control is never evidence for missing behavior."""


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def require(condition, detail):
    if not condition:
        raise ProofArrangementError(detail)


class Scenario:
    def __init__(self, project, *, first='A', machine=False):
        self.project = project
        stages = [
            stage('measure', 'produce', {'complete': 'audit'}, True, [], ['measure']),
            stage('audit', 'inspect', {'clear': 'refine', 'changes_requested': 'measure'}, True, [], ['audit', 'measure'], role='reviewer'),
            stage('refine', 'produce', {'complete': 'audit2'}, True, [], ['refine']),
            stage('audit2', 'inspect', {'clear': None, 'changes_requested': 'refine'}, True, [], ['audit2', 'refine'], role='reviewer'),
        ]
        workflow = fixture('workflow-base.json')
        workflow['stages'] = stages
        write_json(project['root'] / 'config/workflows/file_proof.json', workflow)
        cfg = project['cfg']
        cfg['workflows'] = {'file_proof': 'config/workflows/file_proof.json'}
        cfg['automatic_checks'] = []
        write_json(project['config_path'], cfg)
        task = deepcopy(project['task'])
        task.update(goal_type='file_proof', methods=[], method_inputs=[],
                    checks={s['id']: [] for s in stages}, artifact_requirements=[])
        task['decomposition']['phases'] = [
            {'stage': s['id'], 'skills': ['task-domain'], 'areas': []} for s in stages]
        task['stage_contracts'] = [
            {'stage_id': s['id'], 'allowed_paths': [], 'entry_requirements': [],
             'exit_requirements': [], 'criteria': fixture('empty-criteria.json')} for s in stages]
        task['evidence_plan'] = {
            s['id']: {'subject_methods': {}, 'arguments': [], 'review_arguments': []}
            for s in stages}
        task['evidence_plan']['measure']['arguments'] = [
            {'id': first, 'kind': 'logical', 'phase': 'prepare', 'observation_methods': []}] if first else []
        task['evidence_plan']['audit']['review_arguments'] = [first] if first else []
        if machine:
            method = fixture('machine-method.json')
            method['argv'][0] = sys.executable
            task['methods'] = [method]
            task['checks']['measure'] = ['REAL-RECEIPT']
            task['method_inputs'] = fixture('file_proof_support-literal-1.json')
        self.task = task
        self.author = WorkPoise(project['config_path'], 'proof-author')
        self.reviewer = WorkPoise(project['config_path'], 'proof-reviewer')
        self.runtime = self.author
        self.tools = WorkTools(self.runtime)
        self.context = self.invoke('bootstrap', {
            'task': task, 'decision': None, 'feedback': None, 'rework_stage': None})
        require(self.context['stage'] == 'measure', 'Expected actual measure admission')
        self.root = Path(self.context['task_root'])
        self.serial = 0

    def invoke(self, op, args):
        return self.tools.invoke(request(op, args))

    def path(self, identifier='A'):
        return self.root / 'proofs' / (identifier.lower() + '.json')

    def write(self, identifier='A', data=b'A\r\n'):
        path = self.path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def argument(self, identifier='A', **changes):
        value = fixture('argument.json')
        value.update(id=identifier, path=str(self.path(identifier)),
                     claim=f'Файл подтверждает значение {identifier}.')
        value.update(changes)
        return value

    def payload(self, arguments=(), decisions=()):
        value = result(self.context)
        value['evidence_work'] = {
            'phase': 'prepare', 'arguments': list(arguments), 'decisions': list(decisions)}
        if self.context['handler'] == 'inspect':
            value['stage_work'] = {
                'coverage': 'Осмотрены текущие файлы доказательств.',
                'findings': [], 'resolution_decisions': []}
        return value

    def submit(self, arguments=(), decisions=(), *, required_contract=False):
        try:
            response = self.invoke('verify', {
                'result': self.payload(arguments, decisions), 'artifacts': []})
        except PoiseError as error:
            if required_contract and str(error) == OLD_FIELDS:
                raise ProofFileContractUnavailable(str(error)) from error
            raise
        return response

    def register(self, *identifiers):
        for identifier in identifiers:
            self.write(identifier, b'B\r\n' if identifier == 'B' else b'A\r\n')
        response = self.submit(
            [self.argument(identifier) for identifier in identifiers], required_contract=True)
        require(response['status'] == 'verified', f'Valid presentation failed: {response}')
        return response

    def show(self, kind='evidence'):
        return self.invoke('show', {'queries': [{'id': 'selected', 'kind': kind}]})['results'][0]['value']

    def read(self, identifier='A', representation='text', *, query=None, required_contract=True):
        query = query or {'id': 'read-' + identifier, 'kind': 'proof_file',
                          'argument_id': identifier, 'representation': representation}
        try:
            response = self.invoke('show', {'queries': [query]})
        except PoiseError as error:
            if required_contract and str(error) == 'Unknown batch query kind':
                raise ProofFileContractUnavailable(str(error)) from error
            raise
        return response['results'][0]['value']

    def decide(self, identifier='A', decision='accepted'):
        return {'argument_id': identifier, 'decision': decision,
                'reason': 'Текущее содержимое проверено.'}

    def transfer(self, *, advance=True):
        task = self.show('task')
        self.serial += 1
        released = self.invoke('release', {
            'task_id': 'T1', 'request_id': f'proof-handoff-{self.serial}',
            'expected_version': task['version'], 'reason': 'Synthetic fixture role handoff.'})
        require(released['status'] == 'ownership_released', 'Ordinary release failed')
        self.runtime = self.reviewer if self.runtime is self.author else self.author
        self.tools = WorkTools(self.runtime)
        self.context = self.invoke('bootstrap', {
            'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None})
        if advance:
            self.context = self.invoke('bootstrap', {
                'task': None, 'decision': 'continue', 'feedback': None, 'rework_stage': None})
        return self.context

    def refine(self, a_state='accepted'):
        identifiers = ('B',) if a_state == 'absent' else ('B', 'A')
        self.register(*identifiers)
        self.transfer()
        decisions = [self.decide('B')]
        if a_state == 'accepted':
            decisions.append(self.decide('A'))
        review = self.submit(decisions=decisions)
        require(review['status'] == 'verified' and review['stage_outcome'] == 'clear',
                'Actual initial reviewer decisions were not clear')
        self.transfer()
        require(self.context['stage'] == 'refine', 'Expected real author refine stage')
        proofs = self.show()['arguments']
        other = next((p for p in proofs if p['id'] == 'B'), None)
        require(other is not None and other['state'] == 'accepted', 'Missing accepted B sentinel')
        current = next((p for p in proofs if p['id'] == 'A'), None)
        require(current is None if a_state == 'absent' else current['state'] == a_state,
                'Actual A state does not match negative-case arrangement')
        require(not any(d['argument_id'] == 'A' and d['decision'] == 'rejected'
                        for d in self.show()['decisions']), 'Unexpected prior A rejection')
        return proofs

    def business(self):
        statements = fixture('business-queries.json')
        with self.runtime.store.transaction() as db:
            rows = {key: [list(row) for row in db.execute(sql)]
                    for key, sql in statements.items()}
        files = {str(p): p.read_bytes() for p in (self.path('A'), self.path('B')) if p.is_file()}
        return rows, files

    def refused(self, action, prefix):
        before = self.business()
        reached = False
        try:
            action()
        except PoiseError as error:
            reached = True
            assert str(error).startswith(prefix + ':'), str(error)
        assert reached, 'Public subject did not reject the invalid request'
        assert self.business() == before

    def read_controls(self):
        for representation, expected in [('text', 'A\r\n'), ('base64', 'QQ0K')]:
            value = self.read(representation=representation)
            require(value == {
                'status': 'read_only', 'task': 'T1', 'argument_id': 'A',
                'path': str(self.path()), 'representation': representation,
                'content': expected, 'size_bytes': 3}, 'Valid exact read control failed')
