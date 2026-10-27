"""Own native event delivery, independent from Task context and lifecycle."""
import uuid

from application.sessions import missing_session
from domain.generic import Problem
from domain.hooks import HookBinding, NativeEvent, native_response
from domain.sessions import Caller


class HookEvents:
    def __init__(self, settings, definition, sessions, registry, files):
        self.settings, self.definition = settings, definition
        self.sessions, self.registry, self.files = sessions, registry, files

    def response(self, session, binding, event, replayed):
        return {'session': session.view(), 'binding': binding.view(), 'event': event,
                'native_response': native_response(self.definition, event, session), 'replayed': replayed}

    def handle(self, value):
        event = NativeEvent.parse(value, self.definition)
        event = event.at(self.files.checkout(event.workspace, '/input/event/cwd'))
        caller = Caller(self.settings.installation_id, 'native', event.session_id)
        self.sessions.state.status()
        session, binding, stored = self.registry.observe(caller.key, self.definition.id, event.id)
        if stored is not None:
            return self.response(session, binding, stored, True)
        if event.kind != 'SessionStart':
            if session is None:
                missing_session()
            if binding is None:
                raise Problem('hook_not_found', {}, 'start:hook', 'unavailable')
        with self.registry.transaction() as unit:
            if event.kind == 'SessionStart':
                session, _ = self.sessions.ensure_in(unit.sessions, caller)
            else:
                session = unit.sessions.observe(caller.key)
                if session is None:
                    missing_session()
            binding = unit.binding(session.id, self.definition.id)
            if binding is None:
                if event.kind != 'SessionStart':
                    raise Problem('hook_not_found', {}, 'start:hook', 'unavailable')
                binding = HookBinding(uuid.uuid4().hex, session.id, self.definition.id)
                unit.create_binding(binding)
            stored = unit.find_event(binding, event.id)
            replayed = stored is not None
            if stored is None:
                stored = unit.append_event(binding, event)
        return self.response(session, binding, stored, replayed)
