"""Explicit retirement of an unused transfer setting, without Task migration."""
from copy import deepcopy
import json

import pytest

from poise.common import digest, load_config
from poise.modules.foundation.errors import PoiseError
from poise.modules.task_cleanup.domain import CleanupIntent, CleanupRun
from tests.conftest import write_json
from .test_update import installed_project, update_tools, update_request, work_tools


def obsolete_config(config_path):
    _, config, processes = load_config(config_path)
    config['runtime_services']['transfer']['bundles_directory'] = 'bundles'
    write_json(config_path, config)
    return config, processes


def recovery_request(config_path, config, processes):
    packet = update_request(config_path, digest({'config': config, 'processes': processes}))
    packet.update(schema='project-transfer-config-recovery-1',
                  reason='Retire the unused transfer bundles directory setting.',
                  authorization='User authorized configuration recovery with Task preservation.')
    return packet


def test_recovery_preserves_released_newborn_and_configuration_history(project):
    settings, path, _ = installed_project(project)
    tools = work_tools(path, 'draft-owner')
    tools.invoke({'operation': 'task', 'input': {
        'action': 'create', 'task_id': 'DRAFT', 'sprint_id': None,
        'request_id': 'draft-create'}, 'messages': []})
    tools.invoke({'operation': 'handoff', 'input': {
        'request_id': 'draft-release', 'reason': 'Review the draft later.',
        'result': None, 'commit_message': None, 'artifact_paths': []}, 'messages': []})
    before_task = tools.runtime.task_queries.record('DRAFT')
    before_database = tools.runtime.store.database.path.read_bytes()
    process_bytes = {name: (path.parent / name).read_bytes()
                     for name in tools.runtime.cfg['processes'].values()}
    config, processes = obsolete_config(path)
    packet = recovery_request(path, config, processes)

    result = update_tools(settings).apply(packet)

    assert result['status'] == 'updated'
    expected = deepcopy(config)
    del expected['runtime_services']['transfer']['bundles_directory']
    _, actual, actual_processes = load_config(path)
    assert actual == expected
    assert actual_processes == processes
    assert tools.runtime.task_queries.record('DRAFT') == before_task
    assert tools.runtime.store.database.path.read_bytes() == before_database
    assert {name: (path.parent / name).read_bytes() for name in process_bytes} == process_bytes
    saved = json.loads((project['root'] / packet['receipt_path']).read_text())
    assert saved['recovery']['before_config'] == config
    assert saved['recovery']['before_processes'] == processes
    assert update_tools(settings).apply(packet)['replayed'] is True
    assert tools.runtime.task_queries.record('DRAFT') == before_task


@pytest.mark.parametrize('case', ['stale', 'extra_edit', 'invalid_candidate', 'owned'])
def test_recovery_rejects_unsafe_or_unrelated_changes(project, case):
    settings, path, _ = installed_project(project)
    if case == 'owned':
        tools = work_tools(path, 'active-owner')
        tools.invoke({'operation': 'bootstrap', 'input': {
            'task': project['task'], 'decision': None, 'feedback': None,
            'rework_stage': None}, 'messages': []})
    config, processes = obsolete_config(path)
    if case == 'invalid_candidate':
        config['runtime_services']['transfer']['unexpected_setting'] = True
        write_json(path, config)
    packet = recovery_request(path, config, processes)
    if case == 'stale':
        packet['expected_revision'] = '0' * 64
    elif case == 'extra_edit':
        packet['manifest_edits'] = [{'path': ['git', 'base_ref'], 'value': 'wrong'}]
    before = path.read_bytes()
    reasons = {'stale': 'revision', 'extra_edit': 'only',
               'invalid_candidate': 'unexpected_setting', 'owned': 'active|ownership'}
    with pytest.raises(PoiseError, match=reasons[case]):
        update_tools(settings).apply(packet)
    assert path.read_bytes() == before
    assert not (project['root'] / packet['receipt_path']).exists()


@pytest.mark.parametrize('pending', [
    {'kind': 'check_attempt'},
    {'kind': 'result_integration', 'status': 'running', 'phase': 'checks_running'},
    {'kind': 'result_integration', 'status': 'blocked', 'phase': 'publication_failed'},
    {'kind': 'result_integration', 'status': 'blocked', 'phase': 'checks_failed'},
])
def test_recovery_refuses_unfinished_external_work_on_terminal_task(project, pending):
    settings, path, _ = installed_project(project)
    tools = work_tools(path, 'historical-owner')
    tools.invoke({'operation': 'bootstrap', 'input': {
        'task': project['task'], 'decision': None, 'feedback': None,
        'rework_stage': None}, 'messages': []})
    # Test-only relational arrangement: a terminal ledger cannot hide live effects.
    with tools.runtime.store.database.transaction() as db:
        execution = json.loads(db.execute(
            'SELECT data FROM task_execution WHERE task_id=?', ('T1',)).fetchone()[0])
        execution['pending'] = pending
        db.execute('UPDATE task_execution SET data=? WHERE task_id=?',
                   (json.dumps(execution), 'T1'))
        db.execute('UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?', ('completed', 'T1'))
    config, processes = obsolete_config(path)
    packet = recovery_request(path, config, processes)
    before = path.read_bytes()
    before_database = tools.runtime.store.database.path.read_bytes()
    with pytest.raises(PoiseError, match='pending|external'):
        update_tools(settings).apply(packet)
    assert path.read_bytes() == before
    assert tools.runtime.store.database.path.read_bytes() == before_database


@pytest.mark.parametrize('drift', ['manifest', 'process'])
def test_recovery_rejects_foreign_drift_during_interrupted_replay(project, monkeypatch, drift):
    import poise.infrastructure.project_config as owner
    settings, path, _ = installed_project(project)
    config, processes = obsolete_config(path)
    packet = recovery_request(path, config, processes)
    original = owner.atomic_write
    def interrupted(target, content, mode):
        if target == project['root'] / packet['receipt_path']:
            raise OSError('Interrupted after replace')
        return original(target, content, mode)
    monkeypatch.setattr(owner, 'atomic_write', interrupted)
    with pytest.raises(PoiseError, match='interrupted'):
        update_tools(settings).apply(packet)
    monkeypatch.setattr(owner, 'atomic_write', original)
    if drift == 'manifest':
        value = json.loads(path.read_text())
        value['git']['remote'] = 'foreign-change'
        write_json(path, value)
        modified_path = path
    else:
        modified_path = path.parent / config['processes']['development']
        value = json.loads(modified_path.read_text())
        value['stages'][0]['instruction'] = 'Foreign process change'
        write_json(modified_path, value)
    before = modified_path.read_bytes()
    with pytest.raises(PoiseError, match='Unmanaged'):
        update_tools(settings).apply(packet)
    assert modified_path.read_bytes() == before
    assert not (project['root'] / packet['receipt_path']).exists()


def test_recovery_resumes_after_manifest_replace_without_rewriting_tasks(project, monkeypatch):
    import poise.infrastructure.project_config as owner
    settings, path, _ = installed_project(project)
    tools = work_tools(path, 'preserved-owner')
    tools.invoke({'operation': 'task', 'input': {
        'action': 'create', 'task_id': 'DRAFT', 'sprint_id': None,
        'request_id': 'draft-create'}, 'messages': []})
    tools.invoke({'operation': 'handoff', 'input': {
        'request_id': 'draft-release', 'reason': 'Resume later.',
        'result': None, 'commit_message': None, 'artifact_paths': []}, 'messages': []})
    before_task = tools.runtime.task_queries.record('DRAFT')
    before_database = tools.runtime.store.database.path.read_bytes()
    config, processes = obsolete_config(path)
    process_bytes = {name: (path.parent / name).read_bytes() for name in config['processes'].values()}
    packet = recovery_request(path, config, processes)
    original = owner.atomic_write
    def interrupted(target, content, mode):
        if target == project['root'] / packet['receipt_path']:
            raise OSError('Lost response after configuration publication')
        return original(target, content, mode)
    monkeypatch.setattr(owner, 'atomic_write', interrupted)
    with pytest.raises(PoiseError, match='interrupted'):
        update_tools(settings).apply(packet)
    _, published, _ = load_config(path)
    assert 'bundles_directory' not in published['runtime_services']['transfer']
    pending_path = (project['root'] / packet['receipt_path']).with_suffix('.json.pending')
    pending_recovery = json.loads(pending_path.read_text())['recovery']
    assert pending_recovery == {
        'before_config': config, 'before_processes': processes,
        'after_config': published, 'after_processes': processes,
        'reason': packet['reason'], 'authorization': packet['authorization']}
    monkeypatch.setattr(owner, 'atomic_write', original)
    resumed = update_tools(settings).apply(packet)
    assert resumed['status'] == 'updated'
    assert resumed['replayed'] is True
    saved = json.loads((project['root'] / packet['receipt_path']).read_text())
    assert saved['recovery'] == pending_recovery
    assert tools.runtime.task_queries.record('DRAFT') == before_task
    assert tools.runtime.store.database.path.read_bytes() == before_database
    assert {name: (path.parent / name).read_bytes() for name in process_bytes} == process_bytes


@pytest.mark.parametrize('case', ['reason', 'authorization', 'missing_authorization',
    'process_updates', 'state_relocation', 'probe_repository', 'absent_key', 'escape_receipt'])
def test_recovery_request_is_narrow_and_explicit(project, case):
    settings, path, _ = installed_project(project)
    config, processes = obsolete_config(path)
    packet = recovery_request(path, config, processes)
    if case in ('reason', 'authorization'):
        packet[case] = ' '
    elif case == 'missing_authorization':
        del packet['authorization']
    elif case == 'process_updates':
        packet[case] = [{}]
    elif case == 'state_relocation':
        packet[case] = {}
    elif case == 'probe_repository':
        packet[case] = True
    elif case == 'absent_key':
        del config['runtime_services']['transfer']['bundles_directory']
        write_json(path, config)
        packet['expected_revision'] = digest({'config': config, 'processes': processes})
    else:
        packet['receipt_path'] = '../escaped.json'
    before = path.read_bytes()
    with pytest.raises(PoiseError):
        update_tools(settings).apply(packet)
    assert path.read_bytes() == before
    assert not (project['root'] / 'operations/project-update-1.json').exists()


@pytest.mark.parametrize('drift', ['manifest', 'process', 'request'])
def test_completed_recovery_replay_rejects_drift(project, drift):
    settings, path, _ = installed_project(project)
    config, processes = obsolete_config(path)
    packet = recovery_request(path, config, processes)
    update_tools(settings).apply(packet)
    if drift == 'request':
        packet['reason'] = 'Different authorized request'
        changed = path
    else:
        changed = path if drift == 'manifest' else path.parent / config['processes']['development']
        value = json.loads(changed.read_text())
        if drift == 'manifest':
            value['git']['remote'] = 'foreign-change'
        else:
            value['stages'][0]['instruction'] = 'Foreign process change'
        write_json(changed, value)
    before = changed.read_bytes()
    with pytest.raises(PoiseError, match='Unmanaged|Different request'):
        update_tools(settings).apply(packet)
    assert changed.read_bytes() == before


@pytest.mark.parametrize('status,pending', [
    ('available', None),
    ('completed', {'kind': 'result_integration', 'status': 'integrated', 'phase': 'integrated'}),
    ('cancelled', {'kind': 'task_cleanup', **CleanupRun.new(
        CleanupIntent('paused', 'T1', 'Preserve resources for later decision.', None),
        [{'kind': 'branch', 'name': 'tasks/T1', 'commit': 'a' * 40}]).to_storage()}),
])
def test_recovery_preserves_unowned_available_and_settled_history(project, status, pending):
    settings, path, _ = installed_project(project)
    tools = work_tools(path, 'fixture-owner')
    tools.invoke({'operation': 'bootstrap', 'input': {
        'task': project['task'], 'decision': None, 'feedback': None,
        'rework_stage': None}, 'messages': []})
    with tools.runtime.store.database.transaction() as db:
        execution = json.loads(db.execute('SELECT data FROM task_execution WHERE task_id=?', ('T1',)).fetchone()[0])
        execution.update(pending=pending, worktree=None, branch=None, publication=None)
        db.execute('UPDATE task_execution SET data=? WHERE task_id=?', (json.dumps(execution), 'T1'))
        db.execute('UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?', (status, 'T1'))
    before_database = tools.runtime.store.database.path.read_bytes()
    config, processes = obsolete_config(path)
    assert update_tools(settings).apply(recovery_request(path, config, processes))['status'] == 'updated'
    assert tools.runtime.store.database.path.read_bytes() == before_database


def test_recovery_observes_existing_material_lock_before_publication(project):
    import fcntl
    from poise.common import configured_root, descendant
    settings, path, _ = installed_project(project)
    _, config, _ = load_config(path)
    config['limits']['lock_seconds'] = 0.05
    write_json(path, config)
    config, processes = obsolete_config(path)
    lock = descendant(configured_root(path.parent, config['paths']['state']),
                      config['batch']['artifact_lock'])
    lock.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_bytes()
    with lock.open('a+b') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PoiseError, match='lock'):
            update_tools(settings).apply(recovery_request(path, config, processes))
    assert path.read_bytes() == before
    assert not (project['root'] / 'operations/project-update-1.json').exists()


def test_recovery_preserves_released_newborn_retained_workspace(project):
    settings, path, _ = installed_project(project)
    tools = work_tools(path, 'restart-fixture-owner')
    tools.invoke({'operation': 'bootstrap', 'input': {
        'task': project['task'], 'decision': None, 'feedback': None,
        'rework_stage': None}, 'messages': []})
    retained = project['root'] / 'retained-worktree'
    retained.mkdir()
    source = retained / 'unfinished.txt'
    source.write_text('Preserved uncommitted developer work\n')
    # Isolate the persisted, released restart shape; do not manufacture ready.
    with tools.runtime.store.database.transaction() as db:
        execution = json.loads(db.execute('SELECT data FROM task_execution WHERE task_id=?', ('T1',)).fetchone()[0])
        execution.update(worktree=str(retained), branch='tasks/T1', pending=None, publication=None)
        db.execute('UPDATE task_execution SET data=? WHERE task_id=?', (json.dumps(execution), 'T1'))
        db.execute('UPDATE tasks SET status=?,claimed_by=NULL WHERE id=?', ('newborn', 'T1'))
    before_database = tools.runtime.store.database.path.read_bytes()
    before_source = source.read_bytes()
    config, processes = obsolete_config(path)
    assert update_tools(settings).apply(recovery_request(path, config, processes))['status'] == 'updated'
    assert tools.runtime.store.database.path.read_bytes() == before_database
    assert source.read_bytes() == before_source


@pytest.mark.parametrize('tamper', ['authorization', 'reason', 'after_config', 'after_processes', 'before_config'])
@pytest.mark.parametrize('phase', ['pending', 'completed'])
def test_recovery_rejects_inconsistent_audit(project, monkeypatch, tamper, phase):
    import poise.infrastructure.project_config as owner
    settings, path, _ = installed_project(project)
    config, processes = obsolete_config(path)
    packet = recovery_request(path, config, processes)
    receipt = project['root'] / packet['receipt_path']
    pending = receipt.with_name(receipt.name + '.pending')
    original = owner.atomic_write
    def interrupted(target, content, mode):
        if target == receipt:
            raise OSError('Interrupted before final receipt')
        return original(target, content, mode)
    if phase == 'pending':
        with monkeypatch.context() as patch:
            patch.setattr(owner, 'atomic_write', interrupted)
            with pytest.raises(PoiseError, match='interrupted'):
                update_tools(settings).apply(packet)
    else:
        update_tools(settings).apply(packet)
    audit_path = pending if phase == 'pending' else receipt
    saved = json.loads(audit_path.read_text())
    if tamper in ('authorization', 'reason'):
        saved['recovery'][tamper] = 'Not the original request'
    elif tamper in ('before_config', 'after_config'):
        saved['recovery'][tamper]['git']['remote'] = 'false-audit-remote'
    else:
        saved['recovery'][tamper]['development']['stages'][0]['instruction'] = 'False audit process'
    write_json(audit_path, saved)
    before_manifest = path.read_bytes()
    before_audit = audit_path.read_bytes()
    with pytest.raises(PoiseError, match='audit|snapshots|revision'):
        update_tools(settings).apply(packet)
    assert path.read_bytes() == before_manifest
    assert audit_path.read_bytes() == before_audit
    if phase == 'pending':
        assert not receipt.exists()
