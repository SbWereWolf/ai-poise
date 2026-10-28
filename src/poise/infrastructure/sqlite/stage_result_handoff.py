"""Append-only typed handoff snapshots in the existing Task journal."""
from datetime import datetime, timezone

from ...modules.foundation.errors import VersionConflict, PoiseError


class SqliteHandoffRepository:
    def __init__(self, connection):
        self.db = connection

    def capture(self, request_id):
        row = self.db.execute(
            "SELECT data FROM journal WHERE event='stage_handoff.state' "
            "AND json_extract(data,'$.request_id')=? ORDER BY seq DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        return None if row is None else row['data']

    def save_prepared(self, actor, state, encoded, expected_revision):
        row = self.db.execute(
            "SELECT data,json_extract(data,'$.revision') AS revision, "
            "json_extract(data,'$.actor') AS actor, "
            "json_extract(data,'$.intent_digest') AS intent_digest "
            "FROM journal WHERE event='stage_handoff.state' "
            "AND json_extract(data,'$.request_id')=? ORDER BY seq DESC LIMIT 1",
            (state.request_id,),
        ).fetchone()
        if state.actor != actor or (row is not None and (
                row['actor'] != actor or row['intent_digest'] != state.intent_digest)):
            raise PoiseError('Stage handoff actor or intent conflict')
        if row is not None and row['data'] == encoded:
            return
        revision = 0 if row is None else row['revision']
        if revision != expected_revision or state.revision != expected_revision + 1:
            raise VersionConflict('Stage handoff revision changed; retry its exact public request')
        self.db.execute(
            'INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(), actor, state.task_id,
             'stage_handoff.state', encoded),
        )
