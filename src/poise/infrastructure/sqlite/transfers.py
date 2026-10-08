"""Scoped, same-schema transfer storage. No copying live session ownership."""
from copy import deepcopy
import json
import sqlite3
from pathlib import Path
from datetime import datetime,timezone
from ...common import PoiseError,encoded,digest
from ...modules.transfers.domain import validate_saved_work, ExportPreparation
from .database import SCHEMA,SCHEMA_VERSION
from .tasks import SqliteTaskRepository
from .sprints import SqliteSprintRepository
from .transfer_records import ALL_TABLES,ACCOUNTING_TABLES,insert,relocate_path


def snapshot_fingerprint(tables):
    # A relational snapshot is a set of records. SQLite index selection must not
    # affect the integrity check; explicit seq/version fields retain ordering.
    return digest({table:sorted(rows,key=encoded) for table,rows in tables.items() if table!='journal'})


def completed_external_execution(pending):
    if not isinstance(pending,dict):return False
    if pending.get('kind')=='task_cleanup':
        from ...modules.task_cleanup.domain import CleanupRun
        return CleanupRun.restore(pending).complete
    if pending.get('kind')=='result_integration':
        from ...modules.result_integration.domain import IntegrationRun
        return IntegrationRun.restore(pending).complete
    return False


class SqliteTransferRepository:
    def __init__(self,database):self.database=database

    def pending_consumers(self, task_id):
        return self._consumers(task_id, ('preparing', 'prepared'))

    def preparing_consumers(self, task_id):
        return self._consumers(task_id, ('preparing',))

    def _consumers(self, task_id, phases):
        with self.database.transaction() as db:
            rows = db.execute("SELECT data FROM transfer_requests WHERE json_extract(data,'$.phase') IN (" +
                              ','.join('?' for _ in phases) + ")", phases)
            # Import preparation has a different contract and is not an export consumer.
            return any('task_ids' in data and task_id in ExportPreparation.parse(data).data['task_ids']
                       for data in (json.loads(row[0]) for row in rows))

    def remember_export(self, actor, rid, identity, data, expected_phase):
        if data['phase'] != 'complete':
            ExportPreparation.parse(data)
        with self.database.transaction() as db:
            prior = db.execute('SELECT digest,data FROM transfer_requests WHERE actor=? AND request_id=?',
                               (actor, rid)).fetchone()
            if prior is not None and prior['digest'] != identity:
                raise PoiseError('Transfer request identity conflict')
            phase = None if prior is None else json.loads(prior['data'])['phase']
            if phase != expected_phase:
                raise PoiseError('Export preparation phase changed concurrently')
            db.execute('INSERT INTO transfer_requests VALUES(?,?,?,?) ON CONFLICT(actor,request_id) DO UPDATE SET data=excluded.data',
                       (actor, rid, identity, encoded(data)))

    def request(self,actor,rid,identity):
        with self.database.transaction() as db:
            row=db.execute('SELECT digest,data FROM transfer_requests WHERE actor=? AND request_id=?',(actor,rid)).fetchone()
            if row is None:return None
            if row['digest']!=identity:raise PoiseError('Transfer request ID conflict')
            return json.loads(row['data'])

    def remember(self,actor,rid,identity,data):
        with self.database.transaction() as db:
            prior=db.execute('SELECT digest FROM transfer_requests WHERE actor=? AND request_id=?',(actor,rid)).fetchone()
            if prior is not None and prior['digest']!=identity:raise PoiseError('Transfer request identity conflict')
            db.execute('INSERT INTO transfer_requests VALUES(?,?,?,?) ON CONFLICT(actor,request_id) DO UPDATE SET data=excluded.data',
                       (actor,rid,identity,encoded(data)))

    def capture(self, task_ids, sprint_id, project, limit):
        with self.database.transaction() as db:
            if sprint_id is not None:
                sprint=db.execute('SELECT project,state FROM sprints WHERE id=?',(sprint_id,)).fetchone()
                if sprint is None or sprint['project']!=project:raise PoiseError('Unknown sprint in this project')
                if sprint['state']=='draft':raise PoiseError('Publish the sprint draft before transferring it')
                task_ids=[r[0] for r in db.execute('SELECT task_id FROM sprint_members WHERE sprint_id=? ORDER BY task_id',(sprint_id,))]
            if not task_ids or len(task_ids)>limit:raise PoiseError('Empty or oversized transfer selection')
            q=','.join('?' for _ in task_ids)
            roots=[dict(r) for r in db.execute(f'SELECT * FROM tasks WHERE id IN ({q}) ORDER BY id',task_ids)]
            if len(roots)!=len(task_ids):raise PoiseError('Unknown task in transfer selection')
            members=list(db.execute(f'SELECT sprint_id,task_id FROM sprint_members WHERE task_id IN ({q})',task_ids))
            if any(r['sprint_id']!=sprint_id for r in members):raise PoiseError('Use complete sprint scope, not partial task selection')
            released={r[0] for r in db.execute(f"SELECT task_id FROM handoffs WHERE task_id IN ({q}) AND state='released'",task_ids)}
            validate_saved_work(roots,released)
            for row in roots:
                SqliteTaskRepository(db).load(row['id'])
                e=db.execute('SELECT data FROM task_execution WHERE task_id=?',(row['id'],)).fetchone()
                if e is not None:
                    pending=json.loads(e[0])['pending']
                    if pending is not None and not completed_external_execution(pending):
                        raise PoiseError('Resolve pending execution before transfer')
            data={table:[] for table in ALL_TABLES}
            data['tasks']=roots
            for table in ALL_TABLES:
                if table=='tasks':continue
                if table in ('sprints','sprint_layers','sprint_members','sprint_dependencies','sprint_requests','sprint_artifacts'):
                    if sprint_id is not None:
                        column='id' if table=='sprints' else 'sprint_id'
                        data[table]=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE {column}=?',(sprint_id,))]
                elif table=='artifacts':
                    data[table]=[dict(r) for r in db.execute(f"SELECT * FROM artifacts WHERE (scope='task' AND owner IN ({q})) OR (scope='sprint' AND owner=?)",[*task_ids,sprint_id])]
                elif table=='interaction_events':
                    data[table]=[dict(r) for r in db.execute(f'SELECT e.* FROM interaction_events e JOIN interaction_bindings b ON b.event_id=e.id WHERE b.task_id IN ({q})',task_ids)]
                else:
                    order=' ORDER BY rowid' if table=='task_methods' else ''
                    data[table]=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE task_id IN ({q})'+order,task_ids)]
            return data

    @staticmethod
    def write_snapshot(path,tables):
        with sqlite3.connect(path,autocommit=True) as db:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            for statement in SCHEMA:db.execute(statement)
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            db.execute('BEGIN');db.execute('PRAGMA defer_foreign_keys=ON')
            try:
                for table in ALL_TABLES:
                    for row in tables[table]:insert(db,table,row)
                if db.execute('PRAGMA foreign_key_check').fetchone():raise PoiseError('Snapshot reference inconsistency')
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:db.execute('ROLLBACK')
                raise

    @staticmethod
    def read_snapshot(path):
        uri=Path(path).resolve().as_uri()+'?mode=ro&immutable=1'
        with sqlite3.connect(uri,uri=True,autocommit=True) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA trusted_schema=OFF')
            if db.execute('PRAGMA user_version').fetchone()[0]!=SCHEMA_VERSION:raise PoiseError('Incompatible schema; no migration')
            # Same schema, not a supplied SQL program or a compatibility importer.
            expected=sqlite3.connect(':memory:');expected.row_factory=sqlite3.Row
            try:
                for statement in SCHEMA:expected.execute(statement)
                schema=lambda c:sorted(tuple(r) for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
                if schema(db)!=schema(expected):raise PoiseError('Snapshot schema differs from current format')
            finally:expected.close()
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                raise PoiseError('Snapshot database integrity error')
            result={table:[dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in ALL_TABLES}
            for row in result['tasks']:SqliteTaskRepository(db).load(row['id'])
            validate_saved_work(result['tasks'],{r['task_id'] for r in result['handoffs'] if r['state']=='released'})
            return result

    def ensure_absent(self,tables):
        with self.database.transaction() as db:self._ensure_absent(db,tables)

    @staticmethod
    def _ensure_absent(db,tables):
        for table in ('tasks','sprints'):
            for row in tables[table]:
                for target in ('tasks','sprints'):
                    if db.execute(f'SELECT 1 FROM {target} WHERE id=?',(row['id'],)).fetchone():
                        raise PoiseError(f"Destination owner {row['id']} already exists; no overwrite or merge")
        from .accounting import check_accounting_import
        check_accounting_import(db,tables)
        for row in tables['interaction_events']:
            old=db.execute('SELECT data FROM interaction_events WHERE id=?',(row['id'],)).fetchone()
            if old is not None and old['data']!=row['data']:raise PoiseError('Interaction event identity conflict')
        for row in tables['interaction_bindings']:
            old=db.execute('SELECT task_id FROM interaction_bindings WHERE event_id=?',(row['event_id'],)).fetchone()
            if old is not None:raise PoiseError('Interaction already belongs to another local task')

    def install(self,tables,binding,actor,rid,identity,receipt):
        with self.database.transaction() as db:
            self._ensure_absent(db,tables)
            db.execute('PRAGMA defer_foreign_keys=ON')
            SqliteTaskRepository(db).restore_snapshot(tables,binding)
            SqliteSprintRepository(db).restore_snapshot(tables,binding)
            for table in ('artifacts','task_artifacts','sprint_artifacts','interaction_events','interaction_bindings','journal'):
                for old in tables[table]:
                    row=deepcopy(old)
                    if table=='journal':row.pop('seq')
                    if table=='artifacts':row['path']=relocate_path(row['path'],binding['locations'])
                    if table=='interaction_events' and db.execute('SELECT 1 FROM interaction_events WHERE id=?',(row['id'],)).fetchone():continue
                    insert(db,table,row)
            from .accounting import restore_accounting
            restore_accounting(db,tables)
            for tid,mapping in binding['by_task_locations'].items():
                db.execute('INSERT INTO transfer_locations VALUES(?,?)',(tid,encoded(mapping)))
            for row in tables['tasks']:SqliteTaskRepository(db).load(row['id'])
            if db.execute('PRAGMA foreign_key_check').fetchone():raise PoiseError('Imported snapshot has unresolved references')
            db.execute('UPDATE transfer_requests SET data=? WHERE actor=? AND request_id=? AND digest=?',
                       (encoded({'phase':'complete','receipt':receipt}),actor,rid,identity))
            db.execute('INSERT INTO transfer_imports VALUES(?,?)',(receipt['package_digest'],encoded(receipt)))
            db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                       (datetime.now(timezone.utc).isoformat(),actor,None,'transfer.imported',encoded(receipt)))

    def imported(self,sha):
        with self.database.transaction() as db:
            row=db.execute('SELECT data FROM transfer_imports WHERE package_digest=?',(sha,)).fetchone()
            return None if row is None else json.loads(row['data'])
