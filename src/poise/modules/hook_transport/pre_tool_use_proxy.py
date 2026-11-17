"""PreToolUse selection and neutral result of the admitted empty routes."""
from ..foundation.errors import DomainError
from .initial_proxy import InitialHookProjection


class InitialPreToolUseProjection(InitialHookProjection):
    event_name = 'PreToolUse'


class InitialPreToolUseResult:
    def __init__(self):
        self.diagnostics = []
        self.failed = False
        self.blocking = False

    def add(self, outcome):
        self.blocking |= outcome['exit_code'] == 2
        self.failed |= outcome['failed']
        self.diagnostics.append(outcome['stderr'])
        if outcome['response'] not in (None, {}):
            raise DomainError('Unsupported nonempty initial PreToolUse response; restore original registrations through their owner')

    def complete(self):
        return {}, b''.join(self.diagnostics), 2 if self.blocking else 1 if self.failed else 0
