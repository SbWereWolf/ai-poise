"""Selected hook definition, event and binding values without runtime dependencies."""
from dataclasses import dataclass, replace
from string import Formatter

from domain.generic import Problem, argv, environment, exact_fields, invalid, text
from domain.state import invalid_config, required


EVENTS = {'SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd'}
SOURCES = {'startup', 'resume', 'clear', 'compact', 'fork'}


def configured_text(value, pointer):
    try:
        return text(value, pointer)
    except Problem:
        invalid_config(pointer)


def template(value, pointer):
    configured_text(value, pointer)
    try:
        parts = list(Formatter().parse(value))
        fields = {field for _, field, _, _ in parts if field is not None}
        if fields != {'session_id', 'workspace'} or any(spec or conversion for _, _, spec, conversion in parts):
            invalid_config(pointer)
    except ValueError:
        invalid_config(pointer)
    return value


@dataclass(frozen=True)
class HookDefinition:
    id: str
    events: tuple[str, ...]
    context_template: str
    stop_template: str
    generic_config: str | None
    external: dict | None
    probes: list

    @classmethod
    def parse(cls, raw):
        value = required(raw, {'id', 'events', 'context_template', 'stop_template',
                               'generic_config', 'external', 'probes'}, '/hooks')
        identity = configured_text(value['id'], '/hooks/id')
        events = value['events']
        if (not isinstance(events, list) or not events or
                any(not isinstance(item, str) or item not in EVENTS for item in events) or
                len(set(events)) != len(events) or 'SessionStart' not in events):
            invalid_config('/hooks/events')
        context = template(value['context_template'], '/hooks/context_template')
        stop = template(value['stop_template'], '/hooks/stop_template')
        generic = value['generic_config']
        if generic is not None:
            configured_text(generic, '/hooks/generic_config')
        external = value['external']
        if external is not None:
            selected = required(external, {'argv', 'cwd', 'environment'}, '/hooks/external')
            try:
                external = {'argv': argv(selected['argv'], '/hooks/external'),
                            'cwd': text(selected['cwd'], '/hooks/external'),
                            'environment': environment(selected['environment'], '/hooks/external')}
            except Problem:
                invalid_config('/hooks/external')
        probes = value['probes']
        if not isinstance(probes, list):
            invalid_config('/hooks/probes')
        if generic is None and (external is not None or probes):
            invalid_config('/hooks/generic_config')
        return cls(identity, tuple(events), context, stop, generic, external, probes)


@dataclass(frozen=True)
class NativeEvent:
    id: str
    kind: str
    session_id: str
    workspace: str
    turn_id: str | None
    source: str | None
    reason: str | None

    @classmethod
    def parse(cls, value, definition):
        exact_fields(value, {'event_id', 'event'}, '/input')
        identity = text(value['event_id'], '/input/event_id')
        native = value['event']
        if not isinstance(native, dict):
            invalid('/input/event')
        kind = native.get('hook_event_name')
        if not isinstance(kind, str) or kind not in definition.events:
            invalid('/input/event/hook_event_name')
        session = text(native.get('session_id'), '/input/event/session_id')
        workspace = text(native.get('cwd'), '/input/event/cwd')
        if 'agent_id' in native:
            invalid('/input/event/agent_id')
        turn, source, reason = None, None, None
        if kind == 'SessionStart':
            source = native.get('source')
            if not isinstance(source, str) or source not in SOURCES:
                invalid('/input/event/source')
        elif kind in ('UserPromptSubmit', 'Stop'):
            turn = text(native.get('turn_id'), '/input/event/turn_id')
            if kind == 'UserPromptSubmit' and not isinstance(native.get('prompt'), str):
                invalid('/input/event/prompt')
        else:
            reason = native.get('reason')
            if reason != 'other':
                invalid('/input/event/reason')
        return cls(identity, kind, session, workspace, turn, source, reason)

    def at(self, workspace):
        return replace(self, workspace=workspace)

    def data(self):
        return {'workspace': self.workspace, 'turn_id': self.turn_id,
                'source': self.source, 'reason': self.reason}


@dataclass(frozen=True)
class HookBinding:
    id: str
    session_id: str
    definition_id: str

    def view(self):
        return {'id': self.id, 'session_id': self.session_id, 'definition_id': self.definition_id}


def native_response(definition, event, session):
    values = {'session_id': session.id, 'workspace': event['data']['workspace']}
    kind = event['event']
    if kind in ('SessionStart', 'UserPromptSubmit'):
        return {'hookSpecificOutput': {'hookEventName': kind,
                'additionalContext': definition.context_template.format(**values)}}
    if kind == 'Stop':
        return {'systemMessage': definition.stop_template.format(**values)}
    return {}
