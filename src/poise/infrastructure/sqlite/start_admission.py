"""Capture existing migration facts without deciding Task eligibility."""
import json

from ...modules.tasks.start_admission import migration_receipts


def capture_start_migrations(db, metadata):
    receipts = migration_receipts(metadata)
    if not receipts:
        return ()
    records = [(row[0], json.loads(row[0])) for row in db.execute(
        'SELECT data FROM journal WHERE event=? ORDER BY seq',
        ('task_stage_criteria_migration',),
    )]
    requests = {(record['request_id'], record['request_digest'])
                for _, record in records
                if record['phase'] == 'intent' and record['receipt'] in receipts}
    # Keep the same-intent completion even when its result receipt is wrong.
    return tuple(raw for raw, record in records
                 if record.get('receipt') in receipts
                 or record.get('result', {}).get('receipt') in receipts
                 or (record['request_id'], record['request_digest']) in requests)
