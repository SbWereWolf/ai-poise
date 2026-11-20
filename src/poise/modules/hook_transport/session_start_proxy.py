"""SessionStart-specific callback selection and native response policy."""
from copy import deepcopy

from ..foundation.errors import DomainError
from .initial_proxy import InitialHookProjection


class InitialSessionStartProjection(InitialHookProjection):
    event_name = 'SessionStart'


class InitialSessionStartResult:
    event_name = 'SessionStart'

    def __init__(self):
        self.response, self.parts, self.diagnostics = {}, {}, []
        self.failed, self.blocking = False, False

    def add(self, outcome):
        self.blocking |= outcome['exit_code'] == 2
        self.failed |= outcome['failed']
        self.diagnostics.append(outcome['stderr'])
        result = outcome['response']
        if result is None:
            return
        for key, value in result.items():
            if key == 'hookSpecificOutput':
                if not isinstance(value, dict) or value.get('hookEventName') != self.event_name:
                    raise DomainError('Invalid ' + self.event_name + ' native response')
                for field, content in value.items():
                    if field == 'additionalContext':
                        self._part(field, content)
                    elif field != 'hookEventName':
                        self._same('hookSpecificOutput.' + field, content)
            elif key in ('systemMessage', 'stopReason'):
                self._part(key, value)
            elif key == 'continue':
                if type(value) is not bool:
                    raise DomainError('Native continue must be a boolean')
                self.response[key] = self.response.get(key, True) and value
            else:
                self._same(key, value)

    def _same(self, key, value):
        if key in self.response and self.response[key] != value:
            raise DomainError('Conflicting native response field: ' + key)
        self.response[key] = value

    def _part(self, key, value):
        if not isinstance(value, str):
            raise DomainError('Native response contribution must be text: ' + key)
        if value:
            self.parts.setdefault(key, []).append(value)

    def complete(self):
        result = deepcopy(self.response)
        hook = {key.split('.', 1)[1]: result.pop(key) for key in list(result)
                if key.startswith('hookSpecificOutput.')}
        for key, values in self.parts.items():
            if key == 'additionalContext':
                hook.update(hookEventName=self.event_name, additionalContext='\n'.join(values))
            else:
                result[key] = '\n'.join(values)
        if hook:
            hook.setdefault('hookEventName', self.event_name)
            result['hookSpecificOutput'] = hook
        return result, b''.join(self.diagnostics), 2 if self.blocking else 1 if self.failed else 0
