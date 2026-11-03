"""Registered artifact references and audited path recovery; no lifecycle writes."""
from datetime import datetime, timezone
import json
from contextlib import nullcontext
from ...common import PoiseError, encoded


class SqliteArtifactRepository:
    def __init__(self, db):
        self.db = db

    def records(self, task_id):
        return [dict(r) for r in self.db.execute(
            'SELECT a.id,a.owner,a.scope,a.path,a.digest FROM artifacts a '
            'JOIN task_artifacts t ON a.id=t.artifact_id WHERE t.task_id=? ORDER BY a.id',
            (task_id,))]

    def bind_materials(self, task_id, receipt_id, bindings, actor):
        value = {'schema': 'artifact-material-bindings-1', 'receipt_id': receipt_id,
                 'bindings': bindings}
        row = self.db.execute("SELECT data FROM journal WHERE task_id=? "
                              "AND event='artifact.materials_bound' "
                              "AND json_extract(data,'$.receipt_id')=? ORDER BY seq DESC LIMIT 1",
                              (task_id, receipt_id)).fetchone()
        if row is not None:
            if json.loads(row['data']) != value:
                raise PoiseError('Saved receipt material association conflicts')
            return
        self.append_draft_event(task_id, actor, 'artifact.materials_bound', value)

    def internal_material_ids(self, task_id):
        """Bound evidence remains registered without becoming a delivered result."""
        bound = set()
        for row in self.db.execute(
                "SELECT data FROM journal WHERE task_id=? AND event='artifact.materials_bound'",
                (task_id,)):
            bound.update(item['artifact_id'] for item in json.loads(row['data'])['bindings'])
        delivered = set()
        for row in self.db.execute('SELECT data FROM task_results WHERE task_id=?', (task_id,)):
            delivered.update(item['id'] for item in json.loads(row['data'])['artifacts'])
        return bound - delivered

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

    def record(self, artifact_id):
        row = self.db.execute('SELECT id,owner,scope,path,digest FROM artifacts WHERE id=?',
                              (artifact_id,)).fetchone()
        return None if row is None else dict(row)

    def register_materials(self, records):
        """Permanent user attachments are registered, without becoming stage proofs."""
        for item in records:
            old = self.record(item['id'])
            if old is not None and any(old[key] != item[key] for key in old):
                raise PoiseError('Material artifact identity conflict; retry the original request')
            self._register(item)

    def _link_sprint(self, item):
        self.db.execute('INSERT OR IGNORE INTO sprint_artifacts(sprint_id,artifact_id) VALUES(?,?)',
                        (item['owner'],item['id']))

    def link_sprint(self, artifacts):
        for item in artifacts:
            self._register(item)
            self._link_sprint(item)

    def recovery_request(self, task_id, request_id, digest):
        saved = self.db.execute(
            "SELECT data FROM journal WHERE task_id=? AND event='artifacts.recovered' "
            "AND json_extract(data,'$.request_id')=? ORDER BY seq DESC LIMIT 1",
            (task_id,request_id)).fetchone()
        if saved is not None:
            prior = json.loads(saved['data'])
            if prior['digest'] != digest:
                raise PoiseError('Artifact recovery request conflict')
            return prior['result']
        return None

    def recovery_snapshot(self, intent, digest):
        replay = self.recovery_request(intent.task_id, intent.request_id, digest)
        if replay is not None:
            return {'replay': replay}
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

    def recovery_sessions(self, after_sequence):
        return {row['session_id'] for row in self.db.execute(
            "SELECT session_id FROM journal WHERE seq>? AND event='artifacts.recovered'",
            (after_sequence,))}

    def relocation_records(self, after_sequence):
        """Undo only recorded, identity-preserving rebinds for admission comparison."""
        records = {row['id']: dict(row) for row in self.db.execute(
            'SELECT id,owner,scope,path,digest FROM artifacts')}
        for row in self.db.execute(
                "SELECT data FROM journal WHERE seq>? AND event='artifacts.recovered' ORDER BY seq DESC",
                (after_sequence,)):
            event = json.loads(row['data'])
            prior = event['prior_records']
            published = event['result']['artifacts']
            if len(prior) != len(published):
                raise PoiseError('Artifact recovery history has inconsistent records')
            for old, new in zip(prior, published, strict=True):
                # Public recovery also carries transport source/relative paths;
                # only the existing registry columns describe stored identity.
                columns = ('id', 'owner', 'scope', 'path', 'digest')
                old = {key: old[key] for key in columns}
                new = {key: new[key] for key in columns}
                if (any(old[key] != new[key] for key in ('id', 'owner', 'scope', 'digest'))
                        or records.get(new['id']) != new):
                    raise PoiseError('Artifact recovery history does not prove current placement')
                records[old['id']] = old
        return list(records.values())

    def normalize_relocation(self, before, actual, database, actor):
        """Authorize only this owner's exact binding/recovery effects for a seal.

        This changes the temporary comparison, never stored records or history.
        All other rows, implicit row identities and database metadata remain exact.
        """
        from ..saved_materials import SavedMaterialReader, material_specs, saved_receipts
        original, current = before['tables'], actual['tables']
        sequence = max((row['seq'] for row in original['journal']), default=0)
        appended = [row for row in current['journal'] if row['seq'] > sequence]
        task_versions = {row['id']: row['version'] for row in original['tasks']}
        allowed_artifacts, task_links, sprint_links = set(), set(), set()
        records = {row['id']: row for row in current['artifacts']}
        reader = SavedMaterialReader(database, read_transaction=lambda: nullcontext(self.db))
        for row in appended:
            task_id = row['task_id']
            if task_id not in task_versions or row['event'] not in (
                    'artifacts.recovered', 'artifact.materials_bound'):
                raise PoiseError('Destination history has an unconfirmed relocation event')
            value = json.loads(row['data'])
            if row['event'] == 'artifacts.recovered':
                result = value['result']
                if result['task'] != task_id or result['task_version'] != task_versions[task_id]:
                    raise PoiseError('Artifact recovery history has a different Task/version')
                continue
            if row['session_id'] != actor or value['schema'] != 'artifact-material-bindings-1':
                raise PoiseError('Material binding is outside the admitted relocation')
            receipts = [receipt for receipt in saved_receipts(self.db, task_id)
                        if receipt['id'] == value['receipt_id']]
            if len(receipts) != 1:
                raise PoiseError('Relocation binding has no unique saved receipt')
            specs = material_specs(reader.physical_receipt(receipts[0]))
            bindings = {item['selector']: item['artifact_id'] for item in value['bindings']}
            if len(bindings) != len(value['bindings']) or set(bindings) != set(specs):
                raise PoiseError('Relocation binding differs from complete receipt materials')
            for selector, identifier in bindings.items():
                record = records[identifier]
                if any(record[key] != specs[selector][key] for key in ('path', 'digest')):
                    raise PoiseError('Relocation binding has different physical material')
                allowed_artifacts.add(identifier)
                task_links.add((task_id, identifier))
                if record['scope'] == 'sprint':
                    sprint_links.add((record['owner'], identifier))
        restored = {row['id']: row for row in self.relocation_records(sequence)}
        admitted_ids = {row['id'] for row in original['artifacts']}
        for row in current['artifacts']:
            row.update(restored[row['id']])
        current['artifacts'] = [row for row in current['artifacts']
                                if row['id'] in admitted_ids or row['id'] not in allowed_artifacts]
        for table, owner, permitted in (
                ('task_artifacts', 'task_id', task_links),
                ('sprint_artifacts', 'sprint_id', sprint_links)):
            admitted = {(row[owner], row['artifact_id']) for row in original[table]}
            current[table] = [row for row in current[table]
                              if (row[owner], row['artifact_id']) in admitted
                              or (row[owner], row['artifact_id']) not in permitted]
        # The only allowed sequence change is allocation of those checked append
        # events. Preserve the row's own identity and every unrelated sequence.
        maximum = max((row['seq'] for row in current['journal']), default=0)
        prior_sequence = next((row for row in original['sqlite_sequence']
                               if row['name'] == 'journal'), None)
        for row in current['sqlite_sequence']:
            if row['name'] == 'journal' and appended:
                if row['seq'] != maximum:
                    raise PoiseError('Destination journal allocation has unconfirmed changes')
                if prior_sequence is not None:
                    row['seq'] = prior_sequence['seq']
        if prior_sequence is None and appended:
            current['sqlite_sequence'] = [row for row in current['sqlite_sequence']
                                         if row['name'] != 'journal']
        current['journal'] = [row for row in current['journal'] if row['seq'] <= sequence]

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
