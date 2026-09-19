"""One consistent, bounded-query family read through the Task repository."""
import json
from ...modules.foundation.errors import PoiseError


def read_duplicate_family(db, task_id):
    # The caller supplies the existing Task UoW / read transaction. Do not open
    # independent transactions per relative or commit somebody else's UoW.
    row = db.execute('SELECT metadata FROM tasks WHERE id=?', (task_id,)).fetchone()
    if row is None:
        raise PoiseError('Unknown Task')
    parent_id = json.loads(row['metadata']).get('duplicate_parent_id', task_id)
    children = [r['id'] for r in db.execute(
        "SELECT id FROM tasks WHERE json_extract(metadata,'$.duplicate_parent_id')=? ORDER BY id",
        (parent_id,),
    )]
    if parent_id == task_id and not children:
        return None
    ids = sorted(set([parent_id, *children]))
    # json_each keeps the bind count constant even for large families.
    rows = db.execute(
        'SELECT id,status,stage_index,iteration,claimed_by,version,metadata FROM tasks '
        'WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id', (json.dumps(ids),),
    ).fetchall()
    if {row['id'] for row in rows} != set(ids) or task_id not in ids:
        raise PoiseError('Incomplete duplicate family')
    members = []
    for row in rows:
        metadata = json.loads(row['metadata'])
        if row['id'] == parent_id and metadata.get('duplicate_parent_id') is not None:
            raise PoiseError('Incomplete duplicate family: noncanonical parent')
        process = metadata.get('process')
        stages = process.get('stages', []) if isinstance(process, dict) else []
        index = row['stage_index']
        stage = stages[index]['id'] if 0 <= index < len(stages) else None
        members.append({
            'task_id': row['id'], 'sprint_id': metadata.get('sprint_id'),
            'stage_id': stage, 'process': process, 'status': row['status'],
            'iteration': row['iteration'], 'session_id': row['claimed_by'],
            'version': row['version'],
        })
    return {'parent_id': parent_id, 'members': members}
