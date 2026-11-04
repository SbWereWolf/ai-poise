"""Task start prerequisites shared by execution and read-only discovery. No I/O."""
from ..foundation.errors import DomainError
from .stage_criteria import StageCriteriaBook


def migration_receipts(metadata):
    stages = metadata.get('stage_criteria', {}).get('stages', {})
    return frozenset(
        revision['source']['migration_receipt']
        for stage in stages.values()
        for revision in (*stage['revisions'], stage)
        if revision['source']['kind'] == 'historical'
    )


def _completed_migration(task_id, metadata, records, receipt):
    intents = [record for record in records
               if record['phase'] == 'intent' and record['receipt'] == receipt]
    if not intents or any(record != intents[0] for record in intents):
        return False
    intent = intents[0]
    completed = [record['result'] for record in records
                 if record['phase'] == 'completed'
                 and record['request_id'] == intent['request_id']
                 and record['request_digest'] == intent['request_digest']]
    expected = {'status': 'migrated', 'request_id': intent['request_id'],
                'resources': [item['summary'] for item in intent['resources']],
                'receipt': receipt, 'replayed': False}
    if not completed or any(result != expected for result in completed):
        return False
    targets = [item for item in intent['resources']
               if item['mapping']['kind'] in ('executable_task', 'newborn_task')
               and item['mapping']['task_id'] == task_id]
    if len(targets) != 1:
        return False
    original = targets[0]['candidate']['stage_criteria']['stages']
    current = StageCriteriaBook.from_dict(metadata['stage_criteria']).data['stages']
    if set(current) != set(original):
        return False
    # Criteria owners retain the original revision before legitimate changes.
    # Execution bindings and later history are not immutable migration inputs.
    fields = ('revision', 'criteria', 'entry_requirements', 'exit_requirements', 'source')
    for stage_id, stage in current.items():
        origin = stage if stage['revision'] == 0 else stage['revisions'][0]
        if any(origin[key] != original[stage_id][key] for key in fields):
            return False
    return True


def require_start_execution(task_id, metadata, execution, migration_records):
    if execution is not None and execution['pending'] is not None:
        raise DomainError('Restarted Task execution recovery is still pending')
    receipts = migration_receipts(metadata)
    if not receipts:
        return
    if len(receipts) != 1 or not _completed_migration(
        task_id, metadata, migration_records, next(iter(receipts)),
    ):
        raise DomainError(
            f'Task {task_id}: migration completion is not confirmed; complete the original '
            'migration intent through its migration owner, then retry start'
        )
