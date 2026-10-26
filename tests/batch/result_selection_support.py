"""Transparent fixtures; old/new outputs have an independent literal oracle."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

import conftest
from conftest import WorkPoise, bind_task_requirements, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.batch.helpers import request


FIXTURES = Path(__file__).parent / 'fixtures/result_selection'


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


class Case:
    def __init__(self, project, *, checked=False, actor='selection-author'):
        self.project = project
        cfg = project['cfg']
        cfg['automatic_checks'] = []
        write_json(project['config_path'], cfg)
        self.workflow = fixture('workflow.json')
        write_json(project['root'] / 'config/workflows/development.json', self.workflow)
        self.task = deepcopy(project['task'])
        self.task.update(requirements=['Результат сохраняет выбранные секции и байты.'],
                         methods=[], method_inputs=[], checks={'implementation': [], 'audit': []},
                         artifact_requirements=[], content_contract=fixture('content-contract.json'))
        self.task['decomposition']['phases'] = [
            {'stage': stage['id'], 'skills': ['task-domain'], 'areas': []}
            for stage in self.workflow['stages']]
        self.task['stage_contracts'] = [
            {'stage_id': stage['id'], 'allowed_paths': stage['allowed_paths'],
             'entry_requirements': [], 'exit_requirements': [],
             'criteria': {'requirements': [], 'definition_of_ready': [], 'definition_of_done': []}}
            for stage in self.workflow['stages']]
        self.task['evidence_plan'] = {
            stage['id']: {'subject_methods': {}, 'arguments': [], 'review_arguments': []}
            for stage in self.workflow['stages']}
        bind_task_requirements(self.task, project['requirements_registry'])
        self.runs = project['root'] / 'check-runs.txt'
        self.allow = project['root'] / 'allow-check'
        if checked:
            method = fixture('controlled-check.json')
            method['argv'][0] = sys.executable
            method['environment'] = {'RUNS': str(self.runs), 'ALLOW': str(self.allow)}
            self.task['methods'] = [method]
            self.task['checks']['implementation'] = ['CONTROLLED']
            self.task['method_inputs'] = [{
                'method_id': 'CONTROLLED', 'repository_inputs': [], 'future_outputs': [],
                'reference_profile': {'runner': 'python', 'parser': 'inline-no-path-arguments', 'version': 1}}]
        self.actor = actor
        self.tools = WorkTools(WorkPoise(project['config_path'], actor))
        self.context = self.tools.invoke(request('bootstrap', {
            'task': self.task, 'decision': None, 'feedback': None, 'rework_stage': None}))
        self.root = Path(self.context['task_root'])
        self.source = self.root / 'inputs/input.bin'
        self.source.parent.mkdir()
        self.source.write_bytes(bytes.fromhex(fixture('oracles.json')['bytes_hex']))
        self.target = self.root / fixture('oracles.json')['proof_relative']

    def selected(self):
        return selected(self.context)

    def old(self):
        return request('verify', {'result': fixture('old-payload.json'), 'artifacts': [
            {'scope': 'task', 'path': 'proofs/input.bin', 'source': {
                'kind': 'file', 'path': str(self.source),
                'digest': hashlib.sha256(bytes.fromhex(fixture('oracles.json')['bytes_hex'])).hexdigest()}}]})

    def saved(self):
        with self.tools.runtime.store.transaction() as db:
            row = db.execute('SELECT seq,data FROM submissions WHERE task_id=? ORDER BY seq DESC LIMIT 1', ('T1',)).fetchone()
            assert row is not None
            sections = db.execute('SELECT section_id,content FROM section_layers WHERE submission_id=? ORDER BY section_id', (row['seq'],)).fetchall()
        return {**json.loads(row['data']), 'sections': {item['section_id']: item['content'] for item in sections}}

    def snapshot(self):
        queries = {
            'tasks': 'SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id FROM tasks ORDER BY id',
            'submissions': 'SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq',
            'results': 'SELECT task_id,submission_id,data FROM task_results ORDER BY task_id,submission_id',
            'sections': 'SELECT submission_id,task_id,section_id,content,content_state FROM section_layers ORDER BY submission_id,section_id',
            'packets': 'SELECT task_id,stage,iteration,digest FROM work_packets ORDER BY task_id,stage,iteration',
            'artifacts': 'SELECT id,owner,scope,path,digest FROM artifacts ORDER BY id',
            'links': 'SELECT task_id,artifact_id FROM task_artifacts ORDER BY task_id,artifact_id',
            'verified_events': "SELECT task_id,event,data FROM journal WHERE event='stage.verified' ORDER BY seq",
        }
        with self.tools.runtime.store.transaction() as db:
            return {key: [tuple(row) for row in db.execute(sql)] for key, sql in queries.items()}

    def release(self):
        current = self.tools.runtime.current_task()
        return self.tools.invoke(request('release', {
            'task_id': 'T1', 'request_id': 'release-selection-fixture',
            'expected_version': current['version'], 'reason': 'Transfer fixture ownership without changing its result.'}))

    def reopen(self, actor=None):
        actor = actor or self.actor
        self.tools = WorkTools(WorkPoise(self.project['config_path'], actor))
        self.context = self.tools.invoke(request('bootstrap', {
            'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}))


def selected(context):
    packet = fixture('selection.json')
    packet['input']['selection']['expected'] = {
        'task': context['task'], 'stage': context['stage'],
        'iteration': context['iteration'], 'version': context['version']}
    return packet


def assert_saved(case, response, *, expected_paths=None):
    oracle = fixture('oracles.json')
    assert response['status'] == 'verified'
    assert response['stage_outcome'] == 'complete'
    assert response['task'] == 'T1' and response['stage'] == 'implementation'
    assert case.target.read_bytes() == bytes.fromhex(oracle['bytes_hex'])
    assert set(case.saved()) == set(oracle['selected_metadata_keys'])
    assert case.saved()['sections'] == {'report': oracle['report']}
    assert case.saved()['commit_message'] == 'Preserve selected proof bytes'
    chosen_paths = [case.target] if expected_paths is None else expected_paths
    assert case.saved()['artifact_paths'] == [str(path) for path in chosen_paths]
    with case.tools.runtime.store.transaction() as db:
        rows = db.execute('SELECT scope,owner,path,digest FROM artifacts WHERE path=?', (str(case.target),)).fetchall()
    assert len(rows) == 1
    assert tuple(rows[0]) == ('task', 'T1', str(case.target), hashlib.sha256(bytes.fromhex(oracle['bytes_hex'])).hexdigest())


def assert_refused(case, packet, code):
    before = case.snapshot()
    with pytest.raises(PoiseError) as error:
        case.tools.invoke(packet)
    assert str(error.value).startswith(code), str(error.value)
    assert case.snapshot() == before
    assert not case.target.exists()
    return str(error.value)


@pytest.fixture
def case_factory(tmp_path, monkeypatch):
    dispatchers = conftest.track_runtime_dispatchers(monkeypatch)
    count = 0

    def make(raw=False, **options):
        nonlocal count
        root = tmp_path / ('store-' + str(count))
        count += 1
        root.mkdir()
        project = conftest.project.__wrapped__(root, monkeypatch)
        return project if raw else Case(project, **options)

    yield make
    for dispatcher in dispatchers:
        dispatcher.close()
