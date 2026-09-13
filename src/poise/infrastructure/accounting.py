"""Composition adapter; observes existing work, never drives its business lifecycle."""
from copy import deepcopy
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from ..modules.accounting.domain import MetricPolicy,BenefitDefinition,parse_telemetry,identity
from ..modules.accounting.clock import ClockObservation
from ..common import PoiseError,encoded
from .sqlite.accounting import SqliteAccounting,timestamp,binding
from .accounting_measurement import PayloadMeasurer,zero_measure
from .accounting_queries import AccountingQueries


def _empty_snapshot():
    return {
        'accounts':[], 'tasks':{}, 'usage':[], 'cycles':[], 'telemetry':[],
        'credits':[], 'messages':[], 'reports':[], 'quality':[], 'workflows':{},
        'events':[],
    }


class _SnapshotRepository:
    def __init__(self,data):self.data=data
    def snapshot(self):return deepcopy(self.data)


class _AccountingRepositories:
    """One read projection over authoritative facts and optional observations."""

    def __init__(self,authoritative,telemetry):
        self.authoritative,self.telemetry=authoritative,telemetry
        self.database=telemetry.database

    def __getattr__(self,name):return getattr(self.authoritative,name)

    def snapshot(self):
        result,conflicts=self.combine(
            self.authoritative.snapshot(),self.telemetry.snapshot_nonblocking()
        )
        self.projection_conflicts=conflicts
        return result

    @staticmethod
    def combine(authoritative,optional):
        result=deepcopy(authoritative);conflicts=0

        def content(row):
            return {key:value for key,value in row.items() if key not in ('task_id','seq')}

        def merge(kind,secondary,unique=None):
            nonlocal conflicts
            rows=list(result[kind]);by_id={row['id']:row for row in rows}
            by_unique={} if unique is None else {unique(row):row for row in rows}
            for row in secondary:
                old=by_id.get(row['id'])
                collision=None if unique is None else by_unique.get(unique(row))
                if old is not None:
                    if content(old)!=content(row):conflicts+=1
                    continue
                if collision is not None:
                    conflicts+=1
                    continue
                rows.append(row);by_id[row['id']]=row
                if unique is not None:by_unique[unique(row)]=row
            result[kind]=rows

        merge('usage',optional['usage'],lambda row:(row['source'],row['stream'],row['sequence']))
        merge('cycles',optional['cycles'])
        merge('telemetry',optional['telemetry'])
        return result,conflicts


class RuntimeAccounting:
    def __init__(self,h,database):
        self.h=h;self.policy=MetricPolicy.parse(h.cfg['accounting'])
        try:ZoneInfo(self.policy.data['timezone'])
        except ZoneInfoNotFoundError as exc:raise PoiseError('Unknown accounting timezone') from exc
        self.telemetry_repo=SqliteAccounting(database,h.cfg['project'],self.policy)
        self.repo=_AccountingRepositories(
            SqliteAccounting(h.store.database,h.cfg['project'],self.policy),
            self.telemetry_repo,
        )
        self.measurer=PayloadMeasurer(h)
        self._last_optional_snapshot=_empty_snapshot()
        self._projection_conflicts=0

    def prepare(self,value):
        result=parse_telemetry(value,self.policy)
        for e in result['usage']:timestamp(e.data['occurred_at'])
        for e in result['intervals']:
            a,b=timestamp(e['started_at']),timestamp(e['ended_at'])
            if b<a:raise PoiseError('Time interval ends before start')
        return result

    def _account(self,task,observation):
        if task is None or task['base'] is None:return
        if self.repo.baseline(task['id']) is None:
            d=BenefitDefinition.parse(task['process']['benefit'])
            if set(d.git_categories)-self.policy.data['path_categories'].keys():raise PoiseError('Unconfigured useful Git category')
            # A new task begins without authored sections. Existing early content is not
            # silently adopted as zero: direct API users must bootstrap before submissions.
            self.repo.ensure_account(task,{'git_base':task['base'],'sections':{n:'' for n in d.sections},
                'benefit':task['process']['benefit'],'policy':self.policy.data,'origin':'task_start'},observation.audit_utc)

    @staticmethod
    def _observation(value):
        return ClockObservation(
            value['audit_utc'],
            value['monotonic_ns'],
            value['comparison_domain'],
        )

    def process(self,envelope):
        data=envelope.data
        started=self._observation(data['started']);finished=self._observation(data['finished'])
        raw=data['telemetry'];prepared=None if raw is None else self.prepare(raw)
        if not self.telemetry_repo.store_telemetry(envelope):return
        selected=data['after_binding'] if data['after_binding']['task'] is not None else data['before_binding']
        if prepared is not None:self.telemetry_repo.ingest_binding(prepared,data['session'],selected,False)

    def reconcile(self,observation):
        data=self.repo.snapshot()
        for a in data['accounts']:
            task=data['tasks'][a['task_id']]
            old=self.repo.latest_credit(task['id'])
            state=task['status'] if task['status'] in ('completed','cancelled','superseded') else 'open'
            if state=='open' and (old is None or old['state']=='open'):continue
            if old is not None and old['state']==state and state!='completed':continue
            if old is not None and old['state']=='completed' and state=='completed':continue
            if state=='completed':
                try:measure=self.measurer.measure(task,self.repo.baseline(task['id']))
                except PoiseError as exc:
                    measure=zero_measure(False)
                    measure.update(changed_lines=None,changed_bytes=None,changed_tokens=None,coverage='unavailable',measurement_error=str(exc))
                    self.h.store.event(self.h.session,task['id'],'accounting.measurement_unavailable',{'reason':str(exc)})
            else:measure=zero_measure(self.policy.data['tokenizer']['kind']!='unavailable')
            self.repo.credit(task,measure,observation.audit_utc)

    def report(self,query):
        snapshot=self._snapshot()
        self._project_terminal_credits(snapshot)
        return AccountingQueries(_SnapshotRepository(snapshot),self.policy,self.h.cfg['project']).report(query)

    def telemetry_summary(self,runtime):
        result=dict(runtime)
        result['stored']=len(self._snapshot()['telemetry'])
        result['projection_conflicts']=self._projection_conflicts
        result['coverage']='partial' if (result['stored'] or self._projection_conflicts
            or result.get('coverage')=='partial') else 'unavailable'
        return result

    def _snapshot(self):
        authoritative=self.repo.authoritative.snapshot()
        try:
            optional=self.telemetry_repo.snapshot_nonblocking()
        except Exception:
            optional=deepcopy(self._last_optional_snapshot)
        else:
            self._last_optional_snapshot=deepcopy(optional)
        combined,self._projection_conflicts=self.repo.combine(authoritative,optional)
        return combined

    def _project_terminal_credits(self,snapshot):
        credited={row['task_id'] for row in snapshot['credits']}
        event_times={}
        for event in snapshot['events']:
            event_times[event['task_id']]=event['at']
        for task_id,task in snapshot['tasks'].items():
            if task_id in credited or task['status'] not in ('completed','cancelled','superseded'):
                continue
            definition=BenefitDefinition.parse(task['process']['benefit'])
            baseline={'git_base':task['base'],'sections':{name:'' for name in definition.sections},
                'benefit':task['process']['benefit'],'policy':self.policy.data,'origin':'task_start'}
            if task['status']=='completed':
                try:measurement=self.measurer.measure(task,baseline)
                except PoiseError as exc:
                    measurement=zero_measure(False)
                    measurement.update(changed_lines=None,changed_bytes=None,changed_tokens=None,
                        coverage='unavailable',measurement_error=str(exc))
            else:measurement=zero_measure(self.policy.data['tokenizer']['kind']!='unavailable')
            fields=('changed_lines','changed_bytes','changed_tokens')
            data={'marker':identity([task_id,task['_version'],task['status']]),
                'state':task['status'],'binding':binding(task),'measurement':measurement,
                'delta':{key:measurement[key] for key in fields}}
            snapshot['credits'].append({'task_id':task_id,'project':self.h.cfg['project'],
                'at':event_times.get(task_id,'1970-01-01T00:00:00+00:00'),'data':encoded(data)})
