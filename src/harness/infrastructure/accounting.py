"""Composition adapter; observes existing work, never drives its business lifecycle."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from ..modules.accounting.domain import MetricPolicy,BenefitDefinition,parse_telemetry
from ..common import HarnessError
from .sqlite.accounting import SqliteAccounting,timestamp
from .accounting_measurement import PayloadMeasurer,zero_measure
from .accounting_queries import AccountingQueries


class RuntimeAccounting:
    def __init__(self,h,clock=None):
        self.h=h;self.policy=MetricPolicy.parse(h.cfg['accounting'])
        try:ZoneInfo(self.policy.data['timezone'])
        except ZoneInfoNotFoundError as exc:raise HarnessError('Unknown accounting timezone') from exc
        self.repo=SqliteAccounting(h.store.database,h.cfg['project'],self.policy)
        self.measurer=PayloadMeasurer(h)
        self.clock=(lambda:datetime.now(timezone.utc).isoformat()) if clock is None else clock
        self.call_started=None;self.telemetry=None;self.turn_id=None

    def close_cycle(self):self.repo.stop(self.h.session,self.clock())

    def prepare(self,value):
        result=parse_telemetry(value,self.policy)
        for e in result['usage']:timestamp(e.data['occurred_at'])
        for e in result['intervals']:
            a,b=timestamp(e['started_at']),timestamp(e['ended_at'])
            if b<a:raise HarnessError('Time interval ends before start')
        return result

    def _account(self,task):
        if task is None or task['base'] is None:return
        if self.repo.baseline(task['id']) is None:
            d=BenefitDefinition.parse(task['process']['benefit'])
            if set(d.git_categories)-self.policy.data['path_categories'].keys():raise HarnessError('Unconfigured useful Git category')
            # A new task begins without authored sections. Existing early content is not
            # silently adopted as zero: direct API users must bootstrap before submissions.
            self.repo.ensure_account(task,{'git_base':task['base'],'sections':{n:'' for n in d.sections},
                'benefit':task['process']['benefit'],'policy':self.policy.data,'origin':'task_start'},self.clock())

    def receive(self,prepared,task):
        self._account(task)
        if self.policy.data['time_mode']=='tool_cycle' and task is not None and task['status']=='active':
            self.repo.start(self.h.session,task,self.call_started,self.turn_id)
        self.repo.ingest(prepared,self.h.session,task)

    def begin(self,operation,task,prepared,turn_id):
        self.call_started=self.clock();self.telemetry=prepared;self.turn_id=turn_id
        self.repo.new_turn(self.h.session,turn_id)
        self._account(task)
        if self.policy.data['time_mode']=='tool_cycle' and task is not None and task['status']=='active':
            self.repo.start(self.h.session,task,self.call_started,self.turn_id)

    def finish(self,operation,before,after,result):
        self._account(after)
        if self.policy.data['time_mode']=='tool_cycle':
            if after is not None and after['status']=='active' and operation=='bootstrap':
                self.repo.start(self.h.session,after,self.call_started,self.turn_id)
            if (result['status'] in ('verified','cancelled','handed_off','handoff_complete','rejected')
                    or operation in ('handoff','cancel','accept')
                    or (before is not None and after is None)):
                self.repo.stop(self.h.session,self.clock())
        self.repo.touch(self.h.session,self.clock())
        self.reconcile()

    def reconcile(self):
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
                except HarnessError as exc:
                    measure=zero_measure(False)
                    measure.update(changed_lines=None,changed_bytes=None,changed_tokens=None,coverage='unavailable',measurement_error=str(exc))
                    self.h.store.event(self.h.session,task['id'],'accounting.measurement_unavailable',{'reason':str(exc)})
            else:measure=zero_measure(self.policy.data['tokenizer']['kind']!='unavailable')
            self.repo.credit(task,measure,self.clock())

    def report(self,query):
        self.reconcile()
        return AccountingQueries(self.repo,self.policy,self.h.cfg['project']).report(query)
