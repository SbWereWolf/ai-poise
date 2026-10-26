"""Public verification grammar; admission remains with the execution owners."""
from pathlib import Path

from apps.execution.gate_protocol import _exact, _integer, _task, _text
from apps.execution.domain.records import RecordsError


COMMON = {'request_id', 'expected_revision', 'task_id', 'expected_generation',
          'expected_contract_revision', 'actor_id', 'action'}


def verification_settings(raw, directory, lifecycle):
    fields = {'roles', 'max_attempts', 'repository', 'base_ref', 'output_root'}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise RecordsError('Configure verification with explicit roles, max_attempts, repository, base_ref and output_root')
    roles = raw['roles']
    known = set(lifecycle['actors'].values()) if lifecycle else set()
    if (not isinstance(roles, list) or not roles
            or any(not isinstance(r, str) or not r or r not in known for r in roles)
            or len(set(roles)) != len(roles)):
        raise RecordsError('verification.roles requires unique configured lifecycle roles')
    if type(raw['max_attempts']) is not int or raw['max_attempts'] < 1:
        raise RecordsError('verification.max_attempts requires an explicit positive integer')
    for key in ('repository', 'base_ref', 'output_root'):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise RecordsError('verification.' + key + ' requires explicit text')
    return {**raw, 'repository': (Path(directory) / raw['repository']).resolve(),
            'output_root': (Path(directory) / raw['output_root']).resolve()}


def _common(value, config, fields, label):
    _exact(value, COMMON | fields, label)
    _task(value['task_id'], config)
    _integer(value['expected_revision'], label + ' revision', 0)
    _integer(value['expected_generation'], label + ' generation', 1)
    for key in ('request_id', 'expected_contract_revision', 'actor_id'):
        _text(value[key], label + ' ' + key)


def attempt_packet(value, config):
    action = value.get('action')
    fields = {'begin': {'context_id', 'candidate_id', 'source', 'method_ids'},
              'run': {'run_id'}, 'recover': {'run_id'},
              'finish': {'evidence_work'}, 'abandon': {'reason'}}
    if not isinstance(action, str) or action not in fields:
        raise RecordsError('Attempt action: select begin, run, recover, finish or abandon')
    _common(value, config, fields[action] | {'attempt_id'}, 'Attempt')
    _text(value['attempt_id'], 'Attempt attempt_id')
    for key in fields[action] - {'source', 'method_ids', 'evidence_work'}:
        _text(value[key], 'Attempt ' + key)
    if action == 'begin':
        _source(value['source'])
        if (not isinstance(value['method_ids'], list)
                or any(not isinstance(x, str) or not x for x in value['method_ids'])
                or len(set(value['method_ids'])) != len(value['method_ids'])):
            raise RecordsError('Attempt method_ids requires an explicit ordered unique list')


def _source(value):
    _exact(value, {'commit', 'tree'}, 'Verification source')
    for key in value:
        _text(value[key], 'Verification source ' + key)


def reuse_packet(value, config):
    action = value.get('action')
    fields = {
        'prepare': {'source_task_id', 'source_outcome_id', 'source', 'context_id',
                    'candidate_id', 'artifact_mapping'},
        'verify': {'attempt_id'},
        'accept': {'outcome_id', 'reason', 'result_commit'},
    }
    if not isinstance(action, str) or action not in fields:
        raise RecordsError('Reuse action: select prepare, verify or accept')
    _common(value, config, fields[action] | {'reuse_id', 'admission'}, 'Reuse')
    _text(value['reuse_id'], 'Reuse reuse_id')
    for key in fields[action] - {'source', 'artifact_mapping'}:
        _text(value[key], 'Reuse ' + key)
    _exact(value['admission'], {'claimant', 'family', 'eligibility'}, 'Reuse admission')
    _text(value['admission']['claimant'], 'Reuse claimant')
    for key in ('family', 'eligibility'):
        if not isinstance(value['admission'][key], dict):
            raise RecordsError('Reuse admission.' + key + ' requires a public fact object')
    if action == 'prepare':
        _task(value['source_task_id'], config)
        _source(value['source'])
        if not isinstance(value['artifact_mapping'], list):
            raise RecordsError('Reuse artifact_mapping requires an explicit list')
        for mapping in value['artifact_mapping']:
            _exact(mapping, {'source_scope', 'source_path', 'target_scope', 'target_path'}, 'Reuse artifact mapping')
            for key in mapping:
                _text(mapping[key], 'Reuse mapping ' + key)
            if any(mapping[k] not in ('task', 'sprint') for k in ('source_scope', 'target_scope')):
                raise RecordsError('Reuse mappings select task or sprint scopes')
            for key in ('source_path', 'target_path'):
                p = Path(mapping[key])
                if p.is_absolute() or '..' in p.parts:
                    raise RecordsError('Reuse mapping paths must be relative to their scopes')


def verification_query_item(value, config):
    extras = {'attempt': {'attempt_id'}, 'verification': set(), 'reuse': {'reuse_id'}}
    _exact(value, {'id', 'kind', 'task_id'} | extras[value['kind']], 'Verification query')
    _task(value['task_id'], config)
    for key in {'id'} | extras[value['kind']]:
        _text(value[key], 'Verification query ' + key)
