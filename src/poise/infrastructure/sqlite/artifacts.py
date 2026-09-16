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
            if item['scope'] == 'sprint':
                self._link_sprint(item)

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
