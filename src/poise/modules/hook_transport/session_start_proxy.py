"""Pure projection and response policy for the initial native SessionStart proxy."""
from copy import deepcopy
import math
import re

from ..foundation.errors import DomainError


class InitialSessionStartProjection:
    @staticmethod
    def logical(document, snapshot):
        result = deepcopy(document)
        if snapshot and snapshot['active']:
            if result.get('hooks', {}).get('SessionStart') != [snapshot['entry']]:
                raise DomainError('SessionStart projection changed; restore its recorded native entry before retry')
            result['hooks']['SessionStart'] = deepcopy(snapshot['groups'])
        return result

    @staticmethod
    def physical(document, snapshot):
        result = deepcopy(document)
        result['hooks']['SessionStart'] = [deepcopy(snapshot['entry'])]
        return result

    @staticmethod
    def budgets(groups):
        if not isinstance(groups, list) or any(not isinstance(group, dict) or not isinstance(group.get('hooks'), list) for group in groups):
            raise DomainError('SessionStart requires an ordered native command-group list')
        handlers = [handler for group in groups for handler in group['hooks']]
        if not handlers:
            raise DomainError('SessionStart requires at least one original command')
        for group in groups:
            if not isinstance(group.get('matcher', ''), str):
                raise DomainError('SessionStart matcher must be text')
            try:
                re.compile(group.get('matcher', ''))
            except re.error as exc:
                raise DomainError('Invalid SessionStart matcher; correct its original registration') from exc
        for handler in handlers:
            if not isinstance(handler, dict) or handler.get('type') != 'command' or handler.get('async', False):
                raise DomainError('Initial SessionStart proxy admits synchronous native commands only')
            timeout = handler.get('timeout')
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise DomainError('Original SessionStart command requires a finite positive timeout')
            context = handler.get('additionalContextLimit', 0)
            if type(context) is not int or context < 0 or not isinstance(handler.get('command'), str) or not handler['command']:
                raise DomainError('Original SessionStart command/context contract is invalid')
        return {'timeout': math.ceil(sum(h['timeout'] + .25 for h in handlers) + 1),
                'context': sum(h.get('additionalContextLimit', 0) for h in handlers) + len(handlers) - 1,
                'cleanup_seconds': .25, 'orchestration_seconds': 1}

    @staticmethod
    def preserve_member_order(document, candidate, previous, desired):
        old = [group for event, group in previous if event == 'SessionStart']
        new = [group for event, group in desired if event == 'SessionStart']
        if old:
            groups, inserted = [], False
            for group in document['hooks']['SessionStart']:
                if group in old:
                    if not inserted:
                        groups.extend(deepcopy(new))
                        inserted = True
                else:
                    groups.append(deepcopy(group))
            candidate['hooks']['SessionStart'] = groups
        return candidate

    @staticmethod
    def selected(groups, source):
        if not isinstance(source, str):
            raise DomainError('Native SessionStart source must be text')
        return [(i, handler) for i, group in enumerate(groups)
                if not group.get('matcher') or re.search(group['matcher'], source)
                for handler in group['hooks']]


class InitialSessionStartResult:
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
                if not isinstance(value, dict) or value.get('hookEventName') != 'SessionStart':
                    raise DomainError('Invalid SessionStart native response')
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
                hook.update(hookEventName='SessionStart', additionalContext='\n'.join(values))
            else:
                result[key] = '\n'.join(values)
        if hook:
            hook.setdefault('hookEventName', 'SessionStart')
            result['hookSpecificOutput'] = hook
        return result, b''.join(self.diagnostics), 2 if self.blocking else 1 if self.failed else 0
