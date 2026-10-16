"""Real Task owners determine the response location before/after retirement."""
import io
import json
import sqlite3
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import WorkPoise
from conftest import write_json
from ddd.test_task_id_allocation import automatic_intent, configure, policy
from sprints.helpers import setup as setup_sprints
from sprints.helpers import task as sprint_task
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.interfaces.work import execute
from result_integration.helpers import integration_input
from terminal_materials.archive_oracle import no_new_archive, proof_cells
from terminal_materials.helpers import PROOF, agree, arrange, declaration, file_output


@pytest.mark.parametrize('sufficient', [False, True])
def test_automatic_bootstrap_admission_does_not_consume_identity(project, monkeypatch, sufficient):
    configure(project, policy())
    project['cfg']['paths']['standalone_tasks'] = 'standalone-' + 'x' * 100
    write_json(project['config_path'], project['cfg'])
    runtime = WorkPoise(project['config_path'], 'r')
    packet = request('bootstrap', {'task': automatic_intent(project, 'automatic-budget'),
        'decision': None, 'feedback': None, 'rework_stage': None})
    response_suffix = Path(runtime.paths['runs']) / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    budget = len(json.dumps({'status': 'architecture_boundaries_failed',
        'response_path': str(runtime.runtime / response_suffix)}, ensure_ascii=False,
        separators=(',', ':')) + '\n')
    task_home = runtime.state / runtime.paths['standalone_tasks'] / '0001'
    assert len(json.dumps({'status': 'active',
        'response_path': str(task_home / response_suffix)}, ensure_ascii=False,
        separators=(',', ':')) + '\n') > budget
    runtime.cfg['limits']['output_chars'] = 2200 if sufficient else budget
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
    if not sufficient:
        assert calls == [] and code == 2
        assert runtime.current_task() is None
        with sqlite3.connect(runtime.state / runtime.paths['database']) as db:
            assert db.execute('SELECT id, claimed_by FROM tasks').fetchall() == []
        assert not task_home.exists()
        assert not (runtime.state / runtime.paths['worktrees']).exists()
        runtime.cfg['limits']['output_chars'] = 2200
        output = io.StringIO()
        code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    assert calls == [packet] and code == 0
    response = Path(json.loads(output.getvalue())['response_path'])
    full = json.loads(response.read_text())
    assert response.is_relative_to(task_home / runtime.paths['runs'])
    assert full['status'] == 'active' and full['task'] == '0001'
    assert full['allocation'] == {'request_id': 'automatic-budget',
                                 'task_id': '0001', 'replayed': False}
    assert runtime.current_task()['id'] == '0001'
    assert runtime.current_task()['claimed_by'] == runtime.session
    assert Path(full['worktree']).is_dir()
    assert len(output.getvalue()) <= 2200


@pytest.mark.parametrize('sufficient', [False, True])
def test_taskless_bootstrap_admits_selected_sprint_root(project, monkeypatch, sufficient):
    setup_sprints(project)
    runtime = WorkPoise(project['config_path'], 'r')
    sprint_id = 'S-' + 'x' * 140
    runtime.sprint_tools.apply({'action': 'draft', 'sprint_id': sprint_id,
        'request_id': 'initial-sprint', 'expected_revision': None,
        'template': {'id': 'basic', 'version': '1'}, 'changes': []})
    packet = request('bootstrap', {'task': None, 'decision': None,
                                   'feedback': None, 'rework_stage': None})
    suffix = Path(runtime.paths['runs']) / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    budget = len(json.dumps({'status': 'architecture_boundaries_failed',
        'response_path': str(runtime.runtime / suffix)}, ensure_ascii=False,
        separators=(',', ':')) + '\n')
    home = runtime.state / runtime.paths['sprints'] / sprint_id
    assert len(json.dumps({'status': 'draft', 'response_path': str(home / suffix)},
        ensure_ascii=False, separators=(',', ':')) + '\n') > budget
    runtime.cfg['limits']['output_chars'] = 2200 if sufficient else budget
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
    with sqlite3.connect(runtime.state / runtime.paths['database']) as db:
        assert db.execute('SELECT id, revision FROM sprints').fetchall() == [(sprint_id, 1)]
        assert db.execute('SELECT id FROM tasks').fetchall() == []
    if not sufficient:
        assert calls == [] and code == 2
        assert not (home / runtime.paths['runs']).exists()
    else:
        assert calls == [packet] and code == 0
        response = Path(json.loads(output.getvalue())['response_path'])
        full = json.loads(response.read_text())
        assert response.is_relative_to(home / runtime.paths['runs'])
        assert full['sprint'] == sprint_id and full['status'] == 'draft'
        assert full['revision'] == 1
        assert len(output.getvalue()) <= 2200


@pytest.mark.parametrize('sufficient', [False, True])
def test_bootstrap_completion_admits_terminal_response_before_accepting(
        project, monkeypatch, sufficient):
    tools, root, source, _ = arrange(project, session='response-' + 'long' * 35)
    agree(tools, declaration([file_output(source, project['root'] / 'result.txt')]))
    runtime = tools.runtime
    suffix = Path(runtime.paths['runs']) / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    budget = len(json.dumps({'status': 'architecture_boundaries_failed',
        'response_path': str(root / suffix)}, ensure_ascii=False,
        separators=(',', ':')) + '\n')
    assert len(json.dumps({'status': 'completed',
        'response_path': str(runtime.runtime / suffix)}, ensure_ascii=False,
        separators=(',', ':')) + '\n') > budget
    runtime.cfg['limits']['output_chars'] = 2200 if sufficient else budget
    packet = request('bootstrap', {'task': None, 'decision': 'continue',
                                   'feedback': None, 'rework_stage': None})
    with sqlite3.connect(runtime.state / runtime.paths['database']) as db:
        before = db.execute('SELECT status, version, metadata FROM tasks WHERE id=?',
                            ('T1',)).fetchone()
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
    with sqlite3.connect(runtime.state / runtime.paths['database']) as db:
        after = db.execute('SELECT status, version, metadata FROM tasks WHERE id=?',
                           ('T1',)).fetchone()
    if not sufficient:
        assert calls == [] and code == 2
        assert after == before and after[0] == 'verified'
        assert root.is_dir()
    else:
        assert calls == [packet] and code == 0
        response = Path(json.loads(output.getvalue())['response_path'])
        full = json.loads(response.read_text())
        assert response.is_relative_to(runtime.runtime / runtime.paths['runs'])
        assert full['status'] == 'completed' and full['task'] == 'T1'
        assert after[0] == 'completed'
        assert len(output.getvalue()) <= 2200


@pytest.mark.parametrize('ids', [None, 1, 'D'])
def test_invalid_detachment_saves_rejection_without_business_changes(project, ids):
    setup_sprints(project)
    runtime = WorkPoise(project['config_path'], 'r')
    runtime.sprint_tools.apply({'action': 'draft', 'sprint_id': 'S',
        'request_id': 'initial-sprint', 'expected_revision': None,
        'template': {'id': 'basic', 'version': '1'}, 'changes': []})
    born = runtime.task_action({'action': 'create', 'task_id': 'D',
        'sprint_id': 'S', 'request_id': 'initial-member'})
    assert born['revision'] == 0 and born['sprint_revision'] == 2
    old_home = runtime.state / runtime.paths['sprints'] / 'S' / 'task' / 'D'
    new_home = runtime.state / runtime.paths['standalone_tasks'] / 'D'
    old_home.mkdir(parents=True, exist_ok=True)
    material = old_home / 'owned.txt'
    material.write_bytes(b'preserved Task material\n')
    files_before = set(old_home.rglob('*'))

    def rows():
        with sqlite3.connect(runtime.state / runtime.paths['database']) as db:
            return (db.execute('SELECT status, claimed_by, version, metadata FROM tasks '
                               'WHERE id=?', ('D',)).fetchone(),
                    db.execute('SELECT revision, data FROM sprints WHERE id=?',
                               ('S',)).fetchone(),
                    db.execute('SELECT request_id, digest, data FROM sprint_requests '
                               'WHERE sprint_id=? ORDER BY request_id', ('S',)).fetchall())

    before = rows()
    packet = request('sprint', {'action': 'draft', 'sprint_id': 'S',
        'request_id': 'invalid-detach', 'expected_revision': 2, 'template': None,
        'changes': [{'kind': 'remove_tasks', 'ids': ids}]})
    output = io.StringIO()
    code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    assert code == 2 and rows() == before
    current = runtime.current_task()
    assert current['id'] == 'D' and current['claimed_by'] == runtime.session
    assert current['sprint_id'] == 'S' and current['revision'] == 0
    view = json.loads(output.getvalue())
    assert view['status'] == 'rejected' and len(output.getvalue()) <= 2200
    response = Path(view['response_path'])
    assert response.parent.parent == old_home / runtime.paths['runs']
    assert response.name == runtime.paths['response']
    full = json.loads(response.read_text())
    assert full['status'] == 'rejected'
    assert full['reason'] == 'remove_tasks requires unique IDs'
    assert material.read_bytes() == b'preserved Task material\n'
    assert set(old_home.rglob('*')) - files_before == {
        response, response.parent, response.parent.parent}
    assert not new_home.exists()


@pytest.mark.parametrize('sufficient', [False, True])
@pytest.mark.parametrize('operation', ['detach', 'cancel_explicit', 'cancel_selected'])
def test_current_newborn_detachment_admits_standalone_destination(
        project, monkeypatch, sufficient, operation):
    setup_sprints(project)
    project['cfg']['paths']['standalone_tasks'] = 'standalone-' + 'x' * 100
    write_json(project['config_path'], project['cfg'])
    runtime = WorkPoise(project['config_path'], 'r')
    runtime.sprint_tools.apply({'action': 'draft', 'sprint_id': 'S',
        'request_id': 'initial-sprint', 'expected_revision': None,
        'template': {'id': 'basic', 'version': '1'}, 'changes': []})
    born = runtime.task_action({'action': 'create', 'task_id': 'D',
        'sprint_id': 'S', 'request_id': 'initial-member'})
    assert born['revision'] == 0 and born['sprint_revision'] == 2
    old_home = runtime.state / runtime.paths['sprints'] / 'S' / 'task' / 'D'
    new_home = runtime.state / runtime.paths['standalone_tasks'] / 'D'
    suffix = Path(runtime.paths['runs']) / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    budget = len(json.dumps({'status': 'architecture_boundaries_failed',
        'response_path': str(old_home / suffix)}, ensure_ascii=False,
        separators=(',', ':')) + '\n')
    assert len(json.dumps({'status': 'draft', 'response_path': str(new_home / suffix)},
        ensure_ascii=False, separators=(',', ':')) + '\n') > budget
    runtime.cfg['limits']['output_chars'] = 2200 if sufficient else budget
    packet = request('sprint', {'action': 'draft', 'sprint_id': 'S',
        'request_id': 'detach-member', 'expected_revision': 2, 'template': None,
        'changes': [{'kind': 'remove_tasks', 'ids': ['D']}]})
    if operation != 'detach':
        packet = request('sprint', {'action': 'cancel',
            'sprint_id': None if operation == 'cancel_selected' else 'S',
            'request_id': 'cancel-member-draft',
            'reason': 'Cancel the draft while preserving its newborn Task.'})

    def rows():
        with sqlite3.connect(runtime.state / runtime.paths['database']) as db:
            return (db.execute('SELECT status, claimed_by, version, metadata FROM tasks '
                               'WHERE id=?', ('D',)).fetchone(),
                    db.execute('SELECT revision, data FROM sprints WHERE id=?',
                               ('S',)).fetchone(),
                    db.execute('SELECT request_id, digest, data FROM sprint_requests '
                               'WHERE sprint_id=? ORDER BY request_id', ('S',)).fetchall())

    before = rows()
    old_home.mkdir(parents=True, exist_ok=True)
    material = old_home / 'owned.txt'
    material.write_bytes(b'preserved Task material\n')
    files_before = sorted(old_home.rglob('*'))
    original_bytes = {path: path.read_bytes() for path in files_before if path.is_file()}
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
    after = rows()
    current = runtime.current_task()
    assert current['id'] == 'D' and current['claimed_by'] == runtime.session
    if not sufficient:
        assert calls == [] and code == 2
        assert after == before
        assert current['sprint_id'] == 'S' and current['revision'] == 0
        view = json.loads(output.getvalue())
        response = Path(view['response_path'])
        assert view['status'] == 'rejected' and len(output.getvalue()) <= budget
        assert response.is_relative_to(old_home / runtime.paths['runs'])
        assert response.name == runtime.paths['response']
        assert response.parent.parent == old_home / runtime.paths['runs']
        full = json.loads(response.read_text())
        assert full['status'] == 'rejected' and full['reason']
        assert {path: path.read_bytes() for path in original_bytes} == original_bytes
        assert set(old_home.rglob('*')) - set(files_before) == {
            response, response.parent, response.parent.parent}
        assert not new_home.exists()
    else:
        assert calls == [packet] and code == 0
        response = Path(json.loads(output.getvalue())['response_path'])
        full = json.loads(response.read_text())
        assert response.is_relative_to(new_home / runtime.paths['runs'])
        expected_status = 'draft' if operation == 'detach' else 'cancelled'
        assert full['status'] == expected_status and full['sprint'] == 'S' and full['revision'] == 3
        assert current['sprint_id'] is None and current['revision'] == 1
        assert after[0][:3] == ('newborn', runtime.session, 1)
        assert json.loads(after[0][3])['sprint_id'] is None
        assert after[1][0] == 3
        if operation == 'detach':
            assert json.loads(after[1][1])['aggregate']['plan']['tasks'] == []
            assert [row[0] for row in after[2]] == ['detach-member', 'initial-sprint']
        else:
            assert json.loads(after[1][1])['aggregate']['state'] == 'cancelled'
            assert [row[0] for row in after[2]] == ['cancel-member-draft', 'initial-sprint']
        assert sorted(old_home.rglob('*')) == files_before
        assert len(output.getvalue()) <= 2200


@pytest.mark.parametrize('sufficient', [False, True])
def test_cancelled_recovery_admits_live_destination_before_restoring(
        project, monkeypatch, sufficient):
    from runtime_services.test_cancelled_task_recovery import cancelled
    from runtime_services.test_task_restart import (
        execution, immutable_audit_rows, work_packet_rows, wip_state,
    )

    project['cfg']['paths']['standalone_tasks'] = 'standalone-' + 'x' * 100
    tools, task_id, worktree, prior = cancelled(project)
    runtime = tools.runtime
    before = runtime.task_queries.record(task_id)
    state = execution(runtime, task_id)
    audit = immutable_audit_rows(runtime, task_id)
    packets = work_packet_rows(runtime, task_id)
    work = wip_state(worktree)

    def actions():
        with sqlite3.connect(runtime.state / runtime.paths['database']) as db:
            return db.execute('SELECT data FROM journal WHERE task_id=? '
                'AND event=? ORDER BY seq', (task_id, 'newborn.action')).fetchall()

    receipts = actions()
    assert before['status'] == 'cancelled' and before['claimed_by'] is None
    assert state['pending']['kind'] == 'task_cleanup'
    assert runtime.current_task() is None
    suffix = Path(runtime.paths['runs']) / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    budget = len(json.dumps({'status': 'architecture_boundaries_failed',
        'response_path': str(runtime.runtime / suffix)}, ensure_ascii=False,
        separators=(',', ':')) + '\n')
    live_home = runtime.state / runtime.paths['standalone_tasks'] / task_id
    assert len(json.dumps({'status': 'task_recovered',
        'response_path': str(live_home / suffix)}, ensure_ascii=False,
        separators=(',', ':')) + '\n') > budget
    runtime.cfg['limits']['output_chars'] = 2200 if sufficient else budget
    packet = request('task', {'action': 'recover_cancelled',
        'request_id': 'recover-budget', 'task_id': task_id,
        'expected_version': before['version'],
        'reason': 'Restore the accidentally cancelled unfinished Task.',
        'authorization': 'Explicit isolated test recovery authorization.'})
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
    if not sufficient:
        assert calls == [] and code == 2
        assert runtime.task_queries.record(task_id) == before
        assert execution(runtime, task_id) == state
        assert immutable_audit_rows(runtime, task_id) == audit
        assert work_packet_rows(runtime, task_id) == packets
        assert actions() == receipts
        assert wip_state(worktree) == work
        assert runtime.current_task() is None
        view = json.loads(output.getvalue())
        response = Path(view['response_path'])
        assert view['status'] == 'rejected' and len(output.getvalue()) <= budget
        assert response.is_relative_to(runtime.runtime / runtime.paths['runs'])
        assert json.loads(response.read_text())['status'] == 'rejected'
        runtime.cfg['limits']['output_chars'] = 2200
        output = io.StringIO()
        code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    assert calls == [packet] and code == 0
    view = json.loads(output.getvalue())
    response = Path(view['response_path'])
    full = json.loads(response.read_text())
    assert response.is_relative_to(live_home / runtime.paths['runs'])
    assert full['status'] == 'task_recovered' and full['task'] == task_id
    assert full['task_status'] == prior['status'] and full['replayed'] is False
    assert full['withdrawn_cleanup'] == state['pending']
    after = runtime.task_queries.record(task_id)
    assert after['status'] == prior['status'] and after['version'] == before['version'] + 1
    assert after['claimed_by'] is None and runtime.current_task() is None
    assert after['history'][-1]['event'] == 'cancellation_recovered'
    assert execution(runtime, task_id) == {**state, 'pending': None}
    restored_audit = immutable_audit_rows(runtime, task_id)
    for table, records in audit.items():
        assert restored_audit[table][:len(records)] == records
    assert wip_state(worktree) == work
    assert work_packet_rows(runtime, task_id) == packets
    assert len(actions()) == len(receipts) + 1
    assert len(output.getvalue()) <= 2200


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


@pytest.mark.parametrize('action', ['create', 'duplicate'])
@pytest.mark.parametrize('sufficient', [False, True])
def test_new_sprint_member_address_admitted_before_creation(
        project, monkeypatch, action, sufficient):
    setup_sprints(project)
    runtime = WorkPoise(project['config_path'], 'r')
    sprint_id = 'S-' + 'x' * 140
    runtime.sprint_tools.apply({'action': 'draft', 'sprint_id': sprint_id,
        'request_id': 'initial-sprint', 'expected_revision': None,
        'template': {'id': 'basic', 'version': '1'}, 'changes': []})
    if action == 'duplicate':
        parent = sprint_task(project, 'P')
        parent['sprint_id'] = None
        runtime.task_commands.create(parent, runtime.session,
            runtime.workflows['development'], [], {'config_hash': runtime.config_hash},
            None, runtime.cfg.get('task_ids'), runtime._creation_base(),
            runtime.cfg['task_decomposition'])
    args = {'action': action, 'task_id': 'D', 'sprint_id': sprint_id,
            'request_id': 'new-member'}
    if action == 'duplicate':
        args['parent_id'] = 'P'
    packet = request('task', args)
    standalone = runtime.state / runtime.paths['standalone_tasks'] / 'D'
    assumed_address = standalone / runtime.paths['runs'] / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    budget = len(json.dumps({'status': 'architecture_boundaries_failed',
        'response_path': str(assumed_address)}, ensure_ascii=False,
        separators=(',', ':')) + '\n')
    actual_home = runtime.state / runtime.paths['sprints'] / sprint_id / 'task' / 'D'
    actual_address = actual_home / runtime.paths['runs'] / (
        '00000000-0000-4000-8000-000000000000') / runtime.paths['response']
    assert len(json.dumps({'status': 'newborn', 'response_path': str(actual_address)},
        ensure_ascii=False, separators=(',', ':')) + '\n') > budget
    runtime.cfg['limits']['output_chars'] = 2200 if sufficient else budget

    def rows():
        with sqlite3.connect(runtime.state / runtime.paths['database']) as db:
            return (db.execute('SELECT id, status, version, metadata FROM tasks '
                               'WHERE id IN (?,?) ORDER BY id', ('P', 'D')).fetchall(),
                    db.execute('SELECT revision, data FROM sprints WHERE id=?',
                               (sprint_id,)).fetchall(),
                    db.execute('SELECT sprint_id, task_id FROM sprint_members '
                               'WHERE sprint_id=? ORDER BY task_id',
                               (sprint_id,)).fetchall())

    before = rows()
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
    if not sufficient:
        assert calls == [] and code == 2
        assert runtime.current_task() is None
        assert rows() == before
        assert not actual_home.exists() and not standalone.exists()
    else:
        assert calls == [packet] and code == 0
        response = Path(json.loads(output.getvalue())['response_path'])
        full = json.loads(response.read_text())
        assert response.is_relative_to(actual_home / runtime.paths['runs'])
        assert full['status'] == 'newborn' and full['task'] == 'D'
        assert full['sprint'] == sprint_id and full['revision'] == 0
        assert len(output.getvalue()) <= 2200
        after = rows()
        assert after[0][0][:3] == ('D', 'newborn', 0)
        assert after[1][0][0] == 2
        assert json.loads(after[1][0][1])['aggregate']['plan']['tasks'] == ['D']
        assert after[2] == []  # Draft membership is not published membership.
        assert not standalone.exists()
        if action == 'duplicate':
            assert full['duplicate']['parent_id'] == 'P'
            assert runtime.current_task() is None
        else:
            assert runtime.current_task()['id'] == 'D'
            assert full['claimed_by'] == runtime.session


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
