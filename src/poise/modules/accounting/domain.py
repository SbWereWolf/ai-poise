"""Cost and useful-output contracts. No clock, filesystem, SQL or project dispatch."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
from .clock import validate_source_observation
from ..foundation.errors import DomainError

COUNTERS = ('input_tokens','output_tokens','total_tokens','cached_input_tokens','reasoning_tokens')


def exact(value, fields, label):
    if not isinstance(value,dict) or set(value)!=set(fields):
        raise DomainError(f'{label}: required explicit fields {sorted(fields)}')


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def identity(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def names(value, label):
    if not isinstance(value,list) or any(not isinstance(x,str) or not x.strip() for x in value) or len(value)!=len(set(value)):
        raise DomainError(f'{label}: explicit unique names required')


@dataclass(frozen=True)
class BenefitDefinition:
    git_categories: tuple[str,...]
    sections: tuple[str,...]

    @classmethod
    def parse(cls,value):
        exact(value,{'git_categories','sections'},'benefit')
        names(value['git_categories'],'benefit.git_categories');names(value['sections'],'benefit.sections')
        return cls(tuple(value['git_categories']),tuple(value['sections']))


@dataclass(frozen=True)
class MetricPolicy:
    document: str

    @property
    def data(self):return json.loads(self.document)

    @classmethod
    def parse(cls,value):
        exact(value,{'timezone','week_start','max_events','max_files','max_blob_bytes','time_mode','causes','sources','path_categories','tokenizer','storage'},'accounting config')
        storage=value['storage'];exact(storage,{'database','lock'},'accounting.storage')
        for key in ('database','lock'):
            if not isinstance(storage[key],str) or not storage[key].strip():
                raise DomainError(f'accounting.storage.{key}: nonempty path required')
        for key in ('max_events','max_files','max_blob_bytes'):
            if type(value[key]) is not int or value[key]<=0:raise DomainError(f'{key}: positive explicit bound required')
        if not isinstance(value['timezone'],str) or not value['timezone']:raise DomainError('timezone required')
        if type(value['week_start']) is not int or value['week_start'] not in range(7):raise DomainError('week_start must be 0..6')
        if value['time_mode'] not in ('tool_cycle','reported'):raise DomainError('Explicit time mode required')
        names(value['causes'],'causes')
        if not isinstance(value['sources'],dict) or not value['sources'] or any(not isinstance(k,str) or not k or v not in ('reported','observed') for k,v in value['sources'].items()):
            raise DomainError('Explicit usage sources required')
        if not isinstance(value['path_categories'],dict):raise DomainError('path_categories required')
        for label,patterns in value['path_categories'].items():
            if not isinstance(label,str) or not label:raise DomainError('Category ID required')
            names(patterns,'path patterns')
        t=value['tokenizer'];exact(t,{'kind','identity','command'},'measurement tokenizer')
        if not isinstance(t['identity'],str) or not t['identity']:raise DomainError('Measurement identity required')
        if t['kind']=='unavailable':
            if t['command'] is not None:raise DomainError('Unavailable tokenizer requires explicit null command')
        elif t['kind']=='command':
            c=t['command'];exact(c,{'argv','cwd','environment','timeout_seconds','max_output_bytes'},'tokenizer command')
            if not isinstance(c['argv'],list) or not c['argv'] or any(not isinstance(x,str) for x in c['argv']):raise DomainError('Tokenizer argv required')
            if not isinstance(c['cwd'],str) or not c['cwd'] or not isinstance(c['environment'],dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in c['environment'].items()):raise DomainError('Tokenizer cwd/environment required')
            for k in ('timeout_seconds','max_output_bytes'):
                if type(c[k]) is not int or c[k]<=0:raise DomainError('Tokenizer positive bounds required')
        else:raise DomainError('Unknown tokenizer kind; no fallback')
        return cls(canonical(value))


@dataclass(frozen=True)
class UsageSample:
    document: str

    @property
    def data(self):return json.loads(self.document)
    @property
    def key(self):
        d=self.data;return identity([d['source'],d['stream'],d['event_id']])

    @classmethod
    def parse(cls,value,policy):
        exact(value,{'source','stream','event_id','sequence','mode','occurred_at','counters'},'usage sample')
        for k in ('source','stream','event_id','occurred_at'):
            if not isinstance(value[k],str) or not value[k]:raise DomainError(f'usage {k} required')
        if value['source'] not in policy.data['sources']:raise DomainError('Unknown usage source')
        if type(value['sequence']) is not int or value['sequence']<0:raise DomainError('Usage sequence required')
        if value['mode'] not in ('delta','baseline','cumulative'):raise DomainError('Unknown counter mode')
        counts=value['counters'];exact(counts,COUNTERS,'counters')
        for k,v in counts.items():
            if v is None and k in ('cached_input_tokens','reasoning_tokens'):continue
            if type(v) is not int or v<0:raise DomainError(f'{k}: nonnegative actual integer required')
        if counts['total_tokens']!=counts['input_tokens']+counts['output_tokens']:raise DomainError('Token total must equal input + output')
        for detail,parent in (('cached_input_tokens','input_tokens'),('reasoning_tokens','output_tokens')):
            if counts[detail] is not None and counts[detail]>counts[parent]:raise DomainError('Token detail exceeds parent counter')
        return cls(canonical(value))


def usage_contribution(previous,current):
    d=current.data
    if d['mode']=='baseline':
        if previous is not None:raise DomainError('Counter baseline cannot reset an existing stream')
        return {k:None if d['counters'][k] is None else 0 for k in COUNTERS}
    if d['mode']=='delta':
        if previous is not None and previous.data['mode']!='delta':raise DomainError('Mixed counter modes in one stream')
        return d['counters']
    if previous is None or previous.data['mode'] not in ('baseline','cumulative'):
        raise DomainError('Cumulative usage needs an explicit preceding baseline')
    p=previous.data
    if (p['source'],p['stream'])!=(d['source'],d['stream']) or p['sequence']>=d['sequence']:
        raise DomainError('Cumulative sequence/source mismatch')
    result={}
    for k in COUNTERS:
        now,before=d['counters'][k],p['counters'][k]
        if now is None or before is None:result[k]=None
        elif now<before:raise DomainError('Counter reset: require a new explicit stream and baseline')
        else:result[k]=now-before
    for detail,parent in (('cached_input_tokens','input_tokens'),('reasoning_tokens','output_tokens')):
        if result[detail] is not None and result[detail]>result[parent]:raise DomainError('Counter detail delta exceeds parent delta')
    return result


def line_delta(before:bytes,after:bytes):
    """Raw UTF-8 lines incl. newline; replacements count removed + added content."""
    if b'\x00' in before or b'\x00' in after:raise DomainError('Binary payload has no text metric')
    try:a=before.decode('utf-8').splitlines(keepends=True);b=after.decode('utf-8').splitlines(keepends=True)
    except UnicodeError as exc:raise DomainError('Non-UTF8 payload has no text metric') from exc
    added=[];removed=[]
    for op,i,j,k,l in SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if op in ('replace','delete'):removed.extend(a[i:j])
        if op in ('replace','insert'):added.extend(b[k:l])
    add=''.join(added);rem=''.join(removed)
    return {'added_lines':len(added),'removed_lines':len(removed),'added_bytes':len(add.encode()),'removed_bytes':len(rem.encode()),'added_text':add,'removed_text':rem}


def _source_metric(value, policy):
    """Validate a source fact without converting units or deriving a duration."""
    exact(value, {'source', 'stream', 'event_id', 'observed_at', 'parameters'}, 'source metric')
    for key in ('source', 'stream', 'event_id', 'observed_at'):
        if not isinstance(value[key], str) or not value[key].strip():
            raise DomainError(f'source metric {key}: nonempty source value required')
    if value['source'] not in policy.data['sources']:
        raise DomainError('Unknown source metric source')
    validate_source_observation(value['observed_at'])
    if not isinstance(value['parameters'], dict):
        raise DomainError('Source metric parameters must be a JSON object')
    try:
        canonical(value['parameters'])
    except (TypeError, ValueError) as exc:
        raise DomainError('Source metric parameters must contain finite JSON values') from exc


def parse_telemetry(value,policy):
    fields = {'usage','intervals','cause','finding_targets'}
    if isinstance(value, dict) and 'source_metrics' in value:
        fields.add('source_metrics')
    exact(value,fields,'telemetry')
    if 'source_metrics' in value:
        metrics = value['source_metrics']
        if not isinstance(metrics, list) or len(metrics) > policy.data['max_events']:
            raise DomainError('source_metrics: bounded batch required')
        for item in metrics:
            _source_metric(item, policy)
    for k in ('usage','intervals','finding_targets'):
        if not isinstance(value[k],list) or len(value[k])>policy.data['max_events']:raise DomainError(f'{k}: bounded batch required')
    if value['cause'] is not None and value['cause'] not in policy.data['causes']:raise DomainError('Unknown cost cause; no inferred alias')
    samples=tuple(UsageSample.parse(x,policy) for x in value['usage'])
    if value['intervals'] and policy.data['time_mode']!='reported':raise DomainError('Do not mix reported time and automatic tool cycles')
    for item in value['intervals']:
        exact(item,{'source','stream','event_id','started_at','ended_at'},'reported time')
        if any(not isinstance(v,str) or not v for v in item.values()):raise DomainError('Time event fields required')
        if item['source'] not in policy.data['sources']:raise DomainError('Unknown time source')
    for item in value['finding_targets']:
        exact(item,{'finding_id','stage','iteration'},'finding target')
        if any(not isinstance(item[k],str) or not item[k] for k in ('finding_id','stage')) or type(item['iteration']) is not int or item['iteration']<1:raise DomainError('Finding target requires an explicit delivered iteration')
    return {**deepcopy(value),'usage':samples}
