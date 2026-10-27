import json
from datetime import datetime, timezone

from ...modules.foundation.errors import PoiseError


class SqliteWorkPacketRepository:
    """Current delivery identities; verified results and evidence live elsewhere."""

    def __init__(self, connection):
        self.db = connection

    def current(self, task_id: str, stage: str, iteration: int) -> str | None:
        row = self.db.execute(
            "SELECT digest FROM work_packets "
            "WHERE task_id=? AND stage=? AND iteration=?",
            (task_id, stage, iteration),
        ).fetchone()
        return None if row is None else row[0]

    def remember(
        self, task_id: str, stage: str, iteration: int, digest: str,
    ) -> None:
        current = self.current(task_id, stage, iteration)
        if current is not None and current != digest:
            raise PoiseError("Verified packet cannot be replaced")
        self.db.execute(
            "INSERT OR IGNORE INTO work_packets VALUES(?,?,?,?)",
            (task_id, stage, iteration, digest),
        )

    def invalidate(self, task_id: str) -> None:
        self.db.execute("DELETE FROM work_packets WHERE task_id=?", (task_id,))

    def selection_request(self, task_id, request_id, stage, iteration):
        rows = self.db.execute(
            "SELECT event,data FROM journal WHERE task_id=? AND event IN "
            "('work.selection_admitted','work.selection_guarded') "
            "AND json_extract(data,'$.request_id')=? "
            "AND json_extract(data,'$.expected.stage')=? "
            "AND json_extract(data,'$.expected.iteration')=? ORDER BY seq",
            (task_id, request_id, stage, iteration),
        ).fetchall()
        admitted = None
        for event, raw in rows:
            value = json.loads(raw)
            if event == 'work.selection_admitted':
                admitted = value
            elif admitted is not None:
                admitted['guard'] = value['guard']
        return admitted

    def _selection_event(self, task_id, actor, event, record):
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), actor, task_id, event,
                         json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))))

    def admit_selection(self, task_id, actor, record):
        prior = self.selection_request(task_id, record['request_id'], record['expected']['stage'], record['expected']['iteration'])
        if prior is not None:
            if prior['digest'] != record['digest']:
                raise PoiseError('selection_request_conflict: request_id already binds different input')
            return prior
        self._selection_event(task_id, actor, 'work.selection_admitted', record)
        return record

    def record_selection_guard(self, task_id, actor, request_id, expected, guard):
        saved = self.selection_request(task_id, request_id, expected['stage'], expected['iteration'])
        if saved is None:
            raise PoiseError('selection_recovery_pending: admitted request is missing')
        if saved.get('guard') == guard:
            return
        if saved.get('guard') is not None:
            raise PoiseError('selection_recovery_pending: guarded publication identity changed')
        self._selection_event(task_id, actor, 'work.selection_guarded',
                              {'request_id': request_id, 'expected': expected, 'guard': guard})
