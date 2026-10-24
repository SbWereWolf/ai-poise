"""Advance transport; execution, evidence and route decisions have own owners."""
from apps.execution.domain.process.errors import DomainError
from apps.execution.gate_protocol import _exact, _integer, _task, _text


def packet(value, config):
    action = value.get('action')
    if type(action) is not str or action not in ('next', 'fast_forward'):
        raise DomainError('Advance action: select next or fast_forward')
    common = {'action', 'request_id', 'expected_revision', 'task_id', 'expected_generation',
              'expected_contract_revision', 'actor_id', 'source', 'entry_tree'}
    _exact(value, common | ({'proof'} if action == 'next' else {'target_stage', 'proofs'}), 'Advance input')
    _task(value['task_id'], config)
    _integer(value['expected_revision'], 'Advance expected_revision integer', 0)
    _integer(value['expected_generation'], 'Advance expected_generation integer', 1)
    for key in ['request_id', 'expected_contract_revision', 'actor_id', 'entry_tree']:
        _text(value[key], 'Advance ' + key)
    _exact(value['source'], {'commit', 'tree'}, 'Advance source')
    for key in ['commit', 'tree']:
        _text(value['source'][key], 'Advance source.' + key)
    if action == 'next':
        _exact(value['proof'], {'context_id', 'candidate_id', 'outcome', 'evidence_id'}, 'Advance proof')
        for key, field in value['proof'].items():
            _text(field, 'Advance proof.' + key)
    else:
        if value['target_stage'] is not None:
            _text(value['target_stage'], 'Advance target_stage')
        if type(value['proofs']) is not list:
            raise DomainError('Advance proofs: provide a list')
        seen = set()
        for proof in value['proofs']:
            _exact(proof, {'stage_id', 'context_id', 'candidate_id', 'evidence_id'}, 'Advance proof')
            for key, field in proof.items():
                _text(field, 'Advance proof.' + key)
            if proof['stage_id'] in seen:
                raise DomainError('Advance proofs: duplicate stage_id')
            seen.add(proof['stage_id'])


def query_item(value, config):
    _exact(value, {'id', 'kind', 'task_id'}, 'Progression query')
    _text(value['id'], 'Progression query id')
    _task(value['task_id'], config)
