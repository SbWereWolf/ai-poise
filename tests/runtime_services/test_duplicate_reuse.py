"""Completed-family reuse through the real public work API and test-owned Git/SQLite."""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from batch.helpers import request
from tasks.test_duplicate_creation import prepared, duplicate
from sprints.helpers import publish, verify, task, draft


def family_result(project, *, integrate=True, blocked=False, source_id="P", start_target=False, command='print("checked")'):
    planner, client = prepared(project, command=command)
    duplicate(client)
    planner.bootstrap({'id': 'D'})
    record = planner.task_queries.record('D')
    planner.task_action({'action': 'ready', 'task_id': 'D',
        'expected_revision': record['revision'], 'request_id': 'ready-D'})
    if blocked:
        draft(client, [task(project, 'U')], revision=planner.sprint_tools.overview('S')['revision'],
            request_id='add-prerequisite', updates=[
                {'kind': 'upsert_tasks', 'tasks': [task(project, 'U')]},
                {'kind': 'dependencies', 'items': [
                    {'predecessor': 'U', 'successor': 'D', 'kind': 'completion'}]}])
    publish(client, planner.sprint_tools.overview('S')['revision'])
    if start_target:
        WorkPoise(project['config_path'], 'consumer').bootstrap({'id': 'D'})
    parent = WorkPoise(project['config_path'], 'producer')
    ctx = parent.bootstrap({'id': source_id}, force_duplicate_start=start_target)
    (Path(ctx['worktree']) / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    report = verify(WorkTools(parent), ctx)
    assert report['status'] == 'verified'
    assert WorkTools(parent).invoke(request('accept', {}))['status'] == 'completed'
    if integrate:
        git(project['app'], 'merge', '--ff-only', report['commit'])
    return WorkPoise(project['config_path'], 'consumer'), report


def reuse(client, *, source='P', request_id='reuse-D'):
    h = client.runtime
    record = h.task_queries.record('D')
    return client.invoke(request('reuse', {'task_id': 'D', 'source_task_id': source,
        'expected_version': record['version'], 'request_id': request_id}))


def test_reuse_verifies_locally_then_explicit_accept_completes_without_stages(project):
    h, source = family_result(project)
    c = WorkTools(h)
    before = deepcopy(h.task_queries.record('D'))
    parent = deepcopy(h.task_queries.record('P'))
    result = reuse(c)
    assert result['status'] == 'reuse_verified'
    assert result['source_task_id'] == 'P'
    assert result['source_commit'] == source['commit']
    assert [r['method'] for r in result['checks']] == ['CHECK']
    assert all(r['passed'] for r in result['checks'])
    assert result['checks'][0]['id'] != source['checks'][0]['id']
    assert h.task_queries.record('D')['status'] == 'verified'
    assert h.task_queries.record('D')['stage_index'] == before['stage_index']
    assert h.task_queries.record('D')['iteration'] == before['iteration']
    assert h.sprint_tools.overview('S')['status'] != 'completed'
    out = c.invoke(request('accept', {}))
    assert out['status'] == 'completed' and out['completion_kind'] == 'duplicate_reuse'
    after = h.task_queries.record('D')
    assert after['result_commit'] == source['commit']
    assert after['claimed_by'] is None
    assert h.sprint_tools.overview('S')['status'] == 'completed'
    assert h.task_queries.record('P') == parent
    with h.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM submissions WHERE task_id='D'").fetchone()[0] == 0
        events = [r[0] for r in db.execute("SELECT json_extract(data,'$.event') FROM task_events WHERE task_id='D'")]
        assert 'duplicate_reuse_verified' in events
        assert 'duplicate_reuse_accepted' in events
        assert 'stage_progressed' not in events


def test_unmerged_source_refuses_before_claim_or_workspace(project):
    h, _ = family_result(project, integrate=False)
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='integrat'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D') == before
    assert h.current_task() is None


def test_unrelated_or_unfinished_source_refuses(project):
    h, _ = family_result(project)
    with pytest.raises(PoiseError, match='family|source'):
        reuse(WorkTools(h), source='D')
    assert h.task_queries.record('D')['status'] == 'available'


def test_accept_rechecks_local_head_and_keeps_unfinished_state(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    verified = reuse(c)
    p = Path(verified['worktree'])
    (p / 'src/double.py').write_text('def double(n):\n    return 0\n')
    with pytest.raises(PoiseError, match='changed|clean'):
        c.invoke(request('accept', {}))
    assert h.task_queries.record('D')['status'] == 'verified'
    assert (p / 'src/double.py').read_text() == 'def double(n):\n    return 0\n'


def test_accept_rechecks_receipt_bytes(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    verified = reuse(c)
    Path(verified['checks'][0]['stdout']).write_text('tampered')
    with pytest.raises(PoiseError, match='receipt'):
        c.invoke(request('accept', {}))
    assert h.task_queries.record('D')['status'] == 'verified'


def test_source_membership_and_contract_not_inferred_from_name(project):
    h, _ = family_result(project)
    with h.store.transaction() as db:
        db.execute("UPDATE tasks SET metadata=json_set(metadata,'$.contract.requirements',json('[\"different\"]')) WHERE id='D'")
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='contract'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D') == before


def test_stale_version_is_rejected_without_side_effects(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    d = h.task_queries.record('D')
    with pytest.raises(PoiseError, match='version'):
        c.invoke(request('reuse', {'task_id': 'D', 'source_task_id': 'P',
            'expected_version': d['version'] + 1, 'request_id': 'stale'}))
    assert h.task_queries.record('D') == d


def test_ordinary_force_cannot_start_reuse_task_stage(project):
    h, _ = family_result(project)
    reuse(WorkTools(h))
    before = deepcopy(h.task_queries.record('D'))
    out = h.bootstrap(force_duplicate_start=True)
    assert out['status'] == 'duplicate_start_blocked'
    assert h.task_queries.record('D') == before


def test_reuse_replay_returns_saved_checks_without_reexecuting(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    d = h.task_queries.record('D')
    packet = request('reuse', {'task_id': 'D', 'source_task_id': 'P',
        'expected_version': d['version'], 'request_id': 'same'})
    first = c.invoke(packet)
    second = c.invoke(packet)
    assert first['checks'] == second['checks']
    assert second['replayed'] is True
    assert h.task_queries.record('D')['status'] == 'verified'


def test_reuse_does_not_bypass_incomplete_local_sprint_prerequisite(project):
    h, _ = family_result(project, blocked=True)
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='prerequisite'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D') == before
    assert h.current_task() is None


def test_parent_can_reuse_its_completed_child(project):
    h, source = family_result(project, source_id='D')
    before = h.task_queries.record('P')
    c = WorkTools(h)
    verified = c.invoke(request('reuse', {'task_id': 'P', 'source_task_id': 'D',
        'request_id': 'reuse-parent', 'expected_version': before['version']}))
    assert verified['source_commit'] == source['commit']
    assert verified['status'] == 'reuse_verified'
    accepted = c.invoke(request('accept', {}))
    assert accepted['status'] == 'completed'
    assert h.task_queries.record('P')['duplicate']['role'] == 'parent'


def test_completed_reuse_exact_replay_does_not_reacquire_task(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    d = h.task_queries.record('D')
    packet = request('reuse', {'task_id': 'D', 'source_task_id': 'P',
        'request_id': 'completed-replay', 'expected_version': d['version']})
    first = c.invoke(packet)
    c.invoke(request('accept', {}))
    before = deepcopy(h.task_queries.record('D'))
    result = c.invoke(packet)
    assert result['status'] == 'completed' and result['replayed'] is True
    assert result['checks'] == first['checks']
    assert h.task_queries.record('D') == before
    assert h.current_task() is None


def test_old_task_branch_needs_explicit_update_not_reimplementation(project):
    h, source = family_result(project, start_target=True)
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='absent from local branch'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D') == before
    git(Path(before['worktree']), 'merge', '--ff-only', source['commit'])
    result = reuse(WorkTools(h))
    assert result['status'] == 'reuse_verified'
    assert result['head'] == source['commit']


def test_failed_local_check_never_inherits_source_success_or_repeats_effect(project, monkeypatch):
    h, _ = family_result(project, command='from src.double import double; assert double(3) == 6')
    (project['app'] / 'src/double.py').write_text('def double(n):\n    return n + 1\n')
    git(project['app'], 'add', 'src/double.py')
    git(project['app'], 'commit', '-m', 'Test-only later regression')
    c = WorkTools(h)
    d = h.task_queries.record('D')
    packet = request('reuse', {'task_id': 'D', 'source_task_id': 'P',
        'expected_version': d['version'], 'request_id': 'failing-local'})
    calls = []
    run = h.check_runner.run
    def counted(*args, **kwargs):
        calls.append(args[0])
        return run(*args, **kwargs)
    monkeypatch.setattr(h.check_runner, 'run', counted)
    first = c.invoke(packet)
    assert first['status'] == 'checks_failed'
    assert first['checks'][0]['actual_exit_code'] != 0
    with pytest.raises(PoiseError, match='locally verified'):
        c.invoke(request('accept', {}))
    second = c.invoke(packet)
    assert second['checks'] == first['checks']
    assert len(calls) == 1
    assert h.task_queries.record('D')['status'] == 'active'
    assert h.sprint_tools.overview('S')['status'] != 'completed'


@pytest.mark.parametrize('boundary,can_resume', [('record_observations', True), ('record_receipt', False)])
def test_reuse_preserves_unknown_check_outcome_without_repeating_commands(project, monkeypatch, boundary, can_resume):
    from runtime_services.test_durable_check_attempts import fail_once
    h, _ = family_result(project)
    c = WorkTools(h)
    d = h.task_queries.record('D')
    packet = request('reuse', {'task_id': 'D', 'source_task_id': 'P',
        'expected_version': d['version'], 'request_id': 'interrupted'})
    calls = []
    run = h.check_runner.run
    def counted(*args, **kwargs):
        calls.append(args[0])
        return run(*args, **kwargs)
    monkeypatch.setattr(h.check_runner, 'run', counted)
    fail_once(monkeypatch, h.runner if can_resume else h.evidence_commands, boundary)
    with pytest.raises(OSError, match='injected persistence'):
        c.invoke(packet)
    before = deepcopy(h.task_queries.record('D'))
    if can_resume:
        assert c.invoke(packet)['status'] == 'reuse_verified'
    else:
        with pytest.raises(PoiseError, match='Unknown check outcome'):
            c.invoke(packet)
        assert h.task_queries.record('D') == before
    assert len(calls) == 1


def test_reuse_refuses_another_session_without_mutations(project):
    h, source = family_result(project, start_target=True)
    git(Path(h.task_queries.record('D')['worktree']), 'merge', '--ff-only', source['commit'])
    before = deepcopy(h.task_queries.record('D'))
    other = WorkPoise(project['config_path'], 'stranger')
    with pytest.raises(PoiseError, match='Задача связана с другой сессией'):
        reuse(WorkTools(other))
    assert h.task_queries.record('D') == before


def test_verified_reuse_can_be_resumed_by_new_owner_with_same_request(project):
    h, _ = family_result(project)
    first = reuse(WorkTools(h))
    h.ownership.release_task('D')
    resumed = WorkPoise(project['config_path'], 'continuing')
    result = WorkTools(resumed).invoke(request('reuse', first['request']))
    assert result['replayed'] is True
    assert result['checks'] == first['checks']
    assert resumed.task_queries.record('D')['claimed_by'] == 'continuing'
    assert WorkTools(resumed).invoke(request('accept', {}))['status'] == 'completed'


def test_reuse_rejects_git_operation_in_progress_before_work(project):
    h, source = family_result(project, start_target=True)
    path = Path(h.task_queries.record('D')['worktree'])
    git(path, 'merge', '--ff-only', source['commit'])
    marker = Path(git(path, 'rev-parse', '--git-path', 'MERGE_HEAD'))
    if not marker.is_absolute():
        marker = path / marker
    marker.write_text(source['commit'] + '\n')
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='unfinished Git operation'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D') == before
    assert marker.exists()


@pytest.mark.parametrize('tamper_bundle', [False, True])
def test_verified_reuse_uses_native_handoff_owner_and_preserved_bundle(project, tamper_bundle):
    h, _ = family_result(project)
    c = WorkTools(h)
    first = reuse(c)
    handoff_id = 'reuse-handoff'
    receipt = c.invoke(request('handoff', {
        'request_id': handoff_id, 'reason': 'Continue reuse verification in another session',
        'result': None, 'commit_message': None, 'artifact_paths': [],
    }))
    assert h.handoff_tools.commands.lookup('consumer', handoff_id)['state'] == 'released'
    next_h = WorkPoise(project['config_path'], 'receiver')
    next_c = WorkTools(next_h)
    before = deepcopy(next_h.task_queries.record('D'))
    if tamper_bundle:
        Path(receipt['bundle_path']).write_bytes(b'corrupted transfer')
        with pytest.raises(PoiseError, match='bundle missing or changed'):
            next_c.invoke(request('reuse', first['request']))
        assert next_h.task_queries.record('D') == before
        assert next_h.handoff_tools.commands.lookup('consumer', handoff_id)['state'] == 'released'
    else:
        resumed = next_c.invoke(request('reuse', first['request']))
        assert resumed['status'] == 'reuse_verified'
        assert resumed['checks'] == first['checks']
        assert next_h.handoff_tools.commands.lookup('consumer', handoff_id)['state'] == 'resumed'
        assert next_c.invoke(request('accept', {}))['status'] == 'completed'


def test_completed_reuse_replay_survives_authorized_workspace_cleanup(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    first = reuse(c)
    accepted = c.invoke(request('accept', {}))
    git(project['app'], 'worktree', 'remove', first['worktree'])
    before = deepcopy(h.task_queries.record('D'))
    result = c.invoke(request('reuse', first['request']))
    assert result['status'] == 'completed' and result['replayed'] is True
    assert result['checks'] == accepted['checks']
    assert h.task_queries.record('D') == before
    assert h.current_task() is None
    assert not Path(first['worktree']).exists()


def test_accept_refuses_committed_local_changes_after_reuse_verification(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    first = reuse(c)
    path = Path(first['worktree'])
    (path / 'src/double.py').write_text('def double(n):\n    return 0\n')
    git(path, 'add', 'src/double.py')
    git(path, 'commit', '-m', 'Test-only changed HEAD')
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='changed'):
        c.invoke(request('accept', {}))
    assert h.task_queries.record('D') == before


def test_accept_rechecks_source_integration_in_current_base_ref(project):
    h, source = family_result(project)
    c = WorkTools(h)
    reuse(c)
    git(project['app'], 'reset', '--hard', source['commit'] + '^')
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='integrat'):
        c.invoke(request('accept', {}))
    assert h.task_queries.record('D') == before


def test_completed_reuse_rejects_changed_request_instead_of_reclaiming(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    first = reuse(c)
    c.invoke(request('accept', {}))
    before = deepcopy(h.task_queries.record('D'))
    changed = {**first['request'], 'request_id': 'different-terminal-request'}
    with pytest.raises(PoiseError, match='conflicts with the saved candidate'):
        c.invoke(request('reuse', changed))
    assert h.task_queries.record('D') == before
    assert h.current_task() is None


def test_explicit_restart_preserves_lineage_and_clears_prior_reuse_candidate(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    result = reuse(c)
    before = deepcopy(h.task_queries.record('D'))
    root_record = deepcopy(h.task_queries.record('P'))
    restarted = c.invoke(request('task', {
        'action': 'restart', 'task_id': 'D', 'expected_version': before['version'],
        'request_id': 'restart-reuse', 'reason': 'Discard the local reuse candidate for a new verification.',
        'authorization': 'Test operator explicitly authorized restarting D.',
    }))
    assert restarted['status'] == 'newborn'
    newborn = h.task_queries.record('D')
    h.task_action({'action': 'ready', 'task_id': 'D', 'expected_revision': newborn['revision'],
                  'request_id': 'ready-after-reuse-restart'})
    ready = h.task_queries.record('D')
    assert ready.get('duplicate_reuse') is None
    assert ready['duplicate']['parent_id'] == 'P'
    assert ready['worktree'] == result['worktree'] and Path(ready['worktree']).exists()
    assert h.task_queries.record('P') == root_record
    repeated = reuse(c, request_id='new-reuse-after-restart')
    assert repeated['status'] == 'reuse_verified'
    assert repeated['checks'][0]['id'] != result['checks'][0]['id']


def test_completed_reuse_receipt_replay_survives_later_sprint_cancellation(project):
    h, _ = family_result(project)
    c = WorkTools(h)
    first = reuse(c)
    c.invoke(request('accept', {}))
    c.invoke(request('sprint', {'action': 'cancel', 'sprint_id': 'S',
        'request_id': 'cancel-after-completion', 'reason': 'Test operator closes remaining planning scope.'}))
    before = deepcopy(h.task_queries.record('D'))
    result = c.invoke(request('reuse', first['request']))
    assert result['status'] == 'completed' and result['replayed'] is True
    assert h.task_queries.record('D') == before
    assert h.current_task() is None


def test_missing_required_artifact_refuses_before_claim_or_execution(project):
    import json
    h, _ = family_result(project)
    # Test-owned stored-contract variant: even a purported accepted source does not
    # authorize the reuse path to invent delivery into the target Task's roots.
    with h.store.transaction() as db:
        rows = db.execute("SELECT id,metadata FROM tasks WHERE id IN ('P','D')").fetchall()
        for row in rows:
            metadata = json.loads(row['metadata'])
            metadata['contract']['artifact_requirements'] = [
                {'scope': 'task', 'pattern': '*.txt', 'minimum': 1, 'maximum': 1}]
            db.execute('UPDATE tasks SET metadata=? WHERE id=?',
                       (json.dumps(metadata), row['id']))
    before = deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError, match='количество'):
        reuse(WorkTools(h))
    assert h.task_queries.record('D') == before
    assert h.current_task() is None
    with h.store.unit_of_work() as uow:
        assert not uow.execution.exists('D')
