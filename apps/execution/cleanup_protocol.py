"""Exact cleanup transport; resource decisions belong to the domain owner."""
from apps.execution.domain.process.errors import DomainError
from apps.execution.domain.process.validation import validate_exact_keys
from apps.execution.gate_protocol import _integer, _task, _text


def _exact(value, fields, where):
    validate_exact_keys(value, fields, where, DomainError)


def _resources(value):
    if type(value) is not list:
        raise DomainError('Cleanup resources: provide an explicit list')
    references = {
        'worktree': {'workspace_id', 'path'},
        'branch': {'workspace_id', 'name', 'commit'},
        'temporary': {'path'},
        'temporary_backup': {'path'},
    }
    for resource in value:
        _exact(resource, {'resource_id', 'kind', 'owner', 'reference'}, 'Cleanup resource')
        for key in ('resource_id', 'kind', 'owner'):
            _text(resource[key], 'Cleanup resource.' + key)
        if resource['kind'] not in references:
            raise DomainError('Cleanup resource: select a supported kind')
        fields = references[resource['kind']]
        _exact(resource['reference'], fields, 'Cleanup resource reference')
        for key in fields:
            _text(resource['reference'][key], 'Cleanup resource reference.' + key)


def _disposition(value):
    if value is None:
        return
    if type(value) is not dict:
        raise DomainError('Cleanup disposition: provide an object or null')
    kind = _text(value.get('kind'), 'Cleanup disposition.kind')
    if kind not in ('discard_authorized', 'preserved', 'integrated'):
        raise DomainError('Cleanup disposition: select discard_authorized, preserved or integrated')
    fields = {'kind', 'expected_commit'}
    if kind == 'integrated':
        fields |= {'target_commit', 'integration_request_id'}
    _exact(value, fields, 'Cleanup disposition')
    for key in fields - {'kind'}:
        if key != 'expected_commit' or value[key] is not None:
            _text(value[key], 'Cleanup disposition.' + key)


def _observation(value):
    _exact(value, {'status', 'receipt_id', 'result'}, 'Cleanup observation')
    status = _text(value['status'], 'Cleanup observation.status')
    if status not in ('succeeded', 'failed', 'unknown', 'not_started'):
        raise DomainError('Cleanup observation: select a supported status')
    if status in ('succeeded', 'failed') or value['receipt_id'] is not None:
        _text(value['receipt_id'], 'Cleanup observation.receipt_id')
    result = value['result']
    if status != 'succeeded':
        _exact(result, {'reason', 'recovery'}, 'Cleanup observation.result')
        for key in ('reason', 'recovery'):
            _text(result[key], 'Cleanup observation.result.' + key)
        return
    if type(result) is not dict:
        raise DomainError('Cleanup observation.result: provide an object')
    effect = _text(result.get('effect'), 'Cleanup observation.result.effect')
    fields = {'remove': {'effect'}, 'preserve': {'effect', 'preservation_ref'},
              'integrated': {'effect', 'source_commit', 'target_commit', 'integration_request_id'}}
    shape = {'removed': 'remove', 'preserved': 'preserve', 'integrated': 'integrated'}.get(effect)
    if shape is None:
        raise DomainError('Cleanup observation.result: select a supported effect')
    _exact(result, fields[shape], 'Cleanup observation.result')
    for key in fields[shape]:
        _text(result[key], 'Cleanup observation.result.' + key)


def packet(value, config):
    action = _text(value.get('action'), 'Cleanup action')
    additions = {
        'prepare': {'outcome_id', 'resources', 'commit_disposition', 'authorization'},
        'continue': {'commit_disposition', 'authorization'},
        'observe': {'step_id', 'attempt_id', 'observation'},
    }
    if action not in additions:
        raise DomainError('Cleanup action: select prepare, continue or observe')
    common = {'action', 'request_id', 'expected_revision', 'task_id', 'expected_generation',
              'expected_contract_revision', 'actor_id', 'cleanup_id'}
    _exact(value, common | additions[action], 'Cleanup input')
    _task(value['task_id'], config)
    _integer(value['expected_revision'], 'Cleanup expected_revision', 0)
    _integer(value['expected_generation'], 'Cleanup expected_generation', 1)
    for key in common - {'action', 'task_id', 'expected_revision', 'expected_generation'}:
        _text(value[key], 'Cleanup ' + key)
    if action in ('prepare', 'continue'):
        _text(value['authorization'], 'Cleanup authorization')
        _disposition(value['commit_disposition'])
    if action == 'prepare':
        _text(value['outcome_id'], 'Cleanup outcome_id')
        _resources(value['resources'])
    elif action == 'observe':
        _text(value['step_id'], 'Cleanup step_id')
        _text(value['attempt_id'], 'Cleanup attempt_id')
        _observation(value['observation'])


def query_item(value, config):
    _exact(value, {'id', 'kind', 'task_id', 'cleanup_id'}, 'Cleanup query')
    _task(value['task_id'], config)
    _text(value['id'], 'Cleanup query id')
    _text(value['cleanup_id'], 'Cleanup query cleanup_id')
