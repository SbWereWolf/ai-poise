"""Observed primary invocation; transport never decides the primary outcome."""
from dataclasses import asdict,is_dataclass
from uuid import uuid4
from domain.generic import Problem,exact_fields,invalid
from domain.observation_config import parse_settings as observation_settings
from domain.telemetry_config import TelemetrySettings
from infrastructure.observation_store import ObservationStore
from infrastructure.observation_clock import SystemClock
from infrastructure.telemetry_dispatch import CaptureDispatcher
from infrastructure.telemetry_spool import TelemetrySpool


class TelemetryOperations:
    def __init__(self,raw,path,installation_id,primary=None,primary_names=()):
        self.raw,self.path,self.installation_id=raw,path,installation_id
        self.primary,self.primary_names=primary,set(primary_names)

    def _settings(self):return TelemetrySettings.parse(self.raw,self.path)

    def _receiver(self):
        settings=observation_settings(self.raw,self.path)
        if settings.sources.get('poise')!='observed':
            raise Problem('telemetry_unavailable',{'reason':'poise_source'},'configure:telemetry','unavailable')
        store=ObservationStore(settings,self.installation_id)
        def receive(envelope):
            return store.record([envelope['event']],envelope['session_id'],internal=True)
        return receive

    @staticmethod
    def _envelope(base,started,finished,final):
        def value(clock):return asdict(clock) if is_dataclass(clock) else clock
        start,end=value(started),value(finished)
        event={'source':'poise','stream':'operations','event_id':base['id'],
               'observed_at':start['audit_utc'],'kind':'operation',
               'value':{'operation':base['operation'],'status':final['status'],'exit_code':final['exit_code'],
                        'started':start,'finished':end}}
        return {'id':base['id'],'event':event,'session_id':final['session_id']}

    def invoke(self,value):
        exact_fields(value,{'operation','input'},'/input')
        name=value['operation']
        if not isinstance(name,str) or name not in self.primary_names:invalid('/input/operation')
        dispatcher=None;token=None;error=None;identity=uuid4().hex
        try:
            settings=self._settings();receiver=self._receiver()
            processor=receiver if settings.mode=='best_effort' else TelemetrySpool(settings.spool,receiver)
            dispatcher=CaptureDispatcher(processor,settings.max_pending,settings.max_event_bytes)
            token=dispatcher.begin_capture(SystemClock,{'id':identity,'operation':name},self._envelope)
        except (Problem,OSError,ValueError) as exc:
            error={'code':'telemetry_unavailable','details':{'reason':exc.error['code'] if isinstance(exc,Problem) else type(exc).__name__},'action':'configure:telemetry'}
        primary=None;code=1;session_id=None;finished=False
        try:
            primary,code,session_id=self.primary(name,value['input'])
        finally:
            if dispatcher is not None and token is not None:
                try:finished=dispatcher.finish_capture(token,{'status':'unavailable' if primary is None else primary['status'],
                    'exit_code':code,'session_id':session_id})
                except Exception:finished=False
        state='unavailable' if error is not None else 'admitted' if token is not None and finished else 'dropped'
        stats=dispatcher.summary() if dispatcher is not None else {k:0 for k in ('submitted','processed','duplicates','failed','dropped','pending')}
        result={'schema':'poise-observed-invoke/v1','primary':primary,
                'telemetry':{'state':state,'event_id':identity if token is not None else None,
                             'coverage':'unavailable' if error is not None else 'partial','stats':stats,'error':error}}
        return {'schema':'stride-poise/v1','status':primary['status'],'result':result,
                'error':primary['error'],'evidence':[]},code

    def status(self,value):
        exact_fields(value,set(),'/input');settings=self._settings()
        try:spool=None if settings.mode=='best_effort' else TelemetrySpool(settings.spool,None).status()
        except OSError as exc:raise Problem('telemetry_unavailable',{'reason':type(exc).__name__},'inspect:telemetry','unavailable') from None
        return {'schema':'poise-telemetry-status/v1','mode':settings.mode,'coverage':'partial','spool':spool}

    def replay(self,value):
        exact_fields(value,set(),'/input');settings=self._settings()
        if settings.mode=='best_effort':
            raise Problem('telemetry_unavailable',{'reason':'no_persistent_queue'},'configure:telemetry','unavailable')
        try:return TelemetrySpool(settings.spool,self._receiver()).replay()
        except OSError as exc:raise Problem('telemetry_unavailable',{'reason':type(exc).__name__},'inspect:telemetry','unavailable') from None
