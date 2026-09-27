"""Registered artifact references and audited path recovery; no lifecycle writes."""
from datetime import datetime, timezone
import json
from ...common import PoiseError, encoded


class SqliteArtifactRepository:
    def __init__(self, db):
        self.db = db

    def records(self, task_id):
        return [dict(r) for r in self.db.execute(
            'SELECT a.id,a.owner,a.scope,a.path,a.digest FROM artifacts a '
            'JOIN task_artifacts t ON a.id=t.artifact_id WHERE t.task_id=? ORDER BY a.id',
            (task_id,))]

    def link_task(self, task_id, artifacts):
        for item in artifacts:
            self._register(item)
            self.db.execute('INSERT OR IGNORE INTO task_artifacts(task_id,artifact_id) VALUES(?,?)',
                            (task_id,item['id']))
            if '_draft_previous_digest' in item:
                self._record_revision(task_id,item)
            if item['scope'] == 'sprint':
                self._link_sprint(item)

    def _record_revision(self, task_id, item):
        previous = item['_draft_previous_digest']
        if previous is not None:
            changed = self.db.execute(
                'UPDATE artifacts SET digest=? WHERE id=? AND digest=?',
                (item['digest'],item['id'],previous))
            if changed.rowcount != 1:
                raise PoiseError('Artifact draft digest changed before revision was recorded')
        payload = {
            'artifact_id':item['id'],'scope':item['scope'],'path':item['relative_path'],
            'previous_digest':previous,'digest':item['digest'],
            'stage':item['_draft_stage'],'iteration':item['_draft_iteration'],
        }
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),item['_draft_actor'],task_id,
             'artifact_draft.revised',encoded(payload)))

    def drafts(self, task_id):
        rows = self.db.execute(
            "SELECT event,data FROM journal WHERE task_id=? AND event LIKE 'artifact_draft.%' ORDER BY seq",
            (task_id,)).fetchall()
        current = {}
        for row in rows:
            event = row['event']; data = json.loads(row['data'])
            if event == 'artifact_draft.declared':
                for item in data['drafts']:
                    current[item['artifact_id']] = {**item,'digest':None,'state':'open','snapshots':[]}
            elif event == 'artifact_draft.recovered':
                item = {key:data[key] for key in ('artifact_id','scope','path')}
                current[item['artifact_id']] = {
                    **item,'digest':data['baseline_digest'],'state':'open',
                    'snapshots':[{'digest':data['baseline_digest'],
                                  'snapshot_path':data['snapshot_path']}],
                }
            elif event == 'artifact_draft.revised' and data['artifact_id'] in current:
                current[data['artifact_id']]['digest'] = data['digest']
            elif event == 'artifact_draft.reviewed' and data['artifact_id'] in current:
                current[data['artifact_id']]['snapshots'].append({
                    'digest':data['digest'],'snapshot_path':data['snapshot_path']})
            elif event == 'artifact_draft.finalized':
                for identifier in data['artifact_ids']:
                    if identifier in current: current[identifier]['state'] = 'finalized'
        return current

    def draft_request(self, task_id, request_id, request_digest):
        row = self.db.execute(
            "SELECT data FROM journal WHERE task_id=? AND event IN "
            "('artifact_draft.declared','artifact_draft.recovered','artifact_draft.reviewed','artifact_draft.finalized') "
            "AND json_extract(data,'$.request_id')=? ORDER BY seq LIMIT 1",
            (task_id,request_id)).fetchone()
        if row is None:return None
        data=json.loads(row['data'])
        if data['request_digest'] != request_digest:
            raise PoiseError('Artifact draft request conflict')
        return data['result']

    def append_draft_event(self, task_id, actor, event, data):
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),actor,task_id,event,encoded(data)))

    def link_reused(self, task_id, artifacts):
        """Reuse may share identical Sprint files, never conflicting registry data."""
        for item in artifacts:
            old = self.db.execute(
                'SELECT id,owner,scope,path,digest FROM artifacts WHERE id=?',
                (item['id'],)).fetchone()
            if old is not None and any(old[key] != item[key] for key in old.keys()):
                raise PoiseError('Artifact reuse conflicts with an existing registered identity')
        self.link_task(task_id, artifacts)

    def _register(self, item):
        self.db.execute('INSERT OR IGNORE INTO artifacts(id,owner,scope,path,digest) VALUES(?,?,?,?,?)',
                        (item['id'],item['owner'],item['scope'],item['path'],item['digest']))

    def _link_sprint(self, item):
        self.db.execute('INSERT OR IGNORE INTO sprint_artifacts(sprint_id,artifact_id) VALUES(?,?)',
                        (item['owner'],item['id']))

    def link_sprint(self, artifacts):
        for item in artifacts:
            self._register(item)
            self._link_sprint(item)

    def recovery_snapshot(self, intent, digest):
        saved = self.db.execute(
            "SELECT data FROM journal WHERE task_id=? AND event='artifacts.recovered' "
            "AND json_extract(data,'$.request_id')=? ORDER BY seq DESC LIMIT 1",
            (intent.task_id,intent.request_id)).fetchone()
        if saved is not None:
            prior = json.loads(saved['data'])
            if prior['digest'] != digest:
                raise PoiseError('Artifact recovery request conflict')
            return {'replay': prior['result']}
        row = self.db.execute('SELECT id,version,claimed_by,metadata FROM tasks WHERE id=?',
                              (intent.task_id,)).fetchone()
        if row is None:
            raise PoiseError('Artifact recovery Task is not registered')
        if row['version'] != intent.expected_version:
            raise PoiseError('Artifact recovery Task version changed')
        if row['claimed_by'] is not None:
            raise PoiseError('Artifact recovery requires a released Task claim')
        sprint = json.loads(row['metadata'])['sprint_id']
        available = {r['id']:r for r in self.records(intent.task_id)}
        if any(identifier not in available for identifier in intent.artifact_ids):
            raise PoiseError('Artifact is not registered to selected Task')
        records = [available[identifier] for identifier in intent.artifact_ids]
        owners = {'task': intent.task_id}
        if sprint is not None:
            owners['sprint'] = sprint
        for record in records:
            if record['scope'] not in owners or record['owner'] != owners[record['scope']]:
                raise PoiseError('Registered artifact owner/scope does not match selected Task')
            live = self.db.execute(
                'SELECT t.id FROM task_artifacts a JOIN tasks t ON t.id=a.task_id '
                'WHERE a.artifact_id=? AND t.claimed_by IS NOT NULL LIMIT 1',
                (record['id'],)).fetchone()
            if live is not None:
                raise PoiseError('Shared artifact has an unreleased Task claim')
        return {'records': records, 'sprint_id': sprint, 'owners': owners}

    def rebind(self, intent, digest, records, publications, actor):
        if len(records) != len(publications):
            raise PoiseError('Artifact recovery publication set changed')
        for old, new in zip(records, publications, strict=True):
            if any(new[key] != old[key] for key in ('id','owner','scope','digest')):
                raise PoiseError('Artifact recovery destination identity changed')
            update = self.db.execute(
                'UPDATE artifacts SET path=? WHERE id=? AND owner=? AND scope=? AND path=? AND digest=?',
                (new['path'],old['id'],old['owner'],old['scope'],old['path'],old['digest']))
            if update.rowcount != 1:
                raise PoiseError('Registered artifact changed before rebind')
        result = {'status':'artifacts_recovered','task':intent.task_id,
                  'request_id':intent.request_id,'task_version':intent.expected_version,
                  'artifacts':publications}
        self.db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),actor,intent.task_id,'artifacts.recovered',
             encoded({'request_id':intent.request_id,'digest':digest,'result':result,
                      'reason':intent.reason,'authorization':intent.authorization,
                      'source_roots':intent.source_roots,'prior_records':records})))
        return result
