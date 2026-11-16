"""Coordinate distinct native callbacks, not internal response middleware."""
from ..modules.hook_transport.session_start_proxy import (
    InitialSessionStartProjection, InitialSessionStartResult,
)
from ..modules.foundation.errors import DomainError


class InitialSessionStartProxy:
    def __init__(self, executor):
        self.executor = executor

    def run(self, snapshot, raw, event):
        self.executor.begin(snapshot, raw, event)
        result = InitialSessionStartResult()
        for position, handler in InitialSessionStartProjection.selected(snapshot['groups'], event.get('source', '')):
            outcome = self.executor.execute(position, handler)
            try:
                result.add(outcome)
            except DomainError as exc:
                result.failed = True
                result.diagnostics.append((str(exc) + '\n').encode())
        return result.complete()
