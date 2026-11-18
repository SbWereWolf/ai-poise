"""Selected action receipt identity and detached response decoding."""
from dataclasses import dataclass
import json
from ...modules.foundation.errors import TaskActionIntentConflict


@dataclass(frozen=True)
class TaskActionReceipt:
    data: str

    def result(self):
        return {**json.loads(self.data)['result'], 'replayed': True}


def capture_action_receipt(db, task_id, request_id, digest):
    row = db.execute("SELECT data,json_extract(data,'$.digest') AS digest FROM journal "
        "WHERE task_id=? AND event='newborn.action' AND json_extract(data,'$.request_id')=? "
        'ORDER BY seq LIMIT 1', (task_id, request_id)).fetchone()
    if row is None:
        return None
    if row['digest'] != digest:
        raise TaskActionIntentConflict('Request ID already used with another Task action intent')
    return TaskActionReceipt(row['data'])
