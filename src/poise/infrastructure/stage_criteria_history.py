"""Frozen stage-criteria historical transformation. Standard library only.

This module deliberately does not load current Task/workflow models. Its caller
supplies observed historical records; publication and storage belong elsewhere.
"""
from copy import deepcopy
import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def identity(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def workflow_candidate(source, stages):
    candidate = deepcopy(source)
    original = source['stages']
    if ([stage['id'] for stage in original] != [stage['stage_id'] for stage in stages]
            or len({stage['id'] for stage in original}) != len(original)):
        raise ValueError('Migration must map every historical stage exactly once in order')
    for old, mapped, new in zip(original, stages, candidate['stages'], strict=True):
        if 'purpose' in old or 'criteria_authority' in old:
            raise ValueError('The source already has stage-criteria semantics')
        new['purpose'] = mapped['purpose']
        new['criteria_authority'] = deepcopy(mapped['criteria_authority'])
    return candidate


def _execution_source(task_id, stage_id, execution, contract, events, submissions):
    state = execution['state']
    if execution['source_revision'] is not None:
        raise ValueError('Historical source has no stage-criteria revision')
    proved_started = False
    proved_unstarted = False
    own_events = [row for row in events if row['task_id'] == task_id]
    own_submissions = [row for row in submissions if row['task_id'] == task_id]
    actual_start = any(row['stage'] == stage_id for row in own_submissions)
    for ref in execution['source_refs']:
        if ref['kind'] == 'contract':
            if ref['digest'] != identity(contract):
                raise ValueError('Historical contract source digest mismatch')
        elif ref['kind'] == 'submission':
            matches = [row for row in own_submissions if row['seq'] == ref['id']]
            if len(matches) != 1 or matches[0]['stage'] != stage_id:
                raise ValueError('Historical submission does not belong to this target')
            proved_started = True
        else:
            matches = [row for row in own_events if row['seq'] == ref['id']]
            if len(matches) != 1:
                raise ValueError('Historical Task event is absent or foreign')
            event = json.loads(matches[0]['data'])
            if event['event'] in ('created_newborn', 'became_available'):
                proved_unstarted = not actual_start and not any(
                    json.loads(row['data']).get('stage') == stage_id
                    and json.loads(row['data']).get('event') in ('started', 'submitted', 'verified')
                    for row in own_events)
            if (event.get('stage') == stage_id
                    and event['event'] in ('started', 'submitted', 'verified')):
                proved_started = True
    if state == 'started' and not proved_started:
        raise ValueError('Historical start is not proven by the named sources')
    if state == 'unstarted' and not proved_unstarted:
        raise ValueError('Historical unstarted state is not proven by the named sources')


def task_candidate(row, mapping, events, submissions, receipt):
    metadata = json.loads(row['metadata'])
    original = deepcopy(metadata)
    newborn = row['status'] == 'newborn'
    kind = 'newborn_task' if newborn else 'executable_task'
    if mapping['kind'] != kind:
        raise ValueError('Historical Task kind mismatch')
    expected = mapping['expected_revision'] if newborn else mapping['expected_version']
    if row['version'] != expected or identity(metadata['workflow']) != mapping['expected_workflow_digest']:
        raise ValueError('Historical Task source revision mismatch')
    contract = metadata['newborn']['draft'] if newborn else metadata['contract']
    old_contract = deepcopy(contract)
    originals = old_contract['stage_contracts']
    stage_ids = [stage['stage_id'] for stage in mapping['stages']]
    if (not isinstance(originals, list)
            or [stage.get('stage_id') for stage in originals] != stage_ids
            or len(set(stage_ids)) != len(originals)):
        raise ValueError('Historical stage contracts must exist uniquely in workflow order')
    metadata['workflow'] = workflow_candidate(metadata['workflow'], mapping['stages'])
    book = {'stages': {}, 'requests': {}}
    for stage, mapped in zip(contract['stage_contracts'], mapping['stages'], strict=True):
        if set(stage) != {'stage_id', 'allowed_paths', 'entry_requirements', 'exit_requirements'}:
            raise ValueError('Unsupported historical stage contract shape')
        for key in ('allowed_paths', 'entry_requirements', 'exit_requirements'):
            values = stage[key]
            if (not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values)
                    or len(values) != len(set(values))):
                raise ValueError('Incompatible historical stage contract values')
        _execution_source(row['id'], stage['stage_id'], mapped['execution'],
                          old_contract, events, submissions)
        source = {'kind': 'historical', 'original_digest': identity(stage),
                  'migration_receipt': receipt}
        state = mapped['execution']['state']
        book['stages'][stage['stage_id']] = {
            'purpose': mapped['purpose'], 'revision': 0, 'criteria': deepcopy(mapped['criteria']),
            'entry_requirements': deepcopy(stage['entry_requirements']),
            'exit_requirements': deepcopy(stage['exit_requirements']), 'source': source,
            'execution': {'state': state, 'epoch': 0, 'bound_revision': None if state == 'unstarted' else 0},
            'proposals': [], 'history': [], 'revisions': [],
        }
        stage['criteria'] = deepcopy(mapped['criteria'])
    metadata['stage_criteria'] = book
    key = 'draft' if newborn else 'contract'
    result = {'kind': kind, 'task_id': row['id'],
              'old_digest': identity({'workflow': original['workflow'], key: old_contract}),
              'new_digest': identity({'workflow': metadata['workflow'], key: contract}),
              'revision' if newborn else 'version': row['version']}
    return metadata, result
