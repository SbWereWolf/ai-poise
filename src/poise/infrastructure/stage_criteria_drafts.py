"""Pure historical migration of newborns without future stage contracts.

Full-contract transformation remains frozen in stage_criteria_history. A draft
gets only existing process annotations; absence never creates future work data.
Persistence and publication remain with the existing migration coordinator.
"""
from copy import deepcopy
import json

from .stage_criteria_history import identity, process_candidate


def draft_candidate(row, mapping):
    if row['status'] != 'newborn':
        raise ValueError('Migration newborn draft source must be newborn')
    metadata = json.loads(row['metadata'])
    if not isinstance(metadata, dict):
        raise ValueError('Invalid historical newborn draft metadata')
    if row['version'] != mapping['expected_revision']:
        raise ValueError('Historical newborn draft revision mismatch')
    old_digest = identity(metadata)
    if old_digest != mapping['expected_metadata_digest']:
        raise ValueError('Historical newborn draft metadata digest mismatch')
    newborn = metadata.get('newborn')
    if not isinstance(newborn, dict) or newborn.get('ready') is not False:
        raise ValueError('Invalid historical newborn draft state')
    draft = newborn.get('draft')
    if not isinstance(draft, dict) or 'stage_contracts' in draft:
        raise ValueError('Historical newborn draft requires absent stage contracts')
    candidate = deepcopy(metadata)
    if 'process' not in metadata:
        if 'goal_type' in draft:
            raise ValueError('Historical newborn draft selected type has no process')
        if mapping['stages']:
            raise ValueError('Untyped newborn draft requires empty stage mapping')
    else:
        process = metadata['process']
        if not isinstance(process, dict):
            raise ValueError('Invalid historical newborn draft process')
        selected_type = draft.get('goal_type')
        if (not isinstance(selected_type, str) or not selected_type.strip()
                or process.get('goal_type') != selected_type):
            raise ValueError('Historical newborn draft selected type mismatch')
        try:
            candidate['process'] = process_candidate(process, mapping['stages'])
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f'Invalid historical newborn draft stage mapping: {exc}') from exc
    summary = {'kind': 'newborn_draft', 'task_id': row['id'], 'revision': row['version'],
               'old_digest': old_digest, 'new_digest': identity(candidate)}
    return candidate, summary
