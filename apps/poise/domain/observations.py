"""Source-owned observations, exact contribution and comparable clock values."""
from dataclasses import dataclass
from datetime import datetime,timezone
import json
import re
from domain.generic import Problem,exact_fields,invalid,text

COUNTERS=('input_tokens','output_tokens','total_tokens','cached_input_tokens','reasoning_tokens')


def encoded(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def canonical(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))


def conflict(reason):
    raise Problem('observation_conflict',{'reason':reason},'use:new-event')


def limit(name,action='reduce:events'):
    raise Problem('observation_limit',{'limit':name},action)


def _timestamp_parts(value,path):
    try:
        if not isinstance(value,str):raise ValueError()
        parsed=datetime.fromisoformat(value)
        if parsed.tzinfo is None:raise ValueError()
    except (TypeError,ValueError,OverflowError):invalid(path)
    zone=re.search(r'([+-])\d{2}(?::?\d{2})?(?::?\d{2})?(?:[.,](\d+))?$',value)
    main=value[:zone.start()] if zone else value
    fractions=re.findall(r'[.,](\d+)',main)
    if len(fractions)>1:invalid(path)
    def tail(digits):
        # Extra zero digits preserve the same integer-nanosecond value.
        significant=digits.rstrip('0')
        if len(significant)>9:invalid(path)
        return int((significant+'000000000')[:9][6:])
    main_tail=tail(fractions[0] if fractions else '')
    zone_tail=tail(zone.group(2) or '') if zone else 0
    if zone and zone.group(1)=='-':zone_tail=-zone_tail
    return parsed,main_tail,zone_tail


def timestamp(value,path):
    """Exact integer Unix nanoseconds, retaining source strings at their owner."""
    parsed,main_tail,zone_tail=_timestamp_parts(value,path)
    delta=parsed.replace(tzinfo=None)-datetime(1970,1,1)-parsed.utcoffset()
    return ((delta.days*86400+delta.seconds)*1000000+delta.microseconds)*1000+main_tail-zone_tail


@dataclass(frozen=True)
class ClockObservation:
    audit_utc: str
    monotonic_ns: int
    comparison_domain: str


def clock(value,path):
    if value is None:return
    exact_fields(value,{'audit_utc','monotonic_ns','comparison_domain'},path)
    parsed,_,zone_tail=_timestamp_parts(value['audit_utc'],path+'/audit_utc')
    if parsed.utcoffset().total_seconds()!=0 or zone_tail:invalid(path+'/audit_utc')
    if type(value['monotonic_ns']) is not int or value['monotonic_ns']<0:invalid(path+'/monotonic_ns')
    text(value['comparison_domain'],path+'/comparison_domain')


def timing_duration(value):
    start,end=value['started'],value['finished']
    if start is None or end is None:return None
    if start['comparison_domain']!=end['comparison_domain']:return None
    return end['monotonic_ns']-start['monotonic_ns'] if end['monotonic_ns']>=start['monotonic_ns'] else None


def interval_duration(value):
    return timestamp(value['ended_at'],'/input')-timestamp(value['started_at'],'/input')


@dataclass(frozen=True)
class Event:
    data: dict

    @property
    def identity(self):
        d=self.data
        return d['kind'],d['source'],d['stream'],d['event_id']

    @classmethod
    def parse(cls,value,settings,internal=False):
        exact_fields(value,{'source','stream','event_id','observed_at','kind','value'},'/input/events')
        for key in ('source','stream','event_id'):text(value[key],'/input/events/'+key)
        if value['source'] not in settings.sources:invalid('/input/events/source')
        timestamp(value['observed_at'],'/input/events/observed_at')
        kind=value['kind'];body=value['value'];path='/input/events/value'
        if kind=='usage':
            exact_fields(body,{'sequence','mode','counters'},path)
            if type(body['sequence']) is not int or body['sequence']<0:invalid(path+'/sequence')
            if body['mode'] not in ('delta','baseline','cumulative'):invalid(path+'/mode')
            counters=exact_fields(body['counters'],set(COUNTERS),path+'/counters')
            for key,n in counters.items():
                if n is None and key in COUNTERS[3:]:continue
                if type(n) is not int or n<0:invalid(path+'/counters/'+key)
            if counters['total_tokens']!=counters['input_tokens']+counters['output_tokens']:invalid(path+'/counters/total_tokens')
            for key,parent in (('cached_input_tokens','input_tokens'),('reasoning_tokens','output_tokens')):
                if counters[key] is not None and counters[key]>counters[parent]:invalid(path+'/counters/'+key)
        elif kind=='source':
            exact_fields(body,{'parameters'},path)
            if not isinstance(body['parameters'],dict):invalid(path+'/parameters')
        elif kind in ('timing','operation'):
            names={'started','finished'}
            if kind=='operation':
                if not internal or value['source']!='poise':invalid('/input/events/kind')
                names|={'operation','status','exit_code'}
            exact_fields(body,names,path)
            clock(body['started'],path+'/started');clock(body['finished'],path+'/finished')
            if kind=='operation':
                text(body['operation'],path+'/operation')
                if body['status'] not in ('ok','rejected','unavailable'):invalid(path+'/status')
                if type(body['exit_code']) is not int or body['exit_code'] not in (0,1,2):invalid(path+'/exit_code')
        elif kind=='interval':
            exact_fields(body,{'started_at','ended_at'},path)
            if timestamp(body['ended_at'],path+'/ended_at')<timestamp(body['started_at'],path+'/started_at'):invalid(path)
        else:invalid('/input/events/kind')
        try:raw=encoded(value).encode()
        except (TypeError,ValueError,RecursionError,UnicodeError):invalid('/input/events')
        if len(raw)>settings.limits['max_event_bytes']:limit('max_event_bytes')
        return cls(value)


def usage_contribution(previous,current):
    d=current['value']
    if previous is not None:
        p=previous['value']
        if d['sequence']<=p['sequence']:conflict('sequence')
        if timestamp(current['observed_at'],'/input')<timestamp(previous['observed_at'],'/input'):conflict('source_time')
    if d['mode']=='baseline':
        if previous is not None:conflict('baseline_reset')
        return {k:None if n is None else 0 for k,n in d['counters'].items()}
    if d['mode']=='delta':
        if previous is not None and previous['value']['mode']!='delta':conflict('stream_mode')
        return dict(d['counters'])
    if previous is None or previous['value']['mode'] not in ('baseline','cumulative'):conflict('missing_baseline')
    result={}
    for key,now in d['counters'].items():
        before=previous['value']['counters'][key]
        if now is None or before is None:result[key]=None
        elif now<before:conflict('counter_reset')
        else:result[key]=now-before
    for key,parent in (('cached_input_tokens','input_tokens'),('reasoning_tokens','output_tokens')):
        if result[key] is not None and result[key]>result[parent]:conflict('counter_detail')
    return result
