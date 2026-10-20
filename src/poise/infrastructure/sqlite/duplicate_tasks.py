"""Raw family capture followed by its connection-free Task projection."""
import json
from ...modules.foundation.errors import PoiseError


def read_duplicate_family(db, task_id):
    return restore_duplicate_family(capture_duplicate_family(db, task_id), task_id)


def capture_duplicate_family(db, task_id):
    row = db.execute("SELECT CASE WHEN json_type(metadata,'$.duplicate_parent_id') IS NULL THEN id ELSE json_extract(metadata,'$.duplicate_parent_id') END AS parent_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise PoiseError('Unknown Task')
    parent_id = row['parent_id']
    children = [r['id'] for r in db.execute(
        "SELECT id FROM tasks WHERE json_extract(metadata,'$.duplicate_parent_id')=? ORDER BY id",
        (parent_id,),
    )]
    if parent_id == task_id and not children:
        return None
    ids = sorted(set([parent_id, *children]))
    # json_each keeps the bind count constant even for large families.
    rows = db.execute(
        "SELECT t.id,t.status,t.stage_index,t.iteration,t.claimed_by,t.version, "
        "json_object('duplicate_parent_id',json_extract(t.metadata,'$.duplicate_parent_id'),"
        "'sprint_id',json_extract(t.metadata,'$.sprint_id'),'workflow',json_extract(t.metadata,'$.workflow'),"
        "'restart_history',CASE WHEN json_type(t.metadata,'$.restart_history') IS NULL "
        "THEN json('[]') ELSE json_extract(t.metadata,'$.restart_history') END) AS metadata, "
        "json_extract(e.data,'$.last_report.commit') AS result_commit FROM tasks t "
        'LEFT JOIN task_execution e ON e.task_id=t.id '
        'WHERE t.id IN (SELECT value FROM json_each(?)) ORDER BY t.id', (json.dumps(ids),),
    ).fetchall()
    return parent_id, tuple(ids), tuple(dict(row) for row in rows)


def restore_duplicate_family(capture, task_id):
    if capture is None:
        return None
    parent_id, expected_ids, rows = capture
    if {row['id'] for row in rows} != set(expected_ids) or task_id not in expected_ids:
        raise PoiseError('Incomplete duplicate family')
    members = []
    for row in rows:
        metadata = json.loads(row['metadata'])
        if row['id'] == parent_id and metadata.get('duplicate_parent_id') is not None:
            raise PoiseError('Incomplete duplicate family: noncanonical parent')
        workflow = metadata.get('workflow')
        stages = workflow.get('stages', []) if isinstance(workflow, dict) else []
        index = row['stage_index']
        stage = stages[index]['id'] if 0 <= index < len(stages) else None
        members.append({
            'task_id': row['id'], 'sprint_id': metadata.get('sprint_id'),
            'stage_id': stage, 'workflow': workflow, 'status': row['status'],
            'iteration': row['iteration'], 'session_id': row['claimed_by'],
            'version': row['version'],
            'result_commit': row['result_commit'] if row['status'] == 'completed' else None,
            'local_repair': next((r['local_repair'] for r in reversed(
                metadata.get('restart_history', [])) if 'local_repair' in r), None),
        })
    return {'parent_id': parent_id, 'members': members}
