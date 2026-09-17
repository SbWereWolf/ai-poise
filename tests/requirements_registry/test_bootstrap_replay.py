"""A saved Task, not the current live registry, owns retry authorization."""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import WorkPoise
from batch.helpers import request
from poise.application.work import WorkTools
from poise.common import PoiseError
from sprints.helpers import setup, task


def started(project):
    setup(project)
    runtime = WorkPoise(project['config_path'], 'replay-owner')
    contract = task(project, 'REPLAY')
    contract['sprint_id'] = None
    tools = WorkTools(runtime)
    context = tools.invoke(request('bootstrap', {
        'task': contract, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    return runtime, tools, contract, context


def preserved(runtime):
    with runtime.store.unit_of_work() as uow:
        return (uow.tasks.restart_context('REPLAY'), uow.execution.load('REPLAY'),
                uow.tasks.load('REPLAY').state)


def test_full_retry_preserves_wip_and_historical_requirements(project, monkeypatch):
    runtime, tools, contract, context = started(project)
    sentinel = Path(context['worktree']) / 'uncommitted-sentinel.txt'
    sentinel.write_bytes(b'keep this exact WIP\n')
    before = preserved(runtime)

    def no_live_registry():
        raise AssertionError('replay must not observe the live registry')
    monkeypatch.setattr(runtime.task_commands.requirements_gate, '_registry_loader', no_live_registry)
    result = tools.invoke(request('bootstrap', {
        'task': contract, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert result['worktree'] == context['worktree']
    assert sentinel.read_bytes() == b'keep this exact WIP\n'
    assert preserved(runtime) == before


@pytest.mark.parametrize('field', ['goal', 'snapshot', 'agreement', 'missing_snapshot', 'missing_agreement'])
def test_changed_full_retry_rejects_without_task_changes(project, field):
    runtime, tools, contract, context = started(project)
    changed = deepcopy(contract)
    if field == 'goal':
        changed['goal'] = 'A different goal'
    elif field == 'snapshot':
        item = next(iter(changed['requirements_snapshot']['requirements'].values()))
        item['text'] += ' forged'
    elif field == 'agreement':
        changed['requirements_agreement']['accepted'] = False
    else:
        del changed['requirements_' + field.removeprefix('missing_')]
    before = preserved(runtime)
    with pytest.raises(PoiseError):
        tools.invoke(request('bootstrap', {
            'task': changed, 'decision': None, 'feedback': None, 'rework_stage': None,
        }))
    assert preserved(runtime) == before


def test_id_and_saved_canonical_contract_remain_valid_continuations(project):
    runtime, tools, _, context = started(project)
    for contract in ({'id': 'REPLAY'}, runtime.task_queries.record('REPLAY')['contract']):
        result = tools.invoke(request('bootstrap', {
            'task': contract, 'decision': None, 'feedback': None, 'rework_stage': None,
        }))
        assert result['worktree'] == context['worktree']


def test_exact_full_retry_does_not_steal_foreign_task(project):
    runtime, _, contract, context = started(project)
    before = preserved(runtime)
    other = WorkTools(WorkPoise(project['config_path'], 'foreign-owner'))
    with pytest.raises(PoiseError):
        other.invoke(request('bootstrap', {
            'task': contract, 'decision': None, 'feedback': None, 'rework_stage': None,
        }))
    assert preserved(runtime) == before
    assert Path(context['worktree']).is_dir()
