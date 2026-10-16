"""One-time process-format input conversion; ordinary reads stay workflow-only."""
import json
from copy import deepcopy

from .stage_criteria_history import identity, task_candidate, workflow_candidate
from .stage_criteria_drafts import draft_candidate


def process_workflow_candidate(source, stages):
    original = source['stages']
    ids = [stage['id'] for stage in original]
    if ids != [stage['stage_id'] for stage in stages] or len(set(ids)) != len(ids):
        raise ValueError('Configured stage inventory must match the migration mapping; correct the mapping')
    annotated = ['purpose' in stage or 'criteria_authority' in stage for stage in original]
    if not any(annotated):
        return workflow_candidate(source, stages)
    if not all('purpose' in stage and 'criteria_authority' in stage for stage in original):
        raise ValueError('Configured stage annotations are incomplete; correct the configured stages')
    observed = [{'stage_id': stage['id'], 'purpose': stage['purpose'],
                 'criteria_authority': stage['criteria_authority']} for stage in original]
    if observed != stages:
        raise ValueError('Configured stage annotations differ from the migration mapping; correct the mapping')
    return deepcopy(source)


def process_source(row):
    metadata = json.loads(row['metadata'])
    if not isinstance(metadata, dict) or 'workflow' in metadata:
        raise ValueError('Explicit process-format migration requires historical metadata')
    if row['status'] == 'newborn':
        if not isinstance(metadata.get('newborn'), dict):
            raise ValueError('process-format newborn must be an object; provide a valid newborn source')
        if not isinstance(metadata['newborn'].get('draft'), dict):
            raise ValueError('process-format draft must be an object; provide a valid draft source')
    if 'process' not in metadata:
        if row['status'] == 'newborn':
            draft = metadata['newborn']['draft']
            if 'goal_type' not in draft and 'stage_contracts' not in draft:
                return dict(row)
        raise ValueError('Explicit process-format source has no process')
    if not isinstance(metadata['process'], dict):
        raise ValueError('Explicit process-format source requires an object')
    metadata['workflow'] = metadata.pop('process')
    return {**row, 'metadata': json.dumps(metadata, ensure_ascii=False)}


def process_candidate(row, mapping, events, submissions, receipt):
    source = process_source(row)
    if mapping['kind'] == 'newborn_draft':
        original = json.loads(row['metadata'])
        if identity(original) != mapping['expected_metadata_digest']:
            raise ValueError('Historical newborn draft source revision mismatch')
        adapted = {**mapping, 'expected_metadata_digest': identity(json.loads(source['metadata']))}
        candidate, summary = draft_candidate(source, adapted)
        summary['old_digest'] = identity(original)
        return candidate, summary
    return task_candidate(source, mapping, events, submissions, receipt)
