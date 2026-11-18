"""Pure shared projection mechanics for event-owned initial callback consumers."""
from copy import deepcopy
import math
import re

from ..foundation.errors import DomainError


class InitialHookProjection:
    event_name = 'SessionStart'
    allow_async = False
    wildcard_all = False

    @classmethod
    def logical(cls, document, snapshot):
        result = deepcopy(document)
        if snapshot and snapshot['active']:
            if result.get('hooks', {}).get(cls.event_name) != [snapshot['entry']]:
                raise DomainError(f'{cls.event_name} projection changed; restore its recorded native entry before retry')
            result['hooks'][cls.event_name] = deepcopy(snapshot['groups'])
        return result

    @classmethod
    def physical(cls, document, snapshot):
        result = deepcopy(document)
        result['hooks'][cls.event_name] = [deepcopy(snapshot['entry'])]
        return result

    @classmethod
    def budgets(cls, groups):
        if not isinstance(groups, list) or any(not isinstance(group, dict) or not isinstance(group.get('hooks'), list) for group in groups):
            raise DomainError(f'{cls.event_name} requires an ordered native command-group list')
        handlers = [handler for group in groups for handler in group['hooks']]
        if not handlers:
            raise DomainError(f'{cls.event_name} requires at least one original command')
        for group in groups:
            if not isinstance(group.get('matcher', ''), str):
                raise DomainError(f'{cls.event_name} matcher must be text')
            try:
                if not (cls.wildcard_all and group.get('matcher') == '*'):
                    re.compile(group.get('matcher', ''))
            except re.error as exc:
                raise DomainError(f'Invalid {cls.event_name} matcher; correct its original registration') from exc
        for handler in handlers:
            if not isinstance(handler, dict) or handler.get('type') != 'command' or (handler.get('async', False) and not cls.allow_async):
                raise DomainError(f'Initial {cls.event_name} proxy admits synchronous native commands only')
            if cls.allow_async and type(handler.get('async', False)) is not bool:
                raise DomainError(f'Original {cls.event_name} async must be boolean')
            timeout = handler.get('timeout')
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise DomainError(f'Original {cls.event_name} command requires a finite positive timeout')
            context = handler.get('additionalContextLimit', 0)
            if type(context) is not int or context < 0 or not isinstance(handler.get('command'), str) or not handler['command']:
                raise DomainError(f'Original {cls.event_name} command/context contract is invalid')
        contributors = [h for h in handlers if not h.get('async', False)]
        return {'timeout': math.ceil(sum(h['timeout'] + .25 for h in handlers) + 1),
                'context': sum(h.get('additionalContextLimit', 0) for h in contributors) + max(0, len(contributors) - 1),
                'cleanup_seconds': .25, 'orchestration_seconds': 1}

    @classmethod
    def preserve_member_order(cls, document, candidate, previous, desired):
        old = [group for event, group in previous if event == cls.event_name]
        new = [group for event, group in desired if event == cls.event_name]
        if old:
            groups, inserted = [], False
            for group in document['hooks'][cls.event_name]:
                if group in old:
                    if not inserted:
                        groups.extend(deepcopy(new))
                        inserted = True
                else:
                    groups.append(deepcopy(group))
            candidate['hooks'][cls.event_name] = groups
        return candidate

    @classmethod
    def selected(cls, groups, source):
        if not isinstance(source, str):
            raise DomainError(f'Native {cls.event_name} source must be text')
        return [(i, handler) for i, group in enumerate(groups)
                if not group.get('matcher') or (cls.wildcard_all and group['matcher'] == '*')
                or re.search(group['matcher'], source)
                for handler in group['hooks']]

