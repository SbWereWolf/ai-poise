"""Real Task owners determine the response location before/after retirement."""
import io
import json
import sqlite3
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise
from sprints.helpers import setup as setup_sprints
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.interfaces.work import execute
from result_integration.helpers import integration_input
from terminal_materials.archive_oracle import no_new_archive, proof_cells
from terminal_materials.helpers import PROOF, agree, arrange, declaration, file_output


@pytest.mark.parametrize('selected_scope', [False, True])
@pytest.mark.parametrize('sufficient', [False, True])
def test_sprint_destination_admitted_before_business_effects(
        project, monkeypatch, selected_scope, sufficient):
    setup_sprints(project)
    runtime = WorkPoise(project['config_path'], 'r')
    sprint_id = 'S-' + 'x' * 140
    initial = {'action': 'draft', 'sprint_id': sprint_id,
               'request_id': 'initial-sprint', 'expected_revision': None,
               'template': {'id': 'basic', 'version': '1'}, 'changes': []}
    if selected_scope:
        created = runtime.sprint_tools.apply(initial)
        assert created['revision'] == 1
    packet = request('sprint', {**initial,
        'sprint_id': None if selected_scope else sprint_id,
        'request_id': 'changed-sprint',
        'expected_revision': 1 if selected_scope else None,
        'template': None if selected_scope else initial['template']})
    # Independent wire/address calculation: actual generated UUIDs have this
    # fixed width; no production preflight or projection generates the oracle.
    address = runtime.runtime / runtime.paths['runs'] / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    budget = len(json.dumps({'status': 'architecture_boundaries_failed',
        'response_path': str(address)}, ensure_ascii=False,
        separators=(',', ':')) + '\n')
    sprint_home = runtime.state / runtime.paths['sprints'] / sprint_id
    sprint_address = sprint_home / runtime.paths['runs'] / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    assert len(json.dumps({'status': 'draft', 'response_path': str(sprint_address)},
        ensure_ascii=False, separators=(',', ':')) + '\n') > budget
    runtime.cfg['limits']['output_chars'] = 2200 if sufficient else budget
    database = runtime.state / runtime.paths['database']

    def rows():
        with sqlite3.connect(database) as db:
            return (db.execute('SELECT id, revision, data FROM sprints WHERE id=?',
                               (sprint_id,)).fetchall(),
                    db.execute('SELECT request_id, digest, data FROM sprint_requests '
                               'WHERE sprint_id=? ORDER BY request_id',
                               (sprint_id,)).fetchall())

    before = rows()
    files_before = sorted(sprint_home.rglob('*'))
    calls = []
    original = WorkTools.invoke

    def invoke(owner, value):
        calls.append(value)
        return original(owner, value)

    monkeypatch.setattr(WorkTools, 'invoke', invoke)
    output = io.StringIO()
    try:
        code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    except PoiseError:
        code = 2
    assert runtime.current_task() is None
    if not sufficient:
        assert calls == [] and code == 2
        assert rows() == before
        assert sorted(sprint_home.rglob('*')) == files_before
    else:
        assert calls == [packet] and code == 0
        view = json.loads(output.getvalue())
        response = Path(view['response_path'])
        full = json.loads(response.read_text())
        assert response.is_relative_to(sprint_home / runtime.paths['runs'])
        assert full['sprint'] == sprint_id and full['status'] == 'draft'
        assert full['revision'] == (2 if selected_scope else 1)
        assert len(output.getvalue()) <= 2200
        after = rows()
        assert len(after[0]) == 1
        assert after[0][0][1] == (2 if selected_scope else 1)
        assert [row[0] for row in after[1]] == (
            ['changed-sprint', 'initial-sprint'] if selected_scope else ['changed-sprint'])


def delivered(runtime, packet):
    output = io.StringIO()
    code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    text = output.getvalue()
    assert len(text) <= runtime.cfg['limits']['output_chars'] and text.endswith('\n')
    view = json.loads(text)
    response = Path(view['response_path'])
    return code, view, response, json.loads(response.read_text())


def test_live_task_response_uses_its_actual_owner_root(project):
    tools, root, _, _ = arrange(project)
    code, view, path, full = delivered(tools.runtime, request('show', {'queries': [
        {'id': 'task', 'kind': 'task'}]}))
    assert code == 0 and full['status'] == 'read_only'
    assert path.is_relative_to(root / tools.runtime.paths['runs'])
    assert full['results'][0]['value']['task'] == 'T1'
    assert root.is_dir() and tools.runtime.task_queries.record('T1')['status'] == 'verified'


@pytest.mark.parametrize('operation', ['integrate', 'cleanup'])
def test_terminal_operation_saves_runtime_response_without_resurrection(project, operation):
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project['root'] / 'result.txt')]))
    before = proof_cells(tools, (PROOF,))
    if operation == 'integrate':
        assert tools.invoke(request('accept', {}))['status'] == 'completed'
        packet = request('integrate', integration_input(project, commit))
    else:
        assert tools.invoke(request('cancel', {'reason': 'Cancel the fixture Task.'}))['status'] == 'cancelled'
        packet = request('cleanup', {
            'task_id': 'T1', 'request_id': 'response-destination-cleanup',
            'authorization': 'Discard the exact fixture commit after cancellation.',
            'commit_disposition': {'kind': 'discard_authorized', 'expected_commit': commit},
        })
    code, view, path, full = delivered(tools.runtime, packet)
    assert code == 0
    assert full['status'] == ('integrated' if operation == 'integrate' else 'cleanup_complete')
    assert path.is_relative_to(tools.runtime.runtime / tools.runtime.paths['runs'])
    assert not root.exists()
    assert not path.is_relative_to(root)
    assert (project['root'] / 'result.txt').read_bytes() == b'customer result\n'
    no_new_archive(tools, before, (PROOF,))


def test_preflight_covers_terminal_destination_before_nonterminal_effect(project, monkeypatch):
    tools, root, source, _ = arrange(project, session='response-' + 'long' * 35)
    agree(tools, declaration([file_output(source, project['root'] / 'result.txt')]))
    runtime = tools.runtime
    predicted = root / runtime.paths['runs'] / '00000000-0000-4000-8000-000000000000' / runtime.paths['response']
    current_minimum = {'status': 'content_requirements_failed', 'response_path': str(predicted)}
    runtime.cfg['limits']['output_chars'] = len(
        json.dumps(current_minimum, ensure_ascii=False, separators=(',', ':')) + '\n')
    terminal_minimum = {'status': 'cancelled', 'response_path': str(
        runtime.runtime / runtime.paths['runs'] / '00000000-0000-4000-8000-000000000000' / runtime.paths['response'])}
    assert len(json.dumps(terminal_minimum, ensure_ascii=False, separators=(',', ':')) + '\n') > runtime.cfg['limits']['output_chars']
    before = runtime.task_queries.record('T1')
    calls = []
    original = WorkTools.invoke

    def invoke(owner, packet):
        calls.append(packet)
        return original(owner, packet)

    monkeypatch.setattr(WorkTools, 'invoke', invoke)
    output = io.StringIO()
    try:
        code = execute(runtime, io.BytesIO(json.dumps(request('cancel', {
            'reason': 'Refuse before cancelling when the actual destination cannot fit.'})).encode()), output)
    except PoiseError:
        code = 2
    after = runtime.task_queries.record('T1')
    assert calls == [] and code == 2
    assert after['status'] == 'verified' and after['_version'] == before['_version']
    assert root.is_dir()
    assert runtime.current_task()['id'] == 'T1'
