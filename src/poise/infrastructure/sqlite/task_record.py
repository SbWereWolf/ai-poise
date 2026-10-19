"""One SQL snapshot for a runner record, projected after its reader closes."""
from dataclasses import dataclass
import json
from .task_snapshot import capture_methods
from .duplicate_tasks import capture_duplicate_family, restore_duplicate_family


@dataclass(frozen=True)
class TaskRecordCapture:
    row: dict
    family: object
    methods: tuple[str, ...]
    history: tuple[str, ...]
    progression: tuple[str, ...]
    reached: tuple[str, ...]
    locations: str | None

    def progression_view(self):
        if not self.progression:
            return None
        action = json.loads(self.progression[-1])
        return {'request_id': action['request_id'], 'target_stage': action['target_stage'],
                'status': 'reached' if action['request_id'] in self.reached else 'active'}


def capture_record_in(db, task_id):
    row = db.execute('SELECT t.id,t.status,t.stage_index,t.iteration,t.claimed_by,t.version,'
        't.current_submission_id,t.metadata,e.data AS execution,e.version AS execution_version,'
        'w.data AS workflow FROM tasks t LEFT JOIN task_execution e ON e.task_id=t.id '
        'LEFT JOIN task_workflows w ON w.task_id=t.id WHERE t.id=?', (task_id,)).fetchone()
    if row is None:
        return None
    location = db.execute('SELECT data FROM transfer_locations WHERE task_id=?', (task_id,)).fetchone()
    return TaskRecordCapture(dict(row), capture_duplicate_family(db, task_id),
        capture_methods(db, task_id), tuple(r[0] for r in db.execute(
            'SELECT data FROM task_events WHERE task_id=? ORDER BY seq', (task_id,))),
        tuple(r[0] for r in db.execute("SELECT data FROM journal WHERE task_id=? "
            "AND event='progression.started' ORDER BY seq", (task_id,))),
        tuple(r[0] for r in db.execute("SELECT json_extract(data,'$.request_id') FROM journal "
            "WHERE task_id=? AND event IN ('progression.reached','progression.result')", (task_id,))),
        None if location is None else location[0])

