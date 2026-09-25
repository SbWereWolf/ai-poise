"""Accounting-owned rows. Task/Sprint and messages are read through projections only."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from uuid import uuid4
from ...common import PoiseError,encoded
from ...modules.accounting.domain import UsageSample,usage_contribution,identity,parse_telemetry
from ...modules.accounting.clock import ClockObservation
from .queries import TaskQueries


def timestamp(text):
    try:
        dt=datetime.fromisoformat(text)
        if dt.tzinfo is None:raise ValueError('timezone')
        return dt.astimezone(timezone.utc)
    except (TypeError,ValueError) as exc:raise PoiseError('Accounting timestamp requires ISO datetime with timezone') from exc


def binding(task):
    if task is None:return {'task':None,'sprint':None,'goal_type':None,'stage':None,'iteration':None}
    if task['status'] == 'newborn':
        return {'task':task['id'],'sprint':task['sprint_id'],
                'goal_type':task.get('goal_type') or 'newborn',
                'stage':'newborn','iteration':1}
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

    def finding_context(self, task_id, targets):
        observations = []
        with self.database.transaction() as db:
            row = db.execute('SELECT data FROM task_workflows WHERE task_id=?', (task_id,)).fetchone()
            findings = [] if row is None else json.loads(row[0])['feedback']['findings']
            for target in targets:
                finding = next((item for item in findings if item['id'] == target['finding_id']), None)
                delivered = db.execute(
                    'SELECT 1 FROM interaction_reports WHERE task_id=? AND stage=? AND iteration=?',
                    (task_id, target['stage'], target['iteration'])).fetchone()
                observations.append({'target': dict(target),
                    'finding': None if finding is None else {
                        'stage': finding['stage'], 'iteration': finding['iteration']},
                    'delivered': delivered is not None})
        return {'schema': 'accounting-finding-context-1', 'task': task_id,
                'observations': observations}

    def ingest(self,prepared,session,task):
        return self.ingest_binding(prepared,session,binding(task))

    def ingest_binding(self,prepared,session,at_bind,persist_task_reference=True):
        with self.database.transaction() as db:
            return self._ingest_binding(db,prepared,session,at_bind,persist_task_reference)

    def _ingest_binding(self,db,prepared,session,at_bind,persist_task_reference):
        p=self.policy.data
        task_reference=at_bind['task'] if persist_task_reference else None
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
               (event.key,task_reference,self.project,d['source'],d['stream'],d['sequence'],d['occurred_at'],encoded(record)))
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
                (eid,task_reference,self.project,session,a.isoformat(),b.isoformat(),encoded(data)))
        for target in (prepared['finding_targets'] if persist_task_reference else ()):
            task_id=at_bind['task']
            if task_id is None:raise PoiseError('Finding attribution requires current task')
            row=db.execute('SELECT data FROM task_workflows WHERE task_id=?',(task_id,)).fetchone()
            feedback=json.loads(row[0])['feedback']
            finding=next((x for x in feedback['findings'] if x['id']==target['finding_id']),None)
            if finding is None:raise PoiseError('Unknown finding for cost attribution')
            delivered=db.execute('SELECT at FROM interaction_reports WHERE task_id=? AND stage=? AND iteration=?',
                (task_id,target['stage'],target['iteration'])).fetchone()
            if delivered is None or (finding['stage'],finding['iteration'])==(target['stage'],target['iteration']):
                raise PoiseError('Quality finding must target a previously delivered iteration')
            old=db.execute('SELECT data FROM accounting_findings WHERE task_id=? AND finding_id=?',(task_id,target['finding_id'])).fetchone()
            if old is not None and json.loads(old[0])!=target:raise PoiseError('Finding attribution cannot silently change')
            db.execute('INSERT OR IGNORE INTO accounting_findings VALUES(?,?,?)',(task_id,target['finding_id'],encoded(target)))
        if prepared['cause'] is not None:
            current=db.execute('SELECT id,data FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if current is not None:
                data=json.loads(current['data'])
                if data['cause'] is None:
                    data['cause']=prepared['cause'];data['finding_ids']=[t['finding_id'] for t in prepared['finding_targets']]
                    db.execute('UPDATE accounting_cycles SET data=? WHERE id=?',(encoded(data),current['id']))
                elif data['cause']!=prepared['cause']:raise PoiseError('A work cycle already has an explicit cause')


    def store_telemetry(self,envelope):
        with self.database.transaction() as db:
            return self._store_telemetry(db,envelope)

    def accept_telemetry(self,envelope):
        data=envelope.data
        for name in ('started','finished'):
            value=data[name]
            ClockObservation(value['audit_utc'],value['monotonic_ns'],value['comparison_domain'])
        raw=data['telemetry']
        prepared=None if raw is None else parse_telemetry(raw,self.policy)
        if prepared is not None:
            for event in prepared['usage']:timestamp(event.data['occurred_at'])
            for event in prepared['intervals']:
                if timestamp(event['ended_at'])<timestamp(event['started_at']):
                    raise PoiseError('Time interval ends before start')
        return self.process_telemetry(envelope,prepared)

    def process_telemetry(self,envelope,prepared):
        data=envelope.data
        selected=data['after_binding'] if data['after_binding']['task'] is not None else data['before_binding']
        with self.database.transaction() as db:
            if not self._store_telemetry(db,envelope):return False
            if prepared is not None:
                from ...modules.accounting.finding_context import validate_finding_context
                targets = validate_finding_context(
                    prepared['finding_targets'], selected['task'], data.get('finding_context'))
                self._require_same_finding_targets(db, selected['task'], targets)
                self._ingest_binding(db,prepared,data['session'],selected,False)
        return True

    def _require_same_finding_targets(self, db, task_id, targets):
        if not targets:
            return
        requested = {item['finding_id']: item for item in targets}
        rows = db.execute(
            "SELECT data FROM accounting_cycles WHERE project=? "
            "AND json_extract(data,'$.kind')='telemetry_envelope'", (self.project,))
        for row in rows:
            previous = json.loads(row[0])['envelope']
            bound = previous['after_binding'] if previous['after_binding']['task'] is not None else previous['before_binding']
            raw = previous.get('telemetry')
            if bound['task'] != task_id or raw is None:
                continue
            for old in raw['finding_targets']:
                current = requested.get(old['finding_id'])
                if current is not None and old != current:
                    raise PoiseError('Finding attribution cannot silently change')

    def _store_telemetry(self,db,envelope):
        data=envelope.data
        stored={'kind':'telemetry_envelope','envelope':data}
        old=db.execute('SELECT data FROM accounting_cycles WHERE id=?',(envelope.identity,)).fetchone()
        if old is not None:
            if json.loads(old[0])!=stored:raise PoiseError('Telemetry identity has conflicting content')
            return False
        db.execute(
            'INSERT INTO accounting_cycles VALUES(?,?,?,?,?,?,?)',
            (
                envelope.identity,
                None,
                self.project,
                data['session'],
                data['started']['audit_utc'],
                data['finished']['audit_utc'],
                encoded(stored),
            ),
        )
        return True

    @staticmethod
    def _new_timing(observation:ClockObservation):
        return {'version':2,'comparison_domain':observation.comparison_domain,
                'started_monotonic_ns':observation.monotonic_ns,'last_monotonic_ns':observation.monotonic_ns,
                'ended_monotonic_ns':None,'elapsed_microseconds':None,'status':'open'}

    @staticmethod
    def _unmeasured(data):
        old=data.get('timing')
        old=old if isinstance(old,dict) else {}
        data['timing']={'version':2,'comparison_domain':old.get('comparison_domain'),
            'started_monotonic_ns':old.get('started_monotonic_ns'),
            'last_monotonic_ns':old.get('last_monotonic_ns'),
            'ended_monotonic_ns':None,'elapsed_microseconds':None,'status':'unmeasured_clock_discontinuity'}
        data.pop('seconds',None);data['closed_by']='clock_discontinuity'

    @staticmethod
    def _advance(db,row,observation:ClockObservation,close:bool):
        data=json.loads(row['data']);timing=data.get('timing')
        valid=(isinstance(timing,dict) and timing.get('version')==2 and timing.get('status')=='open'
               and type(timing.get('started_monotonic_ns')) is int
               and type(timing.get('last_monotonic_ns')) is int
               and timing['started_monotonic_ns']>=0 and timing['last_monotonic_ns']>=0)
        if not valid or timing.get('comparison_domain')!=observation.comparison_domain:
            SqliteAccounting._unmeasured(data)
            db.execute('UPDATE accounting_cycles SET ended_at=?,data=? WHERE id=?',
                       (observation.audit_utc,encoded(data),row['id']))
            return 'Open accounting cycle cannot be compared safely; retry the operation'
        if timing['last_monotonic_ns']<timing['started_monotonic_ns']:
            return 'Persisted monotonic clock state is backwards; accounting state was not changed'
        if observation.monotonic_ns<timing['last_monotonic_ns']:
            return 'Monotonic clock moved backwards; accounting state was not changed'
        timing['last_monotonic_ns']=observation.monotonic_ns;data['last_observed_at']=observation.audit_utc
        if close:
            timing['ended_monotonic_ns']=observation.monotonic_ns
            timing['elapsed_microseconds']=(observation.monotonic_ns-timing['started_monotonic_ns'])//1000
            timing['status']='measured';data['closed_by']='tool_result_returned'
            db.execute('UPDATE accounting_cycles SET ended_at=?,data=? WHERE id=?',
                       (observation.audit_utc,encoded(data),row['id']))
        else:db.execute('UPDATE accounting_cycles SET data=? WHERE id=?',(encoded(data),row['id']))
        return None

    def start(self,session,task,at:ClockObservation,turn_id):
        with self.database.transaction() as db:
            r=db.execute('SELECT * FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if r is not None:
                if r['task_id']!=task['id']:raise PoiseError('Finish current accounting cycle before changing task')
                error=self._advance(db,r,at,False)
            else:
                error=None
                data={'kind':'tool_cycle','event':None,'binding':binding(task),'cause':None,'finding_ids':[],
                      'closed_by':None,'turn_id':turn_id,'last_observed_at':at.audit_utc,
                      'timing':self._new_timing(at)}
                db.execute('INSERT INTO accounting_cycles VALUES(?,?,?,?,?,?,?)',
                           (str(uuid4()),task['id'],self.project,session,at.audit_utc,None,encoded(data)))
        if error is not None:raise PoiseError(error)

    def new_turn(self,session,turn_id):
        if turn_id is None:return
        error=None
        with self.database.transaction() as db:
            row=db.execute('SELECT * FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if row is None:return
            d=json.loads(row['data'])
            if d['turn_id'] is None:
                d['turn_id']=turn_id
                db.execute('UPDATE accounting_cycles SET data=? WHERE id=?',(encoded(d),row['id']))
            elif d['turn_id']!=turn_id:
                error=self._close_at_last_observation(db,row,d,'next_user_turn_at_last_observation')
        if error is not None:raise PoiseError(error)

    @staticmethod
    def _close_at_last_observation(db,row,data,reason):
        timing=data.get('timing')
        valid=(isinstance(timing,dict) and timing.get('version')==2 and timing.get('status')=='open'
               and type(timing.get('started_monotonic_ns')) is int
               and type(timing.get('last_monotonic_ns')) is int
               and timing['started_monotonic_ns']>=0 and timing['last_monotonic_ns']>=0)
        if not valid:
            SqliteAccounting._unmeasured(data)
            error='Open accounting cycle cannot be compared safely; retry the operation'
        elif timing['last_monotonic_ns']<timing['started_monotonic_ns']:
            error='Monotonic clock moved backwards; accounting state was not changed'
        else:
            error=None
            timing['ended_monotonic_ns']=timing['last_monotonic_ns']
            timing['elapsed_microseconds']=(timing['last_monotonic_ns']-timing['started_monotonic_ns'])//1000
            timing['status']='measured'
            data['closed_by']=reason
        if error is None or data['timing']['status']=='unmeasured_clock_discontinuity':
            db.execute('UPDATE accounting_cycles SET ended_at=?,data=? WHERE id=?',
                       (data['last_observed_at'],encoded(data),row['id']))
        return error

    def release_cycle(self,session,task_id):
        """Release the legacy session lease using only already recorded observations."""
        with self.database.transaction() as db:
            row=db.execute(
                'SELECT * FROM accounting_cycles WHERE session_id=? AND task_id=? AND ended_at IS NULL',
                (session,task_id),
            ).fetchone()
            if row is None:return
            error=self._close_at_last_observation(
                db,row,json.loads(row['data']),'handoff_at_last_observation',
            )
        if error is not None:raise PoiseError(error)

    def touch(self,session,at:ClockObservation):
        with self.database.transaction() as db:
            row=db.execute('SELECT * FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if row is None:return
            error=self._advance(db,row,at,False)
        if error is not None:raise PoiseError(error)

    def stop(self,session,at:ClockObservation):
        with self.database.transaction() as db:
            r=db.execute('SELECT * FROM accounting_cycles WHERE session_id=? AND ended_at IS NULL',(session,)).fetchone()
            if r is None:return
            error=self._advance(db,r,at,True)
        if error is not None:raise PoiseError(error)

    def snapshot(self):
        with self.database.transaction() as db:
            return self._snapshot_in(db)

    def snapshot_nonblocking(self):
        reader=getattr(self.database,'read_transaction',None)
        if reader is None:return self.snapshot()
        with reader() as db:return self._snapshot_in(db)

    def _snapshot_in(self,db):
        accounts=[dict(r) for r in db.execute('SELECT * FROM accounting_accounts WHERE project=?',(self.project,))]
        tasks={r['id']:TaskQueries.record_in(db,r['id']) for r in db.execute('SELECT id FROM tasks')}
        cycles=[];telemetry=[]
        for original in db.execute(
            'SELECT * FROM accounting_cycles WHERE project=? ORDER BY rowid',
            (self.project,),
        ):
            row=dict(original);data=json.loads(row['data'])
            if data.get('kind')=='telemetry_envelope':
                row['data']=encoded(data['envelope']);telemetry.append(row)
            else:cycles.append(row)
        return {'accounts':accounts,'tasks':tasks,
            'usage':[dict(r) for r in db.execute('SELECT * FROM accounting_usage WHERE project=?',(self.project,))],
            'cycles':cycles,'telemetry':telemetry,
            'credits':[dict(r) for r in db.execute('SELECT * FROM accounting_credits WHERE project=? ORDER BY seq',(self.project,))],
            'messages':[dict(r) for r in db.execute('SELECT e.*,b.task_id,b.sprint_id,b.goal_type,b.stage,b.iteration FROM interaction_events e LEFT JOIN interaction_bindings b ON b.event_id=e.id WHERE e.project=?',(self.project,))],
            'reports':[dict(r) for r in db.execute('SELECT * FROM interaction_reports')],
            'quality':[dict(r) for r in db.execute('SELECT * FROM accounting_findings')],
            'workflows':{r['task_id']:json.loads(r['data']) for r in db.execute('SELECT * FROM task_workflows')},
            'events':[dict(r) for r in db.execute('SELECT * FROM task_events ORDER BY seq')]}

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
            state=task['status'] if task['status'] in ('completed','cancelled') else 'open'
            if previous is not None and previous['state']==state and previous['measurement']==measurement:return
            if previous is None and state=='open':return
            fields=('changed_lines','changed_bytes','changed_tokens')
            delta={k: (measurement[k] if previous is None else (None if measurement[k] is None or previous['measurement'][k] is None else measurement[k]-previous['measurement'][k])) for k in fields}
            data={'marker':marker,'state':state,'binding':binding(task),'measurement':measurement,'delta':delta}
            db.execute('INSERT INTO accounting_credits(id,task_id,project,at,data) VALUES(?,?,?,?,?)',(marker,task['id'],self.project,at,encoded(data)))


class SqliteAccountingCycles:
    """Authoritative cycle release participating in the caller's transaction."""

    def __init__(self,db):self.db=db

    def release(self,session,task_id):
        row=self.db.execute(
            'SELECT * FROM accounting_cycles WHERE session_id=? AND task_id=? AND ended_at IS NULL',
            (session,task_id),
        ).fetchone()
        if row is None:return
        error=SqliteAccounting._close_at_last_observation(
            self.db,row,json.loads(row['data']),'handoff_at_last_observation',
        )
        if error is not None:raise PoiseError(error)


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
