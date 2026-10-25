"""Addressed outcome transport; decision and admission belong to Execution."""
from apps.execution.domain.process.errors import DomainError
from apps.execution.gate_protocol import _exact, _integer, _task, _text


def packet(value, config):
    _exact(value, {'request_id', 'expected_revision', 'task_id', 'expected_generation',
        'expected_contract_revision', 'actor_id', 'outcome_id', 'intent', 'reason',
        'source', 'result_commit'}, 'Outcome input')
    _task(value['task_id'], config)
    _integer(value['expected_revision'], 'Outcome expected_revision', 0)
    _integer(value['expected_generation'], 'Outcome expected_generation', 1)
    for key in ('request_id', 'expected_contract_revision', 'actor_id', 'outcome_id', 'reason'):
        _text(value[key], 'Outcome ' + key)
    if value['intent'] not in ('accept', 'cancel'):
        raise DomainError('Outcome intent: select accept or cancel')
    if value['intent'] == 'cancel':
        if value['source'] is not None or value['result_commit'] is not None:
            raise DomainError('Cancel source and result_commit must be null')
    else:
        _exact(value['source'], {'commit', 'tree'}, 'Outcome source')
        for key in ('commit', 'tree'):
            _text(value['source'][key], 'Outcome source.' + key)
        if value['result_commit'] is not None:
            _text(value['result_commit'], 'Outcome result_commit')


def query_item(value, config):
    _exact(value, {'id', 'kind', 'task_id', 'outcome_id'}, 'Outcome query')
    _task(value['task_id'], config)
    _text(value['id'], 'Outcome query id')
    _text(value['outcome_id'], 'Outcome query outcome_id')
