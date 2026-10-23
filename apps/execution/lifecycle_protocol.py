"""Explicit lifecycle configuration and exact transport; decisions stay in the domain."""
from copy import deepcopy

from apps.execution.domain.process.errors import DomainError
from apps.execution.domain.process.validation import configuration_fields
from apps.execution.gate_protocol import _exact, _integer, _task, _text

MUTABLE_FIELDS = {'requirements_snapshot_revision', 'process_ref', 'stage_expectations',
                  'methods', 'evidence_plan'}
ACTION_FIELDS = {
    'freeze': {'requirements_snapshot'},
    'enter': {'entry_tree'},
    'present': {'context_id', 'candidate_id'},
    'propose_restart': {'proposal_id', 'contract', 'requirements_snapshot', 'reason'},
    'decide_restart': {'proposal_id', 'decision', 'reason'},
    'restart': {'proposal_id', 'approval_id'},
    'rework': {'target_stage_id', 'reason', 'entry_tree'},
}


def _texts(value, where, *, nonempty):
    if not isinstance(value, list) or (nonempty and not value):
        raise DomainError(f'{where}: provide an explicit list')
    for item in value:
        _text(item, where)
    if len(value) != len(set(value)):
        raise DomainError(f'{where}: provide unique values')


def settings(raw):
    selected = configuration_fields(raw, {'actors', 'policies'}, 'Execution lifecycle', DomainError)
    if not isinstance(selected['actors'], dict) or not selected['actors']:
        raise DomainError('lifecycle.actors: provide explicit actor roles')
    for actor, role in selected['actors'].items():
        _text(actor, 'lifecycle actor')
        _text(role, 'lifecycle role')
    if not isinstance(selected['policies'], dict) or not selected['policies']:
        raise DomainError('lifecycle.policies: provide explicit restart policies')
    for policy_id, policy in selected['policies'].items():
        _text(policy_id, 'lifecycle policy id')
        values = configuration_fields(policy, {'approving_roles', 'revision_fields'},
                                      'Lifecycle policy', DomainError)
        _texts(values['approving_roles'], 'approving_roles', nonempty=True)
        _texts(values['revision_fields'], 'revision_fields', nonempty=False)
        if set(values['revision_fields']) - MUTABLE_FIELDS:
            raise DomainError('revision_fields: select supported mutable contract fields')
    return deepcopy(raw)


def packet(value, config, validate_contract):
    action = value.get('action')
    if type(action) is not str or action not in ACTION_FIELDS:
        raise DomainError('Select a supported lifecycle action')
    common = {'action', 'request_id', 'expected_revision', 'task_id',
              'expected_generation', 'expected_contract_revision', 'actor_id'}
    _exact(value, common | ACTION_FIELDS[action], 'Lifecycle input')
    _task(value['task_id'], config)
    _integer(value['expected_revision'], 'Lifecycle expected_revision', 0)
    _integer(value['expected_generation'], 'Lifecycle expected_generation', 1)
    for key in ('request_id', 'expected_contract_revision', 'actor_id'):
        _text(value[key], 'Lifecycle ' + key)
    for key in ACTION_FIELDS[action] - {'requirements_snapshot', 'contract'}:
        _text(value[key], 'Lifecycle ' + key)
    if 'requirements_snapshot' in value and not isinstance(value['requirements_snapshot'], dict):
        raise DomainError('Provide a published Requirements snapshot object')
    if action == 'propose_restart':
        validate_contract(value['contract'], config)
    if action == 'decide_restart' and value['decision'] not in ('approved', 'rejected'):
        raise DomainError('Select approved or rejected decision')


def query_item(value, config):
    _exact(value, {'id', 'kind', 'task_id'}, 'Lifecycle query')
    _text(value['id'], 'Lifecycle query id')
    _task(value['task_id'], config)
