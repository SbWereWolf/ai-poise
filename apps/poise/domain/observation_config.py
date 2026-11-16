"""Explicit observation settings; construction performs no filesystem work."""
from dataclasses import dataclass
import math
from pathlib import Path
from domain.generic import configuration_fields, positive_count
from domain.state import invalid_config


@dataclass(frozen=True)
class ObservationSettings:
    database: Path
    file_mode: int
    timeout: float
    sources: dict
    limits: dict


def fields(value,names,path):
    return configuration_fields(value,set(names),path,lambda message: invalid_config(path))


def finite(value,positive=False):
    try:return type(value) in (int,float) and math.isfinite(value) and (value>0 if positive else value>=0)
    except OverflowError:return False


def parse_settings(raw,path):
    value=fields(raw.get('observations'),('schema','database','file_mode','sqlite_timeout_seconds','sources','limits'),'/observations')
    if value['schema']!='poise-observations-config/v1':invalid_config('/observations/schema')
    location=value['database']
    if not isinstance(location,str) or not location or '\0' in location:invalid_config('/observations/database')
    mode=value['file_mode']
    if type(mode) is not int or not 0<=mode<=0o777:invalid_config('/observations/file_mode')
    if not finite(value['sqlite_timeout_seconds']):invalid_config('/observations/sqlite_timeout_seconds')
    sources=value['sources']
    if not isinstance(sources,dict) or not sources:invalid_config('/observations/sources')
    if any(not isinstance(k,str) or not k or v not in ('reported','observed') for k,v in sources.items()):invalid_config('/observations/sources')
    limits=fields(value['limits'],('max_events','max_event_bytes','max_results','max_read_response_bytes'),'/observations/limits')
    if any(not positive_count(v) for v in limits.values()):invalid_config('/observations/limits')
    database=Path(location)
    if not database.is_absolute():database=path.parent/database
    return ObservationSettings(database,mode,value['sqlite_timeout_seconds'],dict(sources),limits)
