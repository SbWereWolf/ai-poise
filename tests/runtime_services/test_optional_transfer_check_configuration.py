"""Check execution and failed-check rework need no file-transfer feature."""
from copy import deepcopy

import pytest

from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from runtime_services.test_failed_check_rework import (
    _fail_current_stage, _rework, _scenario, _show,
)


def _without_transfer(project, tools):
    project['cfg']['runtime_services'].pop('transfer')
    write_json(project['config_path'], project['cfg'])
    return WorkTools(WorkPoise(project['config_path'], tools.runtime.session))


@pytest.mark.parametrize('transfer', [True, False])
def test_failed_check_executes_without_optional_transfer(project, transfer):
    tools, context = _scenario(project)
    if not transfer:
        tools = _without_transfer(project, tools)
    failed = _fail_current_stage(tools, context)
    current = tools.runtime.current_task()
    assert failed['status'] == 'checks_failed'
    assert failed['checks'][0]['passed'] is False
    assert current['id'] == 'T1'
    assert current['workflow']['stages'][current['stage_index']]['id'] == 'implementation'
    assert current['claimed_by'] == tools.runtime.session
    assert _show(tools)['submission_count'] == 1
    assert _show(tools)['evidence_count'] == 1


@pytest.mark.parametrize('transfer', [True, False])
def test_existing_failed_check_reworks_without_optional_transfer(project, transfer):
    tools, context = _scenario(project, closed_revise_target=True)
    # The batch and its rework must observe the same declared companions.
    # Removing actual companions would correctly invalidate execution identity.
    project['cfg']['runtime_services']['transfer'].pop('recovery')
    write_json(project['config_path'], project['cfg'])
    tools = WorkTools(WorkPoise(project['config_path'], tools.runtime.session))
    _fail_current_stage(tools, context)
    before = _show(tools)
    contract = deepcopy(tools.runtime.current_task()['contract'])
    observations = _show(tools, 'evidence')['observations']
    if not transfer:
        tools = _without_transfer(project, tools)
    recovered = _rework(tools, 'closed_finding_remediation')
    after = _show(tools)
    assert recovered['task'] == 'T1'
    assert recovered['worktree'] == context['worktree']
    assert recovered['stage'] == 'closed_finding_remediation'
    assert recovered['iteration'] == 1
    assert after['submission_count'] == before['submission_count'] == 1
    assert after['evidence_count'] == before['evidence_count'] == 1
    assert _show(tools, 'evidence')['observations'] == observations
    assert tools.runtime.current_task()['contract'] == contract
    assert tools.runtime.current_task()['pending'] is None
    assert tools.runtime.current_task()['claimed_by'] == tools.runtime.session
