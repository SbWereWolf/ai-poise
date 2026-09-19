"""Accepted family files are delivered and verified under the local Task owner."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from conftest import WorkPoise, git
from batch.helpers import request, result, text_artifact
from sprints.helpers import setup, task, draft, publish
from tasks.test_duplicate_creation import duplicate
from runtime_services.test_duplicate_reuse import reuse
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError


def artifact_family(project, *, scope='task', content_rule=False, optional=False, binary=False, extra=False,
                    command='print("checked")'):
    setup(project)
    h = WorkPoise(project['config_path'], 'planner')
    contract = task(project, 'P', command=command)
    contract['sprint_id'] = None if scope == 'task' else 'SOURCE'
    minimum = 0 if optional else 1
    if content_rule:
        contract['content_contract']['requirements'].append({
            'id': 'DELIVER', 'kind': 'artifact', 'stages': ['work'], 'phase': 'post',
            'scope': scope, 'pattern': 'result.txt', 'minimum': minimum, 'maximum': 1,
            'source': {'kind': 'preexisting'}})
        contract['stage_contracts'][0]['exit_requirements'] = ['DELIVER']
    else:
        contract['artifact_requirements'] = [
            {'scope': scope, 'pattern': 'artifacts/result.txt', 'minimum': minimum, 'maximum': 1}]
    if scope == 'sprint':
        from sprints.helpers import publish_existing_contract
        publish_existing_contract(h, contract, 'SOURCE')
    else:
        h.task_commands.create(contract, h.session, h.processes['development'], [],
            {'config_hash': h.config_hash}, None, h.cfg.get('task_ids'), h._creation_base(),
            h.cfg['task_decomposition'])
    client = WorkTools(h)
    draft(client, [])
    duplicate(client)
    h.bootstrap({'id': 'D'})
    born = h.task_queries.record('D')
    h.task_action({'action': 'ready', 'task_id': 'D', 'expected_revision': born['revision'],
                   'request_id': 'ready-D'})
    publish(client, h.sprint_tools.overview('S')['revision'])
    producer = WorkPoise(project['config_path'], 'producer')
    context = producer.bootstrap({'id': 'P'})
    (Path(context['worktree']) / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    files = [] if optional else [text_artifact(scope, 'result.txt', 'accepted family result\n')]
    payload = result(context)
    if binary:
        path = Path(context['task_root']) / 'artifacts' / 'result.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'\x00\xffaccepted\x80')
        payload['artifact_paths'] = [str(path)]
        files = []
    if extra:
        files.append(text_artifact(scope, 'second.txt', 'another accepted result\n'))
    report = WorkTools(producer).invoke(request('verify', {'result': payload, 'artifacts': files}))
    assert report['status'] == 'verified'
    assert WorkTools(producer).invoke(request('accept', {}))['status'] == 'completed'
    git(project['app'], 'merge', '--ff-only', report['commit'])
    return WorkPoise(project['config_path'], 'consumer'), report


@pytest.mark.parametrize('scope,content_rule', [('task', False), ('sprint', False), ('task', True)])
def test_native_reuse_delivers_local_files_and_accepts(project, scope, content_rule):
    h, source = artifact_family(project, scope=scope, content_rule=content_rule)
    before_parent = deepcopy(h.task_queries.record('P'))
    source_records = h.store.artifact_records('P')
    c = WorkTools(h)
    verified = reuse(c)
    assert verified['status'] == 'reuse_verified'
    records = h.store.artifact_records('D')
    assert len(records) == 1
    local = records[0]
    assert local['owner'] == ('D' if scope == 'task' else 'S')
    assert local['scope'] == scope
    assert local['id'] != source_records[0]['id']
    assert local['path'] != source_records[0]['path']
    assert Path(local['path']).read_bytes() == b'accepted family result\n'
    assert local['digest'] == hashlib.sha256(b'accepted family result\n').hexdigest()
    assert verified['artifacts'] == [{'id': local['id'], 'path': local['path']}]
    assert c.invoke(request('accept', {}))['status'] == 'completed'
    assert h.task_queries.record('P') == before_parent
    assert h.store.artifact_records('P') == source_records
    assert Path(source_records[0]['path']).read_bytes() == b'accepted family result\n'


@pytest.mark.parametrize('content_rule', [False, True])
def test_optional_empty_artifact_contract_can_reuse(project, content_rule):
    h, _ = artifact_family(project, content_rule=content_rule, optional=True)
    c = WorkTools(h)
    verified = reuse(c)
    assert verified['status'] == 'reuse_verified'
    assert verified['artifacts'] == []
    assert h.store.artifact_records('D') == []
    assert c.invoke(request('accept', {}))['status'] == 'completed'


def target_path(h):
    return h.state / 'sprints' / 'S' / 'task' / 'D' / 'artifacts' / 'result.txt'


@pytest.mark.parametrize('fault', ['missing', 'changed', 'symlink', 'foreign_owner', 'unregistered'])
def test_source_failure_has_no_claim_execution_or_delivery(project, fault):
    h, source = artifact_family(project)
    record = h.store.artifact_records('P')[0]
    path = Path(record['path'])
    if fault == 'missing':
        path.unlink()
    elif fault == 'changed':
        path.write_bytes(b'not the accepted bytes')
    elif fault == 'symlink':
        other = path.with_name('unregistered.txt')
        path.rename(other)
        path.symlink_to(other)
    elif fault == 'foreign_owner':
        with h.store.transaction() as db:
            db.execute('UPDATE artifacts SET owner=? WHERE id=?', ('foreign', record['id']))
    else:
        with h.store.transaction() as db:
            db.execute('DELETE FROM task_artifacts WHERE task_id=?', ('P',))
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='(?i)artifact|registered'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D') == before
    assert h.store.artifact_records('D') == []
    assert not target_path(h).exists()
    assert h.current_task() is None
    with h.store.unit_of_work() as uow:
        assert not uow.execution.exists('D')


@pytest.mark.parametrize('fault', ['changed', 'symlink', 'parent_symlink'])
def test_destination_conflict_preserves_existing_files(project, fault):
    h, _ = artifact_family(project)
    target = target_path(h)
    target.parent.mkdir(parents=True)
    foreign = h.state / 'foreign-file'
    foreign.write_bytes(b'preserve this content')
    if fault == 'changed':
        target.write_bytes(b'preserve this content')
    elif fault == 'symlink':
        target.symlink_to(foreign)
    else:
        target.parent.rmdir()
        elsewhere = h.state / 'foreign-directory'
        elsewhere.mkdir()
        target.parent.symlink_to(elsewhere, target_is_directory=True)
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='(?i)artifact|symlink'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D') == before
    assert foreign.read_bytes() == b'preserve this content'
    assert h.store.artifact_records('D') == []
    if fault == 'changed':
        assert target.read_bytes() == b'preserve this content'


def test_binary_delivery_and_identical_retry_preserve_own_file(project):
    h, _ = artifact_family(project, binary=True)
    c = WorkTools(h)
    first = reuse(c)
    path = target_path(h)
    source = Path(h.store.artifact_records('P')[0]['path'])
    assert path.read_bytes() == b'\x00\xffaccepted\x80'
    assert path.stat().st_ino != source.stat().st_ino
    inode = path.stat().st_ino
    replay = c.invoke(request('reuse', first['request']))
    assert replay['checks'] == first['checks'] and replay['artifacts'] == first['artifacts']
    assert replay['replayed'] is True and path.stat().st_ino == inode
    assert c.invoke(request('accept', {}))['status'] == 'completed'


@pytest.mark.parametrize('fault', ['missing', 'changed', 'symlink'])
def test_accept_rejects_changed_delivered_files(project, fault):
    h, _ = artifact_family(project)
    c = WorkTools(h)
    first = reuse(c)
    target = target_path(h)
    if fault == 'missing':
        target.unlink()
    elif fault == 'changed':
        target.write_bytes(b'changed local artifact')
    else:
        other = target.with_name('another.txt')
        target.rename(other)
        target.symlink_to(other)
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='(?i)artifact|symlink|артефакт'):
        c.invoke(request('accept', {}))
    assert h.task_queries.record('D') == before
    assert h.task_queries.record('D')['status'] == 'verified'
    assert h.sprint_tools.overview('S')['status'] != 'completed'


def test_partial_delivery_retries_identical_candidate_without_false_registration(project, monkeypatch):
    import os
    h, _ = artifact_family(project, extra=True)
    c = WorkTools(h)
    version = h.task_queries.record('D')['version']
    packet = request('reuse', {'task_id': 'D', 'source_task_id': 'P',
        'expected_version': version, 'request_id': 'partial-delivery'})
    original = os.link
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise OSError('injected copy failure')
        return original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'link', fail_second)
        with pytest.raises(PoiseError, match='injected copy failure'):
            c.invoke(packet)
    assert h.store.artifact_records('D') == []
    assert h.task_queries.record('D')['status'] == 'active'
    delivered = list(target_path(h).parent.glob('*.txt'))
    assert len(delivered) == 1
    inode = delivered[0].stat().st_ino
    finished = c.invoke(packet)
    assert finished['status'] == 'reuse_verified'
    assert len(h.store.artifact_records('D')) == 2
    assert delivered[0].stat().st_ino == inode
    assert c.invoke(request('accept', {}))['status'] == 'completed'


def test_registration_rollback_preserves_files_and_reuses_own_checks(project, monkeypatch):
    import sqlite3
    h, _ = artifact_family(project)
    c = WorkTools(h)
    packet = request('reuse', {'task_id': 'D', 'source_task_id': 'P',
        'expected_version': h.task_queries.record('D')['version'], 'request_id': 'failed-register'})
    executed = []
    run = h.check_runner.run
    def counted(*args, **kwargs):
        executed.append(args[0])
        return run(*args, **kwargs)
    monkeypatch.setattr(h.check_runner, 'run', counted)
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER reject_local BEFORE INSERT ON task_artifacts "
                   "WHEN NEW.task_id='D' BEGIN SELECT RAISE(ABORT,'injected link failure'); END")
    with pytest.raises((PoiseError, sqlite3.IntegrityError), match='injected link failure'):
        c.invoke(packet)
    assert target_path(h).read_bytes() == b'accepted family result\n'
    assert h.store.artifact_records('D') == []
    assert h.task_queries.record('D')['status'] == 'active'
    with h.store.transaction() as db:
        db.execute('DROP TRIGGER reject_local')
    assert c.invoke(packet)['status'] == 'reuse_verified'
    assert len(executed) == 1
    assert len(h.store.artifact_records('D')) == 1
    assert c.invoke(request('accept', {}))['status'] == 'completed'


def test_artifact_reuse_handoff_keeps_local_delivery_and_explicit_accept(project):
    h, _ = artifact_family(project)
    c = WorkTools(h)
    first = reuse(c)
    c.invoke(request('handoff', {'request_id': 'artifact-handoff', 'reason': 'Continue local acceptance',
        'result': None, 'commit_message': None, 'artifact_paths': []}))
    other = WorkPoise(project['config_path'], 'receiver')
    resumed = WorkTools(other).invoke(request('reuse', first['request']))
    assert resumed['artifacts'] == first['artifacts'] and resumed['checks'] == first['checks']
    assert WorkTools(other).invoke(request('accept', {}))['status'] == 'completed'
    assert target_path(h).read_bytes() == b'accepted family result\n'


def test_completed_artifact_replay_does_not_restore_cleaned_workspace(project):
    h, _ = artifact_family(project)
    c = WorkTools(h)
    first = reuse(c)
    c.invoke(request('accept', {}))
    git(project['app'], 'worktree', 'remove', first['worktree'])
    target_path(h).unlink()
    before = deepcopy(h.task_queries.record('D'))
    replay = c.invoke(request('reuse', first['request']))
    assert replay['status'] == 'completed' and replay['replayed'] is True
    assert replay['artifacts'] == first['artifacts']
    assert not target_path(h).exists()
    assert h.task_queries.record('D') == before


def test_child_result_can_deliver_again_to_sister_in_another_sprint(project):
    from sprints.helpers import changes
    h, _ = artifact_family(project)
    c = WorkTools(h)
    first = reuse(c)
    c.invoke(request('accept', {}))
    created = c.invoke(request('sprint', {'action': 'draft', 'sprint_id': 'NEXT',
        'request_id': 'next-sprint', 'expected_revision': None,
        'template': {'id': 'basic', 'version': '1'}, 'changes': changes([])}))
    duplicate(c, tid='E', parent='D', sprint='NEXT')
    h.bootstrap({'id': 'E'})
    born = h.task_queries.record('E')
    h.task_action({'action': 'ready', 'task_id': 'E', 'expected_revision': born['revision'],
                   'request_id': 'ready-E'})
    c.invoke(request('sprint', {'action': 'publish', 'sprint_id': 'NEXT', 'request_id': 'publish-next',
        'expected_revision': h.sprint_tools.overview('NEXT')['revision']}))
    output = c.invoke(request('reuse', {'task_id': 'E', 'source_task_id': 'D', 'request_id': 'reuse-E',
        'expected_version': h.task_queries.record('E')['version']}))
    assert output['status'] == 'reuse_verified'
    own = h.store.artifact_records('E')
    assert len(own) == 1 and own[0]['owner'] == 'E'
    assert own[0]['id'] != h.store.artifact_records('D')[0]['id']
    assert Path(own[0]['path']).read_bytes() == b'accepted family result\n'
    assert c.invoke(request('accept', {}))['status'] == 'completed'


def test_local_check_cannot_change_delivered_artifact(project, monkeypatch):
    h, _ = artifact_family(project)
    original = h._execute_checks
    def check_and_mutate(*args, **kwargs):
        receipts = original(*args, **kwargs)
        target_path(h).write_bytes(b'changed by the local check')
        return receipts
    monkeypatch.setattr(h, '_execute_checks', check_and_mutate)
    with pytest.raises(PoiseError, match='(?i)artifact|digest'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D')['status'] != 'verified'
    assert h.store.artifact_records('D') == []


def test_registered_but_unaccepted_file_is_not_delivered(project):
    from poise.artifacts import inspect_paths
    h, _ = artifact_family(project)
    source = Path(h.store.artifact_records('P')[0]['path'])
    extra = source.with_name('not-in-accepted-report.txt')
    extra.write_text('not a member of the accepted result')
    roots = {'task': source.parent.parent}
    records = inspect_paths([str(extra)], roots, {'task': 'P'})
    with h.store.unit_of_work() as uow:
        uow.artifacts.link_task('P', records)
    verified = reuse(WorkTools(h))
    assert len(verified['artifacts']) == 1
    assert not target_path(h).with_name(extra.name).exists()
    assert len(h.store.artifact_records('D')) == 1


def test_failed_artifact_reuse_restart_repairs_code_in_same_branch(project):
    from runtime_services.test_task_restart import restart
    from runtime_services.test_restart_local_repair import ready
    h, source = artifact_family(project, command='from src.double import double; assert double(3) == 6')
    parent = deepcopy(h.task_queries.record('P'))
    (project['app'] / 'src/double.py').write_text('def double(n):\n    return n + 1\n')
    git(project['app'], 'add', 'src/double.py')
    git(project['app'], 'commit', '-m', 'Branch-specific regression after imported result')
    c = WorkTools(h)
    failed = reuse(c)
    assert failed['status'] == 'checks_failed'
    assert failed['recovery']['action'] == 'restart'
    assert target_path(h).read_bytes() == b'accepted family result\n'
    before = h.task_queries.record('D')
    worktree = Path(before['worktree'])
    ready(h, c, restart(c, 'D', before['version']))
    context = h.bootstrap({'id': 'D'})
    assert context['status'] == 'active'
    assert context['worktree'] == str(worktree)
    assert target_path(h).read_bytes() == b'accepted family result\n'
    (worktree / 'src/double.py').write_text('def double(n):\n    return 2 * n\n')
    payload = result(context)
    payload['artifact_paths'] = [str(target_path(h))]
    checked = c.invoke(request('verify', {'result': payload, 'artifacts': []}))
    assert checked['status'] == 'verified'
    assert checked['checks'][0]['id'] != failed['checks'][0]['id']
    assert c.invoke(request('accept', {}))['status'] == 'completed'
    assert h.sprint_tools.overview('S')['status'] == 'completed'
    assert h.task_queries.record('P') == parent
    assert h.store.artifact_records('D')[0]['owner'] == 'D'
    git(worktree, 'merge-base', '--is-ancestor', source['commit'], 'HEAD')


def test_missing_source_artifact_can_be_recreated_after_normal_restart(project):
    from runtime_services.test_task_restart import restart
    from runtime_services.test_restart_local_repair import ready
    h, source = artifact_family(project)
    source_path = Path(h.store.artifact_records('P')[0]['path'])
    source_path.unlink()  # Test-only simulation of a missing external deliverable.
    c = WorkTools(h)
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='Artifact copy preflight failed'):
        reuse(c)
    assert h.task_queries.record('D') == before
    ready(h, c, restart(c, 'D', before['version']))
    context = h.bootstrap({'id': 'D'})
    assert context['status'] == 'active'
    checked = c.invoke(request('verify', {'result': result(context), 'artifacts': [
        text_artifact('task', 'result.txt', 'local replacement result\n')]}))
    assert checked['status'] == 'verified'
    assert c.invoke(request('accept', {}))['status'] == 'completed'
    assert target_path(h).read_bytes() == b'local replacement result\n'
    assert h.sprint_tools.overview('S')['status'] == 'completed'
    assert not source_path.exists()
    git(Path(context['worktree']), 'merge-base', '--is-ancestor', source['commit'], 'HEAD')


@pytest.mark.parametrize('operation', ['reuse', 'accept', 'completed_replay'])
def test_saved_candidate_without_delivery_metadata_has_a_recovery_path(project, operation):
    import json
    from runtime_services.test_duplicate_reuse import family_result
    from runtime_services.test_task_restart import restart
    from runtime_services.test_restart_local_repair import ready
    from sprints.helpers import verify
    h, _ = family_result(project)
    c = WorkTools(h)
    first = reuse(c)
    if operation == 'completed_replay':
        c.invoke(request('accept', {}))
    # Model the persisted pre-delivery candidate shape in this test-only database.
    with h.store.transaction() as db:
        row = db.execute('SELECT data FROM task_workflows WHERE task_id=?', ('D',)).fetchone()
        workflow = json.loads(row['data'])
        candidate = workflow['duplicate_reuse']['candidate']
        for key in ('artifact_source', 'artifact_delivery', 'artifact_stage'):
            candidate.pop(key)
        db.execute('UPDATE task_workflows SET data=? WHERE task_id=?', (json.dumps(workflow), 'D'))
    before = deepcopy(h.task_queries.record('D'))
    if operation == 'completed_replay':
        out = c.invoke(request('reuse', first['request']))
        assert out['status'] == 'completed' and out['replayed'] is True
        assert h.task_queries.record('D') == before
        return
    packet = request('reuse', first['request']) if operation == 'reuse' else request('accept', {})
    with pytest.raises(PoiseError, match='restart'):
        c.invoke(packet)
    assert h.task_queries.record('D') == before
    ready(h, c, restart(c, 'D', before['version']))
    context = h.bootstrap({'id': 'D'})
    assert context['status'] == 'active'
    assert verify(c, context)['status'] == 'verified'
    assert c.invoke(request('accept', {}))['status'] == 'completed'
