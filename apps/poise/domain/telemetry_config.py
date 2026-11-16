"""Explicit bounded transport policy, independent of Task configuration."""
from dataclasses import dataclass
from pathlib import Path
from domain.generic import positive_count
from domain.observation_config import fields,finite
from domain.state import invalid_config


@dataclass(frozen=True)
class DeliveryPolicy:
    directory: Path
    file_mode: int
    max_entries: int
    max_bytes: int
    max_attempts: int
    batch_items: int
    retry_seconds: float
    lock_seconds: float
    lock_poll_seconds: float
    max_event_bytes: int


@dataclass(frozen=True)
class TelemetrySettings:
    mode: str
    max_pending: int
    max_event_bytes: int
    spool: DeliveryPolicy | None

    @classmethod
    def parse(cls,raw,path):
        v=fields(raw.get('telemetry'),('schema','mode','max_pending','max_event_bytes','spool'),'/telemetry')
        if v['schema']!='poise-telemetry-config/v1':invalid_config('/telemetry/schema')
        if v['mode'] not in ('best_effort','durable'):invalid_config('/telemetry/mode')
        if not positive_count(v['max_pending']) or not positive_count(v['max_event_bytes']):invalid_config('/telemetry')
        policy=None
        if v['mode']=='best_effort':
            if v['spool'] is not None:invalid_config('/telemetry/spool')
        else:
            names=('directory','file_mode','max_entries','max_bytes','max_attempts','batch_items','retry_seconds','lock_seconds','lock_poll_seconds')
            s=fields(v['spool'],names,'/telemetry/spool')
            if not isinstance(s['directory'],str) or not s['directory'] or '\0' in s['directory']:invalid_config('/telemetry/spool/directory')
            if type(s['file_mode']) is not int or not 0<=s['file_mode']<=0o777:invalid_config('/telemetry/spool/file_mode')
            for k in ('max_entries','max_bytes','max_attempts','batch_items'):
                if not positive_count(s[k]):invalid_config('/telemetry/spool/'+k)
            if s['max_attempts']>1000000 or s['batch_items']>s['max_entries']:invalid_config('/telemetry/spool')
            if s['max_bytes']<v['max_event_bytes']+2048:invalid_config('/telemetry/spool/max_bytes')
            for k in ('retry_seconds','lock_seconds','lock_poll_seconds'):
                if not finite(s[k],True):invalid_config('/telemetry/spool/'+k)
            location=Path(s['directory'])
            if not location.is_absolute():location=path.parent/location
            policy=DeliveryPolicy(location,*(s[k] for k in names[1:]),v['max_event_bytes'])
        return cls(v['mode'],v['max_pending'],v['max_event_bytes'],policy)
