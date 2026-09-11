"""Accounting-owned rows. Task/Sprint and messages are read through projections only."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from uuid import uuid4
from ...common import PoiseError,encoded
from ...modules.accounting.domain import UsageSample,usage_contribution,identity
from .queries import TaskQueries


def timestamp(text):
    try:
        dt=datetime.fromisoformat(text)
        if dt.tzinfo is None:raise ValueError('timezone')
        return dt.astimezone(timezone.utc)
    except (TypeError,ValueError) as exc:raise PoiseError('Accounting timestamp requires ISO datetime with timezone') from exc


def binding(task):
    if task is None:return {'task':None,'sprint':None,'goal_type':None,'stage':None,'iteration':None}
    return {'task':task['id'],'sprint':task['sprint_id'],'goal_type':task['contract']['goal_type'],
            'stage':task['process']['stages'][task['stage_index']]['id'],'iteration':task['iteration']}


class SqliteAccounting:
    def __init__(self,database,project,policy):
        self.database,self.project,self.policy=database,project,policy

    def baseline(self,tid):
        with self.database.transaction() as db:
            r=db.execute('SELECT data FROM accounting_accounts WHERE task_id=?',(tid,)).fetchone()
            return None if r is None else json.loads(r[0])

    def ensure_account(self,task,baseline,at):
        with self.database.transaction() as db:
            r=db.execute('SELECT data FROM accounting_accounts WHERE task_id=?',(task['id'],)).fetchone()
            if r is not None:
                if json.loads(r[0])!=baseline:raise PoiseError('Accounting baseline is immutable')
                return
            db.execute('INSERT INTO accounting_accounts VALUES(?,?,?,?)',(task['id'],self.project,at,encoded(baseline)))

    def ingest(self,prepared,session,task):
        at_bind=binding(task);p=self.policy.data
        with self.database.transaction() as db:
            # Sort within the explicit batch. A late novel historical sample is not guessed.
            for event in sorted(prepared['usage'],key=lambda e:(e.data['source'],e.data['stream'],e.data['sequence'])):
                d=event.data;timestamp(d['occurred_at'])
                old=db.execute('SELECT data FROM accounting_usage WHERE id=?',(event.key,)).fetchone()
                if old is not None:
                    if json.loads(old[0])['sample']!=d:raise PoiseError('Usage identity has conflicting content')
                    continue
                prev=db.execute('SELECT data FROM accounting_usage WHERE source=? AND stream=? ORDER BY sequence DESC LIMIT 1',(d['source'],d['stream'])).fetchone()
                previous=None if prev is None else UsageSample.parse(json.loads(prev[0])['sample'],self.policy)
                if previous is not None:
                    if previous.data['sequence']>=d['sequence']:raise PoiseError('Out-of-order usage requires an explicit ordered source batch')
                    if timestamp(previous.data['occurred_at'])>timestamp(d['occurred_at']):raise PoiseError('Usage timestamp went backwards')
                delta=usage_contribution(previous,event)
                record={'sample':d,'contribution':delta,'binding':at_bind,'cause':prepared['cause'],
                        'source_mode':p['sources'][d['source']],'session':session,
                        'finding_ids':[t['finding_id'] for t in prepared['finding_targets']]}
                db.execute('INSERT INTO accounting_usage VALUES(?,?,?,?,?,?,?,?)',
                   (event.key,at_bind['task'],self.project,d['source'],d['stream'],d['sequence'],d['occurred_at'],encoded(record)))
            for entry in prepared['intervals']:
                a,b=timestamp(entry['started_at']),timestamp(entry['ended_at'])
                if b<a:raise PoiseError('Time interval ends before it starts')
                eid=identity([entry['source'],entry['stream'],entry['event_id']])
                old=db.execute('SELECT data FROM accounting_cycles WHERE id=?',(eid,)).fetchone()
                if old is not None:
                    if json.loads(old[0])['event']!=entry:raise PoiseError('Time identity conflict')
                    continue
                for row in db.execute('SELECT started_at,ended_at,data FROM accounting_cycles WHERE project=?',(self.project,)):
                    prior=json.loads(row['data'])
                    if prior['kind']=='reported' and prior['event']['source']==entry['source'] and prior['event']['stream']==entry['stream']:
                        if max(a,timestamp(row['started_at']))<min(b,timestamp(row['ended_at'])):raise PoiseError('Overlapping reported intervals in one stream')
                data={'kind':'reported','event':entry,'binding':at_bind,'cause':prepared['cause'],
                      'finding_ids':[t['finding_id'] for t in prepared['finding_targets']],
                      'seconds':(b-a).total_seconds(),'closed_by':'source'}
                db.execute('INSERT INTO accounting_cycles VALUES(?,?,?,?,?,?,?)',
                    (eid,at_bind['task'],self.project,session,a.isoformat(),b.isoformat(),encoded(data)))
            for target in prepared['finding_targets']:
                if task is None:raise PoiseError('Finding attribution requires current task')
                row=db.execute('SELECT data FROM task_workflows WHERE task_id=?',(task['id'],)).fetchone()
                feedback=json.loads(row[0])['feedback']
                finding=next((x for x in feedback['findings'] if x['id']==target['finding_id']),None)
                if finding is None:raise PoiseError('Unknown finding for cost attribution')
                delivered=db.execute('SELECT at FROM interaction_reports WHERE task_id=? AND stage=? AND iteration=?',
                    (task['id'],target['stage'],target['iteration'])).fetchone()
                if delivered is None or (finding['stage'],finding['iteration'])==(target['stage'],target['iteration']):
                    raise PoiseError('Quality finding must target a previously delivered iteration')
                old=db.execute('SELECT data FROM accounting_findings WHERE task_id=? AND finding_id=?',(task['id'],target['finding_id'])).fetchone()
                if old is not None and json.loads(old[0])!=target:raise PoiseError('Finding attribution cannot silently change')
                db.execute('INSERT OR IGNORE INTO accounting_findings VALUES(?,?,?)',(task['id'],target['finding_id'],encoded(target)))
            if prepared['cause'] is not None:
                current=db.execute('SELECT id,data FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
                if current is not None:
                    data=json.loads(current['data'])
                    if data['cause'] is None:
                        data['cause']=prepared['cause'];data['finding_ids']=[t['finding_id'] for t in prepared['finding_targets']]
                        db.execute('UPDATE accounting_cycles SET data=? WHERE id=?',(encoded(data),current['id']))
                    elif data['cause']!=prepared['cause']:raise PoiseError('A work cycle already has an explicit cause')

    def start(self,session,task,at,turn_id):
        with self.database.transaction() as db:
            r=db.execute('SELECT task_id FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if r is not None:
                if r[0]!=task['id']:raise PoiseError('Finish current accounting cycle before changing task')
                return
            data={'kind':'tool_cycle','event':None,'binding':binding(task),'cause':None,'finding_ids':[], 'seconds':None,'closed_by':None,'turn_id':turn_id,'last_observed_at':at}
            db.execute('INSERT INTO accounting_cycles VALUES(?,?,?,?,?,?,?)',(str(uuid4()),task['id'],self.project,session,at,None,encoded(data)))

    def new_turn(self,session,turn_id):
        if turn_id is None:return
        with self.database.transaction() as db:
            row=db.execute('SELECT * FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if row is None:return
            d=json.loads(row['data'])
            if d['turn_id'] is None:
                d['turn_id']=turn_id
                db.execute('UPDATE accounting_cycles SET data=? WHERE id=?',(encoded(d),row['id']))
            elif d['turn_id']!=turn_id:
                at=d['last_observed_at'];elapsed=(timestamp(at)-timestamp(row['started_at'])).total_seconds()
                if elapsed<0:raise PoiseError('Invalid observed interval')
                d['seconds']=elapsed;d['closed_by']='next_user_turn_at_last_observation'
                db.execute('UPDATE accounting_cycles SET ended_at=?,data=? WHERE id=?',(at,encoded(d),row['id']))

    def touch(self,session,at):
        with self.database.transaction() as db:
            row=db.execute('SELECT id,started_at,data FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if row is None:return
            d=json.loads(row['data'])
            if timestamp(at)<timestamp(d['last_observed_at']):raise PoiseError('Clock moved backwards')
            d['last_observed_at']=at
            db.execute('UPDATE accounting_cycles SET data=? WHERE id=?',(encoded(d),row['id']))

    def stop(self,session,at):
        with self.database.transaction() as db:
            r=db.execute('SELECT * FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if r is None:return
            elapsed=(timestamp(at)-timestamp(r['started_at'])).total_seconds()
            if elapsed<0:raise PoiseError('Clock moved backwards; duration not invented')
            d=json.loads(r['data']);d['seconds']=elapsed;d['closed_by']='tool_result_returned'
            db.execute('UPDATE accounting_cycles SET ended_at=?,data=? WHERE id=?',(at,encoded(d),r['id']))

    def snapshot(self):
        with self.database.transaction() as db:
            accounts=[dict(r) for r in db.execute('SELECT * FROM accounting_accounts WHERE project=?',(self.project,))]
            tasks={r['id']:TaskQueries.record_in(db,r['id']) for r in db.execute('SELECT id FROM tasks')}
            return {'accounts':accounts,'tasks':tasks,
                'usage':[dict(r) for r in db.execute('SELECT * FROM accounting_usage WHERE project=?',(self.project,))],
                'cycles':[dict(r) for r in db.execute('SELECT * FROM accounting_cycles WHERE project=?',(self.project,))],
                'credits':[dict(r) for r in db.execute('SELECT * FROM accounting_credits WHERE project=? ORDER BY seq',(self.project,))],
                'messages':[dict(r) for r in db.execute('SELECT e.*,b.task_id,b.sprint_id,b.goal_type,b.stage,b.iteration FROM interaction_events e LEFT JOIN interaction_bindings b ON b.event_id=e.id WHERE e.project=?',(self.project,))],
                'reports':[dict(r) for r in db.execute('SELECT * FROM interaction_reports')],
                'quality':[dict(r) for r in db.execute('SELECT * FROM accounting_findings')],
                'workflows':{r['task_id']:json.loads(r['data']) for r in db.execute('SELECT * FROM task_workflows')}}

    def latest_credit(self,tid):
        with self.database.transaction() as db:
            row=db.execute('SELECT data FROM accounting_credits WHERE task_id=? ORDER BY seq DESC LIMIT 1',(tid,)).fetchone()
            return None if row is None else json.loads(row[0])

    def credit(self,task,measurement,at):
        marker=identity([task['id'],task['_version'],task['status']])
        with self.database.transaction() as db:
            if db.execute('SELECT 1 FROM accounting_credits WHERE id=?',(marker,)).fetchone():return
            version=db.execute('SELECT version FROM tasks WHERE id=?',(task['id'],)).fetchone()[0]
            if version!=task['_version']:raise PoiseError('Task changed during result measurement')
            row=db.execute('SELECT data FROM accounting_credits WHERE task_id=? ORDER BY seq DESC LIMIT 1',(task['id'],)).fetchone()
            previous=None if row is None else json.loads(row[0])
            state=task['status'] if task['status'] in ('completed','cancelled','superseded') else 'open'
            if previous is not None and previous['state']==state and previous['measurement']==measurement:return
            if previous is None and state=='open':return
            fields=('changed_lines','changed_bytes','changed_tokens')
            delta={k: (measurement[k] if previous is None else (None if measurement[k] is None or previous['measurement'][k] is None else measurement[k]-previous['measurement'][k])) for k in fields}
            data={'marker':marker,'state':state,'binding':binding(task),'measurement':measurement,'delta':delta}
            db.execute('INSERT INTO accounting_credits(id,task_id,project,at,data) VALUES(?,?,?,?,?)',(marker,task['id'],self.project,at,encoded(data)))


def check_accounting_import(db,tables):
    for table in ('accounting_usage','accounting_cycles'):
        for row in tables[table]:
            if db.execute(f'SELECT 1 FROM {table} WHERE id=?',(row['id'],)).fetchone():
                raise PoiseError('Accounting event already exists in destination; no duplicate merge')
    for row in tables['accounting_cycles']:
        if row['ended_at'] is None:raise PoiseError('Finish or hand off the open work cycle before transfer')


def restore_accounting(db,tables):
    from .transfer_records import ACCOUNTING_TABLES,insert
    check_accounting_import(db,tables)
    for table in ACCOUNTING_TABLES:
        for original in tables[table]:
            row=dict(original)
            if table=='accounting_credits':row.pop('seq')
            insert(db,table,row)
