"""Establish, observe and assign the caller's own independent Session."""
import uuid

from domain.generic import Problem
from domain.sessions import Session, assignment_input


def missing_session():
    raise Problem('session_not_found', {}, 'ensure:session', 'unavailable')


class SessionOperations:
    def __init__(self, state, resolver, repository, roles, session_factory=None):
        self.state, self.resolver, self.repository = state, resolver, repository
        self.roles = roles
        self.session_factory = session_factory or (lambda: uuid.uuid4().hex)

    def caller(self, *, create):
        native = self.resolver.validate_context()
        self.state.status()
        caller = self.resolver.resolve(native, create=create)
        if caller is None:
            missing_session()
        return caller

    def observed(self, caller):
        session = self.repository.observe(caller.key)
        if session is None:
            missing_session()
        return session

    def status(self):
        return {'session': self.observed(self.caller(create=False)).view()}

    def ensure(self):
        caller = self.caller(create=True)
        existing = self.repository.observe(caller.key)
        if existing is not None:
            return {'session': existing.view(), 'created': False}
        with self.repository.transaction() as unit:
            session, created = self.ensure_in(unit, caller)
        return {'session': session.view(), 'created': created}

    def ensure_in(self, unit, caller):
        existing = unit.observe(caller.key)
        if existing is not None:
            return existing, False
        while True:
            identity = caller.native_session or self.session_factory()
            if unit.exists(identity):
                if caller.native_session is not None:
                    raise Problem('identity_conflict', {'session_id': identity}, 'select:caller')
                continue
            session = Session.initial(identity, caller.mode)
            unit.create(caller.key, session)
            return session, True

    def assign_role(self, value):
        assignment = assignment_input(value, self.roles)
        caller = self.caller(create=False)
        session, changed = self.observed(caller).assign(assignment)
        if not changed:
            return {'session': session.view(), 'changed': False}
        with self.repository.transaction() as unit:
            current = unit.observe(caller.key)
            if current is None:
                missing_session()
            session, changed = current.assign(assignment)
            if changed:
                unit.assign(session)
        return {'session': session.view(), 'changed': changed}
