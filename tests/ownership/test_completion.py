from copy import deepcopy
import json

import pytest

from batch.helpers import request
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools


@pytest.mark.parametrize('worktree_required', [False, True], ids=['task_only', 'worktree'])
@pytest.mark.parametrize('operation', ['accept', 'continue'])
def test_terminal_completion_preserves_result_and_measures_sections(project, operation, worktree_required):
    process = deepcopy(project['process'])
    stage = process['stages'][0]
    stage['transitions'] = {'complete': None}
    process['stages'] = [stage]
    process['worktree_required'] = worktree_required
    process['benefit']['sections'] = ['report']
    write_json(project['root'] / 'config/processes/development.json', process)
    cfg = deepcopy(project['cfg'])
    cfg['automatic_checks'] = []
    write_json(project['config_path'], cfg)
    task = deepcopy(project['task'])
    task.update(methods=[], method_inputs=[], checks={stage['id']: []},
                evidence_plan={stage['id']: {'subject_methods': {}, 'arguments': [], 'review_arguments': []}})
    task['decomposition']['phases'] = [
        phase for phase in task['decomposition']['phases'] if phase['stage'] == stage['id']]
    task['stage_contracts'] = [
        contract for contract in task['stage_contracts'] if contract['stage_id'] == stage['id']]
    runtime = WorkPoise(project['config_path'], 'completer')
    tools = WorkTools(runtime)
    before = git(project['app'], 'rev-parse', 'HEAD')
    context = tools.invoke(request('bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None}))
    result = deepcopy(context['result_template'])
    report = 'Completed section content.'
    result['sections']['report'] = report
    if not worktree_required:
        # An explicit independent binding is not owned by this Task's release.
        runtime.ownership.acquire_worktree(task['id'])
        # Arrange a valid stored tree identity: task-only bootstrap currently stores
        # a commit in entry_tree. That separate defect is not the release subject.
        with runtime.store.transaction() as db:
            data = json.loads(db.execute(
                'SELECT data FROM task_execution WHERE task_id=?',
                (task['id'],)).fetchone()[0])
            data['entry_tree'] = git(project['app'], 'rev-parse', 'HEAD^{tree}')
            db.execute('UPDATE task_execution SET data=? WHERE task_id=?',
                       (json.dumps(data), task['id']))
    verified = tools.invoke(request('verify', {'result': result, 'artifacts': []}))
    assert verified['status'] == 'verified'
    packet = (request('accept', {}) if operation == 'accept' else
              request('bootstrap', {'task': None, 'decision': 'continue', 'feedback': None, 'rework_stage': None}))
    completed = tools.invoke(packet)
    assert completed['status'] == 'completed'
    assert completed['commit'] == verified['commit'] == before
    assert runtime.current_task() is None
    with runtime.store.transaction() as db:
        assert db.execute('SELECT claimed_by FROM tasks WHERE id=?',
                          (task['id'],)).fetchone()[0] is None
        binding = db.execute('SELECT task_id FROM sessions WHERE id=?',
                             (runtime.session,)).fetchone()[0]
    assert binding == (None if worktree_required else task['id'])
    metrics = runtime.accounting.report({
        'scope': {'kind': 'task', 'id': task['id']},
        'group_by': [], 'from': None, 'to': None})
    assert metrics['totals']['outcomes']['completed'] == 1
    benefit = metrics['totals']['benefit']
    assert benefit['coverage'] == 'complete'
    assert benefit['changed_bytes'] == len(report.encode())
    assert set(benefit['categories']) == {'section:report'}
    assert git(project['app'], 'rev-parse', 'HEAD') == before
    assert git(project['app'], 'status', '--porcelain') == ''


@pytest.mark.parametrize('operation', ['accept', 'cancel'])
def test_terminal_release_and_execution_write_roll_back_together(project, monkeypatch, operation):
    from poise.infrastructure.sqlite.tasks import SqliteExecutionRepository
    from result_integration.helpers import prepare_completed_task, source_change

    tools, worktree, commit = prepare_completed_task(project, source_change, accept=False)
    runtime = tools.runtime
    with runtime.store.transaction() as db:
        before_task = tuple(db.execute(
            "SELECT status,version,claimed_by FROM tasks WHERE id='T1'").fetchone())
        before_binding = db.execute(
            "SELECT task_id FROM sessions WHERE id=?", (runtime.session,)).fetchone()[0]

    def fail_save(*args, **kwargs):
        raise RuntimeError('injected execution write failure')

    monkeypatch.setattr(SqliteExecutionRepository, 'save', fail_save)
    packet = request(operation, {} if operation == 'accept' else {'reason': 'Cancel this test Task.'})
    with pytest.raises(RuntimeError, match='injected execution write failure'):
        tools.invoke(packet)
    with runtime.store.transaction() as db:
        assert tuple(db.execute(
            "SELECT status,version,claimed_by FROM tasks WHERE id='T1'").fetchone()) == before_task
        assert db.execute("SELECT task_id FROM sessions WHERE id=?",
                          (runtime.session,)).fetchone()[0] == before_binding == 'T1'
    assert worktree.is_dir()
    assert git(worktree, 'rev-parse', 'HEAD') == commit


def test_cancellation_releases_dependent_binding_without_removing_source(project):
    from result_integration.helpers import prepare_completed_task, source_change

    tools, worktree, commit = prepare_completed_task(project, source_change, accept=False)
    cancelled = tools.invoke(request('cancel', {'reason': 'Cancel this test Task.'}))
    assert cancelled['status'] == 'cancelled'
    with tools.runtime.store.transaction() as db:
        assert tuple(db.execute(
            "SELECT status,claimed_by FROM tasks WHERE id='T1'").fetchone()) == ('cancelled', None)
        assert db.execute("SELECT task_id FROM sessions WHERE id=?",
                          (tools.runtime.session,)).fetchone()[0] is None
    assert worktree.is_dir()
    assert git(worktree, 'rev-parse', 'HEAD') == commit


def test_cancellation_preserves_an_independent_worktree_binding(project):
    from result_integration.helpers import prepare_completed_task, source_change

    tools, worktree, commit = prepare_completed_task(project, source_change, accept=False)
    with tools.runtime.store.transaction() as db:
        metadata = json.loads(db.execute("SELECT metadata FROM tasks WHERE id='T1'").fetchone()[0])
        metadata['process']['worktree_required'] = False
        db.execute("UPDATE tasks SET metadata=? WHERE id='T1'", (json.dumps(metadata),))
    assert tools.invoke(request('cancel', {'reason': 'Cancel this test Task.'}))['status'] == 'cancelled'
    with tools.runtime.store.transaction() as db:
        assert db.execute("SELECT task_id FROM sessions WHERE id=?",
                          (tools.runtime.session,)).fetchone()[0] == 'T1'
    assert worktree.is_dir()
    assert git(worktree, 'rev-parse', 'HEAD') == commit
