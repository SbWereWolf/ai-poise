"""Codex command-hook definition: protocol names, not task workflow decisions."""
from copy import deepcopy
from dataclasses import dataclass
import string
import re
from ..artifact_factory.domain import exact
from ..capabilities.domain import ProbeSpec,positive
from ..foundation.validation import nonempty
from ..foundation.errors import PoiseError

class MissingHookBinding(PoiseError):
    """No genuine binding exists for the requested native identity."""


def binding_context(record):
    """Required binding identity, excluding the diagnostic settings revision."""
    if not isinstance(record, dict):
        raise PoiseError('Binding must be an object; restore the registered binding file')
    return {key: value for key, value in record.items() if key != 'settings_digest'}


TOOL_EVENTS={'PreToolUse','PostToolUse'}
COMPACTION_EVENTS = {'PreCompact', 'PostCompact'}
EVENTS={'SessionStart','UserPromptSubmit','Stop','SessionEnd'} | TOOL_EVENTS | COMPACTION_EVENTS
CONTEXT_EVENTS={'SessionStart','UserPromptSubmit'}
SESSION_START_SOURCES=('startup','resume','clear','compact','fork')


def validate_native_events(events, *, working_configuration=False):
    if not isinstance(events,list) or not events:raise PoiseError('Explicit hooks required')
    seen=set()
    for event in events:
        fields = {'event','matcher','timeout_seconds','async','context_limit','status_message'}
        if working_configuration:
            from ..foundation.validation import configuration_fields
            configuration_fields(event, fields, 'hook event')
        else:
            exact(event, fields, 'hook event')
        kind=event['event']
        if kind not in EVENTS or kind in seen:raise PoiseError('Unknown or duplicate hook event')
        seen.add(kind)
        if not isinstance(event['matcher'],str):raise PoiseError('Explicit matcher required')
        try:re.compile(event['matcher'])
        except re.error as exc:raise PoiseError('Invalid hook matcher') from exc
        positive(event['timeout_seconds'],'hook timeout')
        if event['async'] is not False:
            raise PoiseError('Binding/counting hooks are synchronous; SessionEnd is advisory synchronous')
        if kind=='SessionEnd' and not 1<=event['timeout_seconds']<=3:
            raise PoiseError('Codex SessionEnd requires a timeout between one and three seconds')
        if kind in CONTEXT_EVENTS | TOOL_EVENTS:positive(event['context_limit'],'context_limit',True)
        elif event['context_limit'] is not None:raise PoiseError('No additionalContext for this event: use explicit null')
        nonempty(event['status_message'],'status_message')
    if 'SessionStart' not in seen:raise PoiseError('SessionStart binding hook is required')
    if seen & TOOL_EVENTS and not TOOL_EVENTS <= seen:
        raise PoiseError('Install PreToolUse and PostToolUse together')
    if any(e['event'] in TOOL_EVENTS and e['matcher'] != 'Bash' for e in events):
        raise PoiseError('Command intake requires the explicit Bash matcher')



@dataclass(frozen=True)
class HookDefinition:
    data: dict

    @classmethod
    def parse(cls,raw):
        if isinstance(raw, dict) and raw.get('schema') == 'hook-definition-2':
            return cls._parse_current(raw)
        exact(raw,{'schema','id','agent_id','message_source','events','probes','gate_operations',
                   'context_template','stop_template','unknown_outcome_message'},'hook definition')
        if raw['schema']!='hook-definition-1':raise PoiseError('Unsupported hook definition; no migration')
        for key in ('id','agent_id','message_source','unknown_outcome_message'):nonempty(raw[key],key)
        for key,allowed in [('context_template',{'launcher','state'}),('stop_template',{'state'})]:
            nonempty(raw[key],key)
            try:
                fields={field for _,field,_,_ in string.Formatter().parse(raw[key]) if field is not None}
                if fields!=allowed:raise ValueError('Template must explicitly contain exactly its named fields')
                raw[key].format(**{k:'sample' for k in allowed})
            except (KeyError,ValueError,IndexError) as exc:raise PoiseError(f'Invalid {key}') from exc
        validate_native_events(raw['events'])
        if not isinstance(raw['probes'],list):raise PoiseError('Explicit probe list required')
        probes=[ProbeSpec.parse(p) for p in raw['probes']]
        if len({p.data['id'] for p in probes})!=len(probes):raise PoiseError('Duplicate capability')
        if not isinstance(raw['gate_operations'],list) or len(set(raw['gate_operations']))!=len(raw['gate_operations']):
            raise PoiseError('Explicit unique gate operations required')
        if any(x not in ('bootstrap','verify','artifacts') for x in raw['gate_operations']):
            raise PoiseError('Read, handoff and cancellation must not be blocked by capability gates')
        return cls(deepcopy(raw))


    @classmethod
    def _parse_current(cls, raw):
        from ..foundation.validation import configuration_fields
        configuration_fields(raw, {'schema', 'id', 'agent_id', 'message_source',
                                   'events', 'routes', 'transport'}, 'hook definition')
        for key in ('id', 'agent_id', 'message_source'):
            nonempty(raw[key], key)
        validate_native_events(raw['events'], working_configuration=True)
        routes = raw['routes']
        if not isinstance(routes, dict) or set(routes) != {e['event'] for e in raw['events']}:
            raise PoiseError('Hook routes must name exactly the installed events')
        for event, paths in routes.items():
            if not isinstance(paths, list):
                raise PoiseError('Hook routes require ordered configuration path lists')
            for path in paths:
                nonempty(path, f'routes.{event}')
                if not path.startswith('/'):
                    raise PoiseError('Hook routes require explicit absolute configuration paths')
        transport = configuration_fields(raw['transport'], {'unknown_outcome_message'}, 'transport')
        nonempty(transport['unknown_outcome_message'], 'transport.unknown_outcome_message')
        return cls(deepcopy(raw))


def parse_native_event(raw,definition):
    if not isinstance(raw,dict):raise PoiseError('Native hook JSON object required')
    if 'agent_id' in raw:
        raise PoiseError('Subagent or ambiguous native agent context cannot bind the parent native session; open a distinct native session')
    kind=raw.get('hook_event_name')
    if kind not in {x['event'] for x in definition.data['events']}:raise PoiseError('Hook event is not installed for this definition')
    for key in ('session_id','cwd'):nonempty(raw.get(key),key)
    if kind in {'UserPromptSubmit','Stop'} | COMPACTION_EVENTS:nonempty(raw.get('turn_id'),'turn_id')
    if kind in COMPACTION_EVENTS:nonempty(raw.get('trigger'),'trigger')
    if kind=='UserPromptSubmit' and not isinstance(raw.get('prompt'),str):
        raise PoiseError('UserPromptSubmit requires its prompt field; its text is not retained')
    # Optional wire metadata are not process defaults. Ignore prompt and assistant text.
    if kind=='SessionStart':
        if raw.get('source') not in SESSION_START_SOURCES:raise PoiseError('Unsupported SessionStart source')
    if kind=='SessionEnd' and raw.get('reason')!='other':raise PoiseError('Unsupported SessionEnd reason')
    return {'event':kind,'session_id':raw['session_id'],'cwd':raw['cwd'],
            'turn_id':raw['turn_id'] if kind in {'UserPromptSubmit','Stop'} | COMPACTION_EVENTS else None}


def native_event_metadata(raw, native):
    """The same normalized committed metadata authorize both delivery and retry."""
    if native['event'] == 'SessionStart':
        return {'source': raw['source'], 'workspace': native['cwd']}
    if native['event'] in COMPACTION_EVENTS:
        return {'turn_id': native['turn_id'], 'trigger': raw['trigger'], 'workspace': native['cwd']}
    if native['event'] == 'SessionEnd':
        return {'reason': raw['reason']}
    return {}


def merge_hook_document(existing,previous,desired):
    """Replace only previously owned complete groups; preserve every foreign group."""
    if existing is None:doc={'hooks':{}}
    else:
        if not isinstance(existing,dict) or not isinstance(existing.get('hooks'),dict):
            raise PoiseError('Existing hooks file must have a hooks object')
        doc=deepcopy(existing)
    for event,groups in doc['hooks'].items():
        if not isinstance(groups,list):raise PoiseError('Existing hook groups must be lists')
    for event,group in previous:
        groups=doc['hooks'].get(event,[])
        if groups.count(group)!=1:raise PoiseError('Managed hook changed externally; do not overwrite')
        groups.remove(group)
        if not groups:doc['hooks'].pop(event,None)
    for event,group in desired:
        groups=doc['hooks'].setdefault(event,[])
        if group not in groups:groups.append(deepcopy(group))
    return doc
