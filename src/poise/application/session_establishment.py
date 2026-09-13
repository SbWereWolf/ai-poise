"""Application owner for establishing exactly one session per caller."""
from __future__ import annotations

import uuid

from ..common import PoiseError
from ..modules.session_establishment.domain import EstablishedSession


class SessionEstablisher:
    def __init__(self, registry, session_factory=None):
        self.registry = registry
        self.session_factory = session_factory or (lambda: uuid.uuid4().hex)

    @staticmethod
    def _result(caller, session):
        origin = 'native' if caller.native_session == session else 'generated'
        return EstablishedSession(session, origin)

    def establish(self, caller, inventory, preferred_session=None):
        existing = self.registry.existing(caller.key, inventory)
        if existing is not None:
            return self._result(caller, existing)

        fixed = preferred_session if preferred_session is not None else caller.native_session
        if fixed is not None:
            session = self.registry.reserve(caller.key, fixed, inventory)
            if session is None:
                raise PoiseError('Native session is already assigned to another caller')
            return self._result(caller, session)

        while True:
            # Candidate generation deliberately happens outside the registry lock.
            candidate = self.session_factory()
            session = self.registry.reserve(caller.key, candidate, inventory)
            if session is not None:
                return self._result(caller, session)
