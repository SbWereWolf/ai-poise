"""Prospective cross-contract validation cannot leave an unloadable Task."""
from copy import deepcopy
import sqlite3
from pathlib import Path

import pytest

from conftest import write_json
from poise.common import PoiseError
from tests.verification.test_current_registry_mutation import (
    configure_public_registry_case, public_tools, change, operation, method,
)
from .helpers import bootstrap, request, result, text_artifact
from .test_atomic_verify_delivery import prepared


def database_snapshot(runtime):
    """Explicit columns capture all durable tables, not just the Task row."""
    with runtime.store.transaction() as db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        values = {}
        for table in tables:
            name = '"' + table.replace('"', '""') + '"'
            columns = ','.join('"' + row[1].replace('"', '""') + '"'
                               for row in db.execute(f'PRAGMA table_info({name})'))
            values[table] = sorted((tuple(row) for row in db.execute(
                f'SELECT {columns} FROM {name}')), key=repr)
        return values


def registry_case(project):
    configure_public_registry_case(project)
    # An inactive observe stage is valid until a new guard activates it without
    # a subject. This is the late rehydration failure from the reported incident.
    project['process']['stages'][2].update(handler='observe', read_only=True,
                                            allowed_paths=[])
    project['task']['stage_contracts'][2]['allowed_paths'] = []
    write_json(project['root'] / 'config/processes/development.json', project['process'])
    tools = public_tools(project, 'registry-atomic-owner')
    context = bootstrap(tools, project)
    failed = tools.invoke(request('verify', {'result': result(context), 'artifacts': []}))
    assert failed['status'] == 'checks_failed'
    context = tools.invoke(request('bootstrap', {'task': None, 'decision': 'rework',
        'feedback': 'Repair the failed RED schedule.', 'rework_stage': 'test_implementation'}))
    assert context['iteration'] == 2
    return tools, context


@pytest.mark.parametrize('entry', ['public', 'runtime', 'commands'])
@pytest.mark.parametrize('mutation', ['reschedule', 'add'])
def test_failed_check_rework_registry_rejection_preserves_every_table(project, entry, mutation):
    tools, context = registry_case(project)
    runtime = tools.runtime
    payload = result(context)
    if mutation == 'reschedule':
        selected = operation('reschedule', 'RED', stages=['test_remediation'])
    else:
        guard = method('NEW-GUARD', 'guard', green_stage='test_remediation')
        guard['source_under_test'] = {'kind': 'external', 'reason': 'Observe external input.'}
        guard['verification_plan']['change_surface'] = []
        selected = operation('add', 'NEW-GUARD', registration={
            'method': guard, 'stages': ['test_remediation']})
    payload['method_additions'] = change(selected)
    before = database_snapshot(runtime)
    with pytest.raises(PoiseError, match='active observe'):
        if entry == 'commands':
            runtime.task_commands.submit(context['task'], runtime.session, payload)
        elif entry == 'runtime':
            runtime.verify(payload, packet_digest=runtime.packet_digest(
                {'result': payload, 'artifacts': []}))
        else:
            tools.invoke(request('verify', {'result': payload,
                'artifacts': [text_artifact(path='not-materialized.md')]}))
    assert [name for name, rows in database_snapshot(runtime).items() if rows != before[name]] == []
    values = tools.invoke(request('show', {'queries': [
        {'id': 'task', 'kind': 'task'}, {'id': 'registry', 'kind': 'verification_registry'}]}))
    assert values['results'][0]['value']['status'] == 'active'
    assert values['results'][1]['value']['revision'] == 0
    assert not list(Path(context['task_root']).rglob('not-materialized.md'))


def test_rejected_registry_still_allows_public_handoff_and_restart(project):
    tools, context = registry_case(project)
    payload = result(context)
    payload['method_additions'] = change(operation('reschedule', 'RED', stages=['test_remediation']))
    with pytest.raises(PoiseError, match='active observe'):
        tools.invoke(request('verify', {'result': payload, 'artifacts': []}))
    handed = tools.invoke(request('handoff', {'request_id': 'registry-preserve',
        'reason': 'Preserve the rejected candidate without changing the registry.',
        'result': None, 'commit_message': None, 'artifact_paths': []}))
    assert handed['status'] == 'handed_off'
    current = tools.runtime.task_queries.record(context['task'])
    restarted = tools.invoke(request('task', {'action': 'restart',
        'request_id': 'registry-restart', 'task_id': context['task'],
        'expected_version': current['version'], 'reason': 'Revise the specification.',
        'authorization': 'Fixture user explicitly authorized restart.'}))
    assert restarted['status'] == 'newborn'


@pytest.mark.parametrize('boundary', ['cleanup', 'response'])
def test_late_locked_response_replays_the_durable_verified_packet(prepared, monkeypatch, boundary):
    runtime, tools, context, packet = prepared
    owner, attribute = ((runtime, '_cleanup_runtime') if boundary == 'cleanup'
                        else (tools.interactions, 'delivered'))
    original = getattr(owner, attribute)
    captured = {}
    def fail_once(*args, **kwargs):
        assert runtime.current_task()['status'] == 'verified'
        captured['snapshot'] = database_snapshot(runtime)
        captured['report'] = deepcopy(runtime.current_task()['last_report'])
        monkeypatch.setattr(owner, attribute, original)
        raise sqlite3.OperationalError('database is locked: late response finalization')
    monkeypatch.setattr(owner, attribute, fail_once)
    with pytest.raises(sqlite3.OperationalError, match='late response'):
        tools.invoke(packet)
    monkeypatch.setattr(runtime, '_execute_checks', lambda *a, **kw: pytest.fail('duplicate checks'))
    replay = tools.invoke(deepcopy(packet))
    assert replay['replayed'] is True
    assert replay['checks'] == captured['report']['checks']
    assert replay['commit'] == captured['report']['commit']
    after = database_snapshot(runtime)
    # The failed response was not delivered. Its first successful replay may
    # record the delivery marker, but must not repeat any business transition.
    assert [name for name, rows in after.items()
            if name != 'interaction_reports' and rows != captured['snapshot'][name]] == []
    assert len(after['interaction_reports']) == 1
    tools.invoke(deepcopy(packet))
    assert database_snapshot(runtime) == after
    assert tools.invoke(request('bootstrap', {'task': None, 'decision': None,
        'feedback': None, 'rework_stage': None}))['status'] == 'verified'


def test_valid_registry_change_refreshes_candidate_evidence_checks(project):
    tools, context = registry_case(project)
    runtime = tools.runtime
    payload = result(context)
    payload['method_additions'] = change(operation('remove', 'CLEANUP_FULL_GREEN'))
    with runtime.store.unit_of_work() as uow:
        task = uow.tasks.load(context['task'])
        candidate = task.submit(runtime.session, payload['sections'], (), payload['commit_message'],
            payload['content_additions'], payload['trace'], payload['method_additions'],
            payload['stage_work'], payload['evidence_work']).task
        assert 'CLEANUP_FULL_GREEN' in task.evidence_plan.stage('implementation').checks
        assert candidate.evidence_plan.stage('implementation').checks == ('GREEN',)
    runtime.task_commands.submit(context['task'], runtime.session, payload)
    with runtime.store.unit_of_work() as uow:
        assert uow.tasks.load(context['task']).evidence_plan == candidate.evidence_plan
