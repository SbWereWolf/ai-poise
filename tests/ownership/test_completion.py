from copy import deepcopy

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
    runtime = WorkPoise(project['config_path'], 'completer')
    tools = WorkTools(runtime)
    before = git(project['app'], 'rev-parse', 'HEAD')
    context = tools.invoke(request('bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None}))
    result = deepcopy(context['result_template'])
    report = 'Completed section content.'
    result['sections']['report'] = report
    verified = tools.invoke(request('verify', {'result': result, 'artifacts': []}))
    assert verified['status'] == 'verified'
    packet = (request('accept', {}) if operation == 'accept' else
              request('bootstrap', {'task': None, 'decision': 'continue', 'feedback': None, 'rework_stage': None}))
    completed = tools.invoke(packet)
    assert completed['status'] == 'completed'
    assert completed['commit'] == verified['commit'] == before
    assert runtime.current_task() is None
    credit = runtime.accounting.port.repo.latest_credit(task['id'])
    assert credit['state'] == 'completed'
    assert credit['measurement']['coverage'] == 'complete'
    assert credit['measurement']['changed_bytes'] == len(report.encode())
    assert set(credit['measurement']['categories']) == {'section:report'}
    assert git(project['app'], 'rev-parse', 'HEAD') == before
    assert git(project['app'], 'status', '--porcelain') == ''
