"""Composition adapter; observes existing work, never drives its business lifecycle."""
from copy import deepcopy
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from ..modules.accounting.domain import MetricPolicy,BenefitDefinition,parse_telemetry
from ..modules.accounting.clock import ClockObservation
from ..common import PoiseError
from .sqlite.accounting import SqliteAccounting,timestamp
from .accounting_measurement import PayloadMeasurer,zero_measure
from .accounting_queries import AccountingQueries


def _empty_snapshot():
    return {
        'accounts':[], 'tasks':{}, 'usage':[], 'cycles':[], 'telemetry':[],
        'credits':[], 'messages':[], 'reports':[], 'quality':[], 'workflows':{},
    }


class _SnapshotRepository:
    def __init__(self,data):self.data=data
    def snapshot(self):return deepcopy(self.data)


class RuntimeAccounting:
    def __init__(self,h,database):
        self.h=h;self.policy=MetricPolicy.parse(h.cfg['accounting'])
        try:ZoneInfo(self.policy.data['timezone'])
        except ZoneInfoNotFoundError as exc:raise PoiseError('Unknown accounting timezone') from exc
        self.repo=SqliteAccounting(database,h.cfg['project'],self.policy)
        self.measurer=PayloadMeasurer(h)
        self._last_snapshot=_empty_snapshot()

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
        if not self.repo.store_telemetry(envelope):return
        selected=data['after_binding'] if data['after_binding']['task'] is not None else data['before_binding']
        if prepared is not None:self.repo.ingest_binding(prepared,data['session'],selected,False)
        self._last_snapshot=self.repo.snapshot()

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
        return AccountingQueries(_SnapshotRepository(snapshot),self.policy,self.h.cfg['project']).report(query)

    def telemetry_summary(self,runtime):
        result=dict(runtime)
        result['stored']=len(self._snapshot()['telemetry'])
        result['coverage']='partial' if result['stored'] or result.get('coverage')=='partial' else 'unavailable'
        return result

    def _snapshot(self):
        if getattr(self.h,'telemetry',None) is not None:
            try:
                if self.h.telemetry.summary().get('pending',0):
                    return deepcopy(self._last_snapshot)
            except Exception:
                return deepcopy(self._last_snapshot)
        try:
            self._last_snapshot=self.repo.snapshot()
        except Exception:
            pass
        return deepcopy(self._last_snapshot)
