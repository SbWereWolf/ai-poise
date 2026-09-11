"""Sprint-owned writes. Uses the existing externally locked SQLite transaction."""
import json
from datetime import datetime, timezone
from ...modules.foundation.errors import HarnessError, VersionConflict
from ...modules.tasks.allocation import creation_alias
from .tasks import encode


class SqliteSprintRepository:
    def __init__(self, connection):self.db=connection

    def restore_snapshot(self,tables,binding):
        from .transfer_records import restore_sprints
        if any(self.get(row['id']) is not None for row in tables['sprints']):
            raise HarnessError('Sprint snapshot cannot replace an existing aggregate')
        restore_sprints(self.db,tables,binding)

    def get(self,sprint_id):
        row=self.db.execute('SELECT data FROM sprints WHERE id=?',(sprint_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def save(self,record,expected_revision):
        s=record['aggregate'];sid=s['plan']['id'];version=s['revision']
        prior=self.get(sid)
        if expected_revision is None:
            if prior is not None:raise VersionConflict('Sprint already exists')
            if self.db.execute('SELECT 1 FROM tasks WHERE id=?',(sid,)).fetchone():raise HarnessError('Task/sprint ID collision')
            self.db.execute('INSERT INTO sprints VALUES(?,?,?,?,?)',(sid,record['project'],s['state'],version,encode(record)))
        else:
            if prior is None or prior['aggregate']['revision']!=expected_revision or version!=expected_revision+1:
                raise VersionConflict('Sprint revision changed; no overwrite')
            self.db.execute('UPDATE sprints SET state=?,revision=?,data=? WHERE id=? AND revision=?',
                            (s['state'],version,encode(record),sid,expected_revision))
        self.db.execute('INSERT INTO sprint_layers VALUES(?,?,?,?)',(sid,version,encode(record),datetime.now(timezone.utc).isoformat()))
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                        (datetime.now(timezone.utc).isoformat(),record['actor'],None,'sprint.changed',
                         encode({'sprint':sid,'revision':version,'state':s['state']})))

    def publish_members(self,record):
        plan=record['aggregate']['plan'];sid=plan['id']
        self.db.executemany('INSERT INTO sprint_members VALUES(?,?)',[(sid,t['id']) for t in plan['tasks']])
        self.db.executemany('INSERT INTO sprint_dependencies VALUES(?,?,?,?)',
            [(sid,e['predecessor'],e['successor'],e['kind']) for e in plan['dependencies']])

    def replace_dependencies(self,record):
        plan=record['aggregate']['plan'];sid=plan['id']
        self.db.execute('DELETE FROM sprint_dependencies WHERE sprint_id=?',(sid,))
        self.db.executemany('INSERT INTO sprint_dependencies VALUES(?,?,?,?)',
            [(sid,e['predecessor'],e['successor'],e['kind']) for e in plan['dependencies']])

    def add_replacement_member(self,record,replacement):
        plan=record['aggregate']['plan'];sid=plan['id']
        self.db.execute('INSERT INTO sprint_members VALUES(?,?)',(sid,replacement))
        self.replace_dependencies(record)

    def receipt(self,sprint_id,request_id,digest):
        row=self.db.execute('SELECT digest,data FROM sprint_requests WHERE sprint_id=? AND request_id=?',(sprint_id,request_id)).fetchone()
        if row is None:return None
        if row[0]!=digest:raise HarnessError('Request ID already used with another sprint intent')
        return json.loads(row[1])

    def remember(self,sprint_id,request_id,digest,result):
        self.db.execute('INSERT INTO sprint_requests VALUES(?,?,?,?)',(sprint_id,request_id,digest,encode(result)))

    def bind(self,session,sprint_id):
        self.db.execute('INSERT INTO session_sprints VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET sprint_id=excluded.sprint_id',(session,sprint_id))

    def scope(self,session):
        row=self.db.execute('SELECT sprint_id FROM session_sprints WHERE session_id=?',(session,)).fetchone()
        return None if row is None else row[0]

    def facts(self,sprint_id):
        record=self.get(sprint_id)
        if record is None:return {}
        current={creation_alias(t) for t in record['aggregate']['plan']['tasks']}
        rows=self.db.execute('SELECT t.id,t.status,t.claimed_by,t.stage_index,t.iteration,t.metadata,e.data AS execution, '
            "EXISTS(SELECT 1 FROM handoffs h WHERE h.task_id=t.id AND h.state='released') AS handoff_available "
            'FROM sprint_members m JOIN tasks t ON t.id=m.task_id '
            'LEFT JOIN task_execution e ON e.task_id=t.id WHERE m.sprint_id=? ORDER BY t.id',(sprint_id,)).fetchall()
        result={}
        for r in rows:
            if r['id'] not in current:continue
            meta=json.loads(r['metadata']);exe=None if r['execution'] is None else json.loads(r['execution'])
            report=None if exe is None else exe['last_report']
            result[r['id']]={'status':r['status'],'goal':meta['goal'],'goal_type':meta['contract']['goal_type'],
                'stage':meta['process']['stages'][r['stage_index']]['id'],'iteration':r['iteration'],
                'claimed_by':r['claimed_by'],'handoff_available':bool(r['handoff_available']),'result_commit':None if report is None else report['commit'],
                'worktree':None if exe is None else exe['worktree'],
                'pending':None if exe is None else exe['pending']}
        return result

    def published_ids(self,project):
        return [row['id'] for row in self.db.execute(
            "SELECT s.id FROM sprints s WHERE s.project=? AND (s.state='published' OR "
            "(s.state='cancelled' AND EXISTS (SELECT 1 FROM sprint_members m WHERE m.sprint_id=s.id))) ORDER BY s.id",
            (project,))]

    def history(self,sprint_id):
        return [{'revision':r[0],'data':json.loads(r[1]),'at':r[2]} for r in self.db.execute(
            'SELECT revision,data,at FROM sprint_layers WHERE sprint_id=? ORDER BY revision',(sprint_id,))]
