"""Sprint-owned writes. Uses the existing externally locked SQLite transaction."""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from ...modules.foundation.errors import PoiseError, VersionConflict
from ...modules.sprints.domain import task_identity
from .tasks import encode


@dataclass(frozen=True)
class PreparedSprintRecord:
    sprint_id: str
    project: str
    actor: str
    state: str
    revision: int
    data: str
    event: str
    members: tuple
    dependencies: tuple


class SqliteSprintRepository:
    def __init__(self, connection):self.db=connection


    def get(self,sprint_id):
        row=self.db.execute('SELECT data FROM sprints WHERE id=?',(sprint_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def save(self,record,expected_revision):
        self.save_prepared(self.prepare(record), expected_revision)

    @staticmethod
    def prepare(record):
        aggregate = record['aggregate']
        sid, revision, state = aggregate['plan']['id'], aggregate['revision'], aggregate['state']
        return PreparedSprintRecord(sid, record['project'], record['actor'], state, revision,
                                    encode(record), encode({'sprint': sid, 'revision': revision,
                                                            'state': state}),
            tuple((sid, task_identity(t)) for t in aggregate['plan']['tasks']),
            tuple((sid, e['predecessor'], e['successor'], e['kind']) for e in aggregate['plan']['dependencies']))

    def save_prepared(self, prepared, expected_revision):
        sid, version = prepared.sprint_id, prepared.revision
        prior = self.db.execute('SELECT revision FROM sprints WHERE id=?', (sid,)).fetchone()
        if expected_revision is None:
            if prior is not None:raise VersionConflict('Sprint already exists')
            if self.db.execute('SELECT 1 FROM tasks WHERE id=?',(sid,)).fetchone():raise PoiseError('Task/sprint ID collision')
            self.db.execute('INSERT INTO sprints VALUES(?,?,?,?,?)',
                            (sid, prepared.project, prepared.state, version, prepared.data))
        else:
            if prior is None or prior['revision']!=expected_revision or version!=expected_revision+1:
                raise VersionConflict('Sprint revision changed; no overwrite')
            self.db.execute('UPDATE sprints SET state=?,revision=?,data=? WHERE id=? AND revision=?',
                            (prepared.state,version,prepared.data,sid,expected_revision))
        self.db.execute('INSERT INTO sprint_layers VALUES(?,?,?,?)',
                        (sid,version,prepared.data,datetime.now(timezone.utc).isoformat()))
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                        (datetime.now(timezone.utc).isoformat(),prepared.actor,None,'sprint.changed',
                         prepared.event))

    def publish_members(self, record):
        self.publish_prepared_members(self.prepare(record))

    def publish_prepared_members(self, prepared):
        existing = {tuple(row) for row in self.db.execute(
            'SELECT sprint_id,predecessor,successor,kind FROM sprint_dependencies WHERE sprint_id=?',
            (prepared.sprint_id,))}
        if existing - set(prepared.dependencies):
            raise PoiseError('Existing sprint dependency projection conflicts with the draft plan')
        self.db.executemany('INSERT OR IGNORE INTO sprint_members VALUES(?,?)', prepared.members)
        self.db.executemany('INSERT OR IGNORE INTO sprint_dependencies VALUES(?,?,?,?)', prepared.dependencies)

    def replace_dependencies(self, record):
        self.replace_prepared_dependencies(self.prepare(record))

    def replace_prepared_dependencies(self, prepared):
        self.db.execute('DELETE FROM sprint_dependencies WHERE sprint_id=?', (prepared.sprint_id,))
        self.db.executemany('INSERT INTO sprint_dependencies VALUES(?,?,?,?)', prepared.dependencies)

    def receipt_raw(self, sprint_id, request_id, digest):
        row = self.db.execute('SELECT digest,data FROM sprint_requests WHERE sprint_id=? AND request_id=?',
                              (sprint_id, request_id)).fetchone()
        if row is None:
            return None
        if row[0] != digest:
            raise PoiseError('Request ID already used with another sprint intent')
        return row[1]

    def receipt(self, sprint_id, request_id, digest):
        raw = self.receipt_raw(sprint_id, request_id, digest)
        return None if raw is None else json.loads(raw)

    def remember(self, sprint_id, request_id, digest, result):
        self.remember_prepared(sprint_id, request_id, digest, encode(result))

    def remember_prepared(self, sprint_id, request_id, digest, result):
        self.db.execute('INSERT INTO sprint_requests VALUES(?,?,?,?)', (sprint_id, request_id, digest, result))

    def bind(self,session,sprint_id):
        self.db.execute('INSERT INTO session_sprints VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET sprint_id=excluded.sprint_id',(session,sprint_id))

    def scope(self,session):
        row=self.db.execute('SELECT sprint_id FROM session_sprints WHERE session_id=?',(session,)).fetchone()
        return None if row is None else row[0]

    @staticmethod
    def capture_task_facts(db, task_ids):
        from .task_facts import capture_task_facts
        return capture_task_facts(db, task_ids)

    @staticmethod
    def capture_facts(db, sprint_id):
        ids = tuple(r[0] for r in db.execute(
            "SELECT CASE WHEN j.type='text' THEN j.value ELSE "
            "COALESCE(json_extract(j.value,'$.request_id'),json_extract(j.value,'$.id')) END "
            "FROM sprints s,json_each(s.data,'$.aggregate.plan.tasks') j WHERE s.id=?", (sprint_id,)))
        return SqliteSprintRepository.capture_task_facts(db, ids)

    @staticmethod
    def capture_eligibility(db, sprint_id):
        return tuple(dict(r) for r in db.execute(
            "SELECT t.id,t.status,t.claimed_by,json_extract(e.data,'$.last_report.commit') AS result_commit "
            'FROM tasks t LEFT JOIN task_execution e ON e.task_id=t.id '
            "WHERE json_extract(t.metadata,'$.sprint_id')=? AND t.id IN "
            "(SELECT CASE WHEN j.type='text' THEN j.value ELSE "
            "COALESCE(json_extract(j.value,'$.request_id'),json_extract(j.value,'$.id')) END "
            "FROM sprints s,json_each(s.data,'$.aggregate.plan.tasks') j WHERE s.id=?) ORDER BY t.id",
            (sprint_id, sprint_id)))

    def facts(self, sprint_id):
        return self.restore_facts(self.get(sprint_id), self.capture_facts(self.db, sprint_id))

    @staticmethod
    def restore_facts(record, rows):
        if record is None:
            return {}
        sprint_id = record['aggregate']['plan']['id']
        current = {task_identity(t) for t in record['aggregate']['plan']['tasks']}
        result={}
        for r in sorted(rows, key=lambda row: row['id']):
            if r['id'] not in current:continue
            meta=json.loads(r['metadata']);exe=None if r['execution'] is None else json.loads(r['execution'])
            if meta['sprint_id'] != sprint_id:continue
            if r['status'] == 'newborn':
                newborn = meta['newborn']
                draft = newborn['draft']
                result[r['id']] = {
                    'status':'newborn','version':r['version'],'goal':draft.get('goal',''),
                    'goal_type':draft.get('goal_type'),'stage':'newborn','iteration':1,
                    'claimed_by':r['claimed_by'],
                    'result_commit':None,'worktree':None if exe is None else exe['worktree'],
                    'pending':None if exe is None else exe['pending'],
                    'publication':None if exe is None else exe['publication'],
                    'worktree_owner':r['worktree_owner'],
                    '_execution_branch':None if exe is None else exe['branch'],
                    'ready':newborn['ready'],
                    '_newborn_draft':draft,
                    '_newborn_workflow':meta.get('workflow'),
                }
                continue
            report=None if exe is None else exe['last_report']
            result[r['id']]={'status':r['status'],'version':r['version'],'goal':meta['goal'],'goal_type':meta['contract']['goal_type'],
                'stage':meta['workflow']['stages'][r['stage_index']]['id'],'iteration':r['iteration'],
                'claimed_by':r['claimed_by'],'result_commit':None if report is None else report['commit'],
                'worktree':None if exe is None else exe['worktree'],
                'pending':None if exe is None else exe['pending'],
                'publication':None if exe is None else exe['publication'],
                'worktree_owner':r['worktree_owner'],
                '_execution_branch':None if exe is None else exe['branch']}
        return result

    def add_draft_member(self, sprint_id, task_id):
        self.db.execute(
            'INSERT INTO sprint_members VALUES(?,?)', (sprint_id, task_id)
        )

    def remove_draft_member(self, sprint_id, task_id):
        self.db.execute(
            'DELETE FROM sprint_members WHERE sprint_id=? AND task_id=?',
            (sprint_id, task_id),
        )

    def published_ids(self,project):
        return [row['id'] for row in self.db.execute(
            "SELECT s.id FROM sprints s WHERE s.project=? AND (s.state='published' OR "
            "(s.state='cancelled' AND EXISTS (SELECT 1 FROM sprint_members m WHERE m.sprint_id=s.id))) ORDER BY s.id",
            (project,))]

    def history(self,sprint_id):
        return [{'revision':r[0],'data':json.loads(r[1]),'at':r[2]} for r in self.db.execute(
            'SELECT revision,data,at FROM sprint_layers WHERE sprint_id=? ORDER BY revision',(sprint_id,))]
