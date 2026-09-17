"""Registered legacy handoff files are rehomed, never silently accepted in place."""
from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest

from conftest import WorkPoise, add_test, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from .helpers import bootstrap, request, result, verify


@pytest.fixture
def legacy(project):
    first = WorkPoise(project['config_path'], 'A')
    tools = WorkTools(first)
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    handoff = tools.invoke(request('handoff', {
        'request_id': 'legacy-handoff', 'reason': 'save incomplete work',
        'result': result(context), 'commit_message': 'Preserve incomplete task work',
        'artifact_paths': [],
    }))
    old_root = Path(context['task_root'])
    records = first.store.artifact_records('T1')
    assert any(Path(row['path']).name == 'source.bundle' for row in records)
    original_bytes = {row['id']: Path(row['path']).read_bytes() for row in records}
    project['cfg']['paths']['standalone_tasks'] = 'relocated'
    write_json(project['config_path'], project['cfg'])
    current = WorkPoise(project['config_path'], 'B')
    task = current.task_queries.record('T1')
    args = {
        'request_id': 'recover-legacy-1', 'task_id': 'T1',
        'expected_version': task['_version'],
        'artifact_ids': [row['id'] for row in records],
        'source_roots': {'task': str(old_root)},
        'reason': 'authorized standalone root change',
        'authorization': 'operator approved root migration',
    }
    return {'runtime': current, 'tools': WorkTools(current), 'args': args,
            'records': records, 'bytes': original_bytes, 'old_root': old_root,
            'new_root': current.state / 'relocated' / 'T1', 'handoff': handoff}


def history(h):
    # Whole before/after snapshots of immutable records, not a generated oracle.
    with h.store.transaction() as db:
        return {
            'tasks': [tuple(x) for x in db.execute(
                'SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata FROM tasks ORDER BY id')],
            'submissions': [tuple(x) for x in db.execute('SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq')],
            'handoffs': [tuple(x) for x in db.execute('SELECT seq,actor,request_id,task_id,state,data FROM handoffs ORDER BY seq')],
            'execution': [tuple(x) for x in db.execute('SELECT task_id,data,version FROM task_execution ORDER BY task_id')],
            'events': [tuple(x) for x in db.execute('SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq')],
        }


def recover(case, **changes):
    return case['tools'].invoke(request('recover_artifacts', {**case['args'], **changes}))


def test_public_recovery_of_0121_shaped_handoff_removes_verify_blocker(legacy):
    h = legacy['runtime']
    task = h.task_queries.record('T1')
    with pytest.raises(PoiseError, match='област'):
        h._candidate_artifacts(task, [], h._roots(task))
    before = history(h)
    saved = recover(legacy)
    assert saved['status'] == 'artifacts_recovered'
    assert saved['task'] == 'T1'
    assert history(h) == before
    records = h.store.artifact_records('T1')
    assert {row['id'] for row in records} == set(legacy['bytes'])
    for row in records:
        old = next(x for x in legacy['records'] if x['id'] == row['id'])
        relative = Path(old['path']).relative_to(legacy['old_root'])
        assert Path(row['path']) == legacy['new_root'] / relative
        assert Path(row['path']).read_bytes() == legacy['bytes'][row['id']]
        assert Path(old['path']).read_bytes() == legacy['bytes'][row['id']]
    assert len(h._candidate_artifacts(task, [], h._roots(task))) == len(records)
    resumed = legacy['tools'].invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert verify(legacy['tools'], resumed['result_template'])['status'] == 'verified'


def test_exact_replay_returns_original_receipt_and_conflict_is_rejected(legacy):
    first = recover(legacy)
    assert recover(legacy) == first
    with pytest.raises(PoiseError, match='(?i)request.*conflict'):
        recover(legacy, reason='different intent with reused ID')
    with legacy['runtime'].store.transaction() as db:
        assert db.execute("SELECT count(*) FROM journal WHERE event='artifacts.recovered'").fetchone()[0] == 1


def test_identical_existing_destination_is_reused_without_overwrite(legacy):
    for row in legacy['records']:
        dest = legacy['new_root'] / Path(row['path']).relative_to(legacy['old_root'])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(legacy['bytes'][row['id']])
    inodes = {p: p.stat().st_ino for p in legacy['new_root'].rglob('*') if p.is_file()}
    recover(legacy)
    assert {p: p.stat().st_ino for p in inodes} == inodes


@pytest.mark.parametrize('case,pattern', [
    ('digest', 'digest'), ('missing', 'file|существ'), ('source_link', 'symlink'),
    ('source_parent_link', 'symlink'), ('destination_link', 'symlink'),
    ('destination_conflict', 'different|conflict'), ('wrong_root', 'identity|owner'),
    ('version', 'version'), ('unregistered', 'registered'), ('owner', 'owner'),
    ('claimed', 'released|claim'), ('authorization', 'authorization'),
])
def test_rejects_unsafe_or_stale_recovery_before_reference_changes(legacy, case, pattern):
    args = deepcopy(legacy['args'])
    row = legacy['records'][-1]
    source = Path(row['path'])
    dest = legacy['new_root'] / source.relative_to(legacy['old_root'])
    h = legacy['runtime']
    if case == 'digest': source.write_bytes(b'changed after registration')
    elif case == 'missing': source.unlink()
    elif case == 'source_link':
        outside = source.parent / 'not-registered'
        source.rename(outside); source.symlink_to(outside)
    elif case == 'source_parent_link':
        moved = source.parent.with_name(source.parent.name + '-moved')
        source.parent.rename(moved); source.parent.symlink_to(moved, target_is_directory=True)
    elif case == 'destination_link':
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(source)
    elif case == 'destination_conflict':
        dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(b'foreign destination')
    elif case == 'wrong_root': args['source_roots']['task'] = str(legacy['old_root'].parent)
    elif case == 'version': args['expected_version'] -= 1
    elif case == 'unregistered': args['artifact_ids'] = ['not-a-registered-artifact']
    elif case == 'owner':
        with h.store.transaction() as db:
            db.execute('UPDATE artifacts SET owner=? WHERE id=?', ('foreign-task', row['id']))
    elif case == 'claimed':
        legacy['tools'].invoke(request('bootstrap', {
            'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
        args['expected_version'] = h.task_queries.record('T1')['_version']
    elif case == 'authorization': args['authorization'] = ''
    before, registered = history(h), h.store.artifact_records('T1')
    with pytest.raises(PoiseError, match='(?i)' + pattern):
        legacy['tools'].invoke(request('recover_artifacts', args))
    assert h.store.artifact_records('T1') == registered
    assert history(h) == before


def test_database_failure_keeps_old_references_and_identical_retry_works(legacy):
    h = legacy['runtime']
    before = history(h)
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER reject_rebind BEFORE UPDATE ON artifacts BEGIN SELECT RAISE(ABORT,'injected rebind failure'); END")
    with pytest.raises((PoiseError, sqlite3.IntegrityError), match='injected rebind failure'):
        recover(legacy)
    assert history(h) == before
    assert h.store.artifact_records('T1') == legacy['records']
    assert any(p.is_file() for p in legacy['new_root'].rglob('*'))
    with h.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM journal WHERE event='artifacts.recovered'").fetchone()[0] == 0
        db.execute('DROP TRIGGER reject_rebind')
    assert recover(legacy)['status'] == 'artifacts_recovered'


def test_partial_file_publication_never_rebinds_and_retry_does_not_overwrite(legacy, monkeypatch):
    import os
    link = os.link
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2: raise OSError('injected publication failure')
        return link(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'link', fail_second)
        with pytest.raises(PoiseError, match='injected publication failure'):
            recover(legacy)
    assert legacy['runtime'].store.artifact_records('T1') == legacy['records']
    assert recover(legacy)['status'] == 'artifacts_recovered'


def test_ordinary_validation_remains_strict_after_recovery(legacy):
    recover(legacy)
    h = legacy['runtime']; task = h.task_queries.record('T1')
    with pytest.raises(PoiseError, match='област'):
        h.validate_artifact_paths([legacy['records'][0]['path']], task)
    new = h.store.artifact_records('T1')[0]
    Path(new['path']).write_bytes(b'altered destination')
    with pytest.raises(PoiseError, match='изменён'):
        h._candidate_artifacts(task, [], h._roots(task))


def test_public_recovery_preserves_sprint_and_task_scope(project):
    from sprints.helpers import publish_existing_contract
    from .helpers import text_artifact
    from poise.infrastructure.task_paths import task_root, sprint_root
    first = WorkPoise(project['config_path'], 'A')
    project['task'] = publish_existing_contract(first, project['task'], 'S1')
    tools = WorkTools(first)
    context = bootstrap(tools, project)
    tools.invoke(request('artifacts', {'items':[
        text_artifact('sprint', 'shared.md'), text_artifact('task', 'local.md')]}))
    add_test(context['worktree'])
    tools.invoke(request('handoff', {
        'request_id':'sprint-handoff', 'reason':'save work', 'result':result(context),
        'commit_message':'Save sprint work', 'artifact_paths':[]}))
    records = first.store.artifact_records('T1')
    roots = {'task':context['task_root'], 'sprint':context['sprint_root']}
    project['cfg']['paths']['sprints'] = 'relocated_sprints'
    write_json(project['config_path'], project['cfg'])
    h = WorkPoise(project['config_path'], 'B')
    args = {'request_id':'recover-sprint','task_id':'T1',
        'expected_version':h.task_queries.record('T1')['_version'],
        'artifact_ids':[r['id'] for r in records], 'source_roots':roots,
        'reason':'authorized root migration','authorization':'operator approved'}
    before = history(h)
    receipt = WorkTools(h).invoke(request('recover_artifacts', args))
    assert history(h) == before
    new_roots = {'task':task_root(h.state,h.paths,'T1','S1'),
                 'sprint':sprint_root(h.state,h.paths,'S1')}
    for old, new in zip(records, receipt['artifacts'], strict=True):
        assert new['path'] == str(new_roots[old['scope']] / Path(old['path']).relative_to(roots[old['scope']]))
        assert new['id'] == old['id']
    with h.store.transaction() as db:
        linked = db.execute('SELECT artifact_id FROM sprint_artifacts WHERE sprint_id=?',('S1',)).fetchall()
    assert {r[0] for r in linked} == {r['id'] for r in records if r['scope'] == 'sprint'}


def test_recovery_does_not_require_active_process_snapshot(legacy):
    import json
    h = legacy['runtime']
    with h.store.transaction() as db:
        db.execute('UPDATE tasks SET status=?,metadata=? WHERE id=?',
                   ('newborn',json.dumps({'sprint_id':None,'goal':'retained draft',
                    'newborn':{'draft':{},'ready':False}}),'T1'))
    before = history(h)
    assert recover(legacy)['status'] == 'artifacts_recovered'
    assert history(h) == before


@pytest.mark.parametrize('bad', [[], ['duplicate','duplicate'], ['x', 1]])
def test_recovery_rejects_invalid_id_collection(legacy, bad):
    with pytest.raises(PoiseError, match='artifact IDs'):
        recover(legacy, artifact_ids=bad)
    assert legacy['runtime'].store.artifact_records('T1') == legacy['records']


def test_factory_rejects_destination_scope_change_before_publication(tmp_path):
    import hashlib
    from poise.artifacts import artifact_identity
    from poise.infrastructure.artifact_factory import FileArtifactFactory
    from .helpers import batch_config
    old = tmp_path/'old'
    source = old/'task/T1/source.bundle'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'original')
    row = {'id':artifact_identity('sprint','S1','task/T1/source.bundle'),
           'scope':'sprint','owner':'S1','path':str(source),
           'digest':hashlib.sha256(b'original').hexdigest()}
    new = tmp_path/'new'
    factory = FileArtifactFactory({'sprint':new,'task':new/'task/T1'},batch_config(),tmp_path/'lock',1,.01)
    with pytest.raises(PoiseError, match='scope/owner identity'):
        factory.recover_registered([row],{'sprint':str(old)},{'sprint':'S1','task':'T1'})
    assert not new.exists()


def test_fifo_source_rejected_without_blocking(legacy):
    import os
    source = Path(legacy['records'][0]['path'])
    source.unlink(); os.mkfifo(source)
    with pytest.raises(PoiseError, match='regular file'):
        recover(legacy)
    assert legacy['runtime'].store.artifact_records('T1') == legacy['records']


def test_two_explicit_root_changes_preserve_history_then_verify(legacy,project):
    """0142: chained configuration changes use registered identities, not aliases."""
    first=recover(legacy)
    h=legacy['runtime'];before=history(h)
    migrated=h.store.artifact_records('T1')
    project['cfg']['paths']['standalone_tasks']='second-relocation'
    write_json(project['config_path'],project['cfg'])
    current=WorkPoise(project['config_path'],'second-migration-operator')
    tools=WorkTools(current)
    second={**legacy['args'],'request_id':'second-root-change',
            'source_roots':{'task':str(legacy['new_root'])}}
    saved=tools.invoke(request('recover_artifacts',second))
    assert tools.invoke(request('recover_artifacts',deepcopy(second)))==saved
    assert history(current)==before
    latest=current.store.artifact_records('T1')
    assert {r['id']:r['digest'] for r in latest}=={r['id']:r['digest'] for r in migrated}
    assert all(Path(r['path']).is_relative_to(current.state/'second-relocation'/'T1') for r in latest)
    for old in legacy['records']+migrated:
        assert Path(old['path']).read_bytes()==legacy['bytes'][old['id']]
    assert all(Path(r['path']).read_bytes()==legacy['bytes'][r['id']] for r in latest)
    # Immutable historical receipts remain historical; current paths come from the registry.
    assert tools.invoke(request('recover_artifacts',legacy['args']))==first
    context=tools.invoke(request('bootstrap',{'task':{'id':'T1'},'decision':None,'feedback':None,'rework_stage':None}))
    assert verify(tools,context['result_template'])['status']=='verified'
