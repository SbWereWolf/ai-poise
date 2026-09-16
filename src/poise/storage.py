from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from .common import encoded, PoiseError
from .infrastructure.sqlite.database import Database
from .infrastructure.sqlite.uow import SqliteUnitOfWork
from .infrastructure.sqlite.tasks import EXECUTION_FIELDS
from .infrastructure.sqlite.queries import TaskQueries
from .infrastructure.sqlite.artifacts import SqliteArtifactRepository


class Store:
    """Composition/receipt facade of the original slice; no Task lifecycle writes."""
    def __init__(self, database: Path, lock: Path, wait: float, poll: float, processes: dict):
        self.database=Database(database,lock,wait,poll)
        self.path=database
        self.processes=processes
        self.queries=TaskQueries(self.database)

    def unit_of_work(self):
        return SqliteUnitOfWork(self.database,self.processes)

    def transaction(self):
        return self.database.transaction()

    def event(self, session: str, task: str | None, event: str, data: object) -> None:
        with self.transaction() as db:
            db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                       (datetime.now(timezone.utc).isoformat(),session,task,event,encoded(data)))

    def bind(self, session: str, task_id: str | None) -> None:
        with self.transaction() as db:
            db.execute('INSERT INTO sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id', (session,task_id))

    def current(self, session: str) -> dict | None:
        with self.transaction() as db:
            rows=db.execute('SELECT id FROM tasks WHERE claimed_by=? ORDER BY id',(session,)).fetchall()
            if len(rows)>1:raise PoiseError(f'Session {session} owns more than one Task')
            return None if not rows else self.queries.record_in(db,rows[0][0])

    def save(self, data: dict) -> None:
        # Runner can persist ONLY execution bookkeeping; never lifecycle or content.
        with self.unit_of_work() as uow:
            task_state = uow.tasks.load(data['id']).state
            expected = {'status':task_state.status.value,'stage_index':task_state.stage_index,
                        'iteration':task_state.iteration,'claimed_by':task_state.claimed_by,
                        '_version':task_state.version}
            if any(data[key] != value for key,value in expected.items()):
                raise PoiseError('Execution writer не может менять или игнорировать stale Task lifecycle')
            data['_execution_version']=uow.execution.save(data['id'],
                {key:data[key] for key in EXECUTION_FIELDS},data['_execution_version'])

    def artifact_records(self, task_id: str) -> list[dict]:
        with self.transaction() as db:
            return SqliteArtifactRepository(db).records(task_id)

    def link_artifacts(self, task_id: str, artifacts: list[dict]) -> None:
        with self.transaction() as db:
            SqliteArtifactRepository(db).link_task(task_id, artifacts)

    def link_sprint_artifacts(self, artifacts):
        with self.transaction() as db:
            SqliteArtifactRepository(db).link_sprint(artifacts)

    def counts(self, task_id: str) -> tuple[int,int]:
        with self.transaction() as db:
            return (db.execute('SELECT COUNT(*) FROM submissions WHERE task_id=?',(task_id,)).fetchone()[0],
                    db.execute('SELECT COUNT(*) FROM evidence WHERE task_id=?',(task_id,)).fetchone()[0])
