"""Fixed Task header/execution facts shared by its allocation and Sprint consumers."""
import json


def capture_task_facts(db, task_ids):
    return tuple(dict(r) for r in db.execute(
        'SELECT t.id,t.status,t.claimed_by,t.version,t.stage_index,t.iteration,t.metadata,e.data AS execution, '
        '(SELECT id FROM sessions WHERE task_id=t.id) AS worktree_owner '
        'FROM tasks t LEFT JOIN task_execution e ON e.task_id=t.id '
        'WHERE t.id IN (SELECT value FROM json_each(?)) ORDER BY t.id', (json.dumps(task_ids),)))

