"""One native context assembly and UTF-8 budget for all configured producers."""
from ..common import PoiseError


class NativeContextPresentation:
    def __init__(self, service, result, definition, native, binding, deadline=None):
        self.result, self.native = result, native
        self.service, self.definition, self.binding, self.deadline = service, definition, binding, deadline
        self.collected = False
        self.limit = None
        self.parts = []
        if native['event'] not in ('SessionStart', 'UserPromptSubmit'):
            return
        self.limit = next(e['context_limit'] for e in definition.data['events'] if e['event'] == native['event'])
        self.parts = [result['hookSpecificOutput']['additionalContext']]

    def _gather(self):
        if self.collected or self.limit is None:
            return
        # A failed observation remains a failed observation; completing a
        # warning must not launch that same capability probe a second time.
        self.collected = True
        service, definition, native, binding, deadline = (
            self.service, self.definition, self.native, self.binding, self.deadline)
        settings = service.settings
        if 'project_launcher' in settings.raw:
            self.parts.append(settings.raw['project_launcher']['context_template'].format(launcher=binding['launcher']))
        if native['event'] == 'SessionStart':
            from .clock import SystemClock
            options = settings.consumer
            readiness = (service._readiness(binding) if deadline is None or options is None else
                         service._readiness(binding, deadline=deadline-options['completion_reserve_seconds'],
                             cleanup_seconds=options['cleanup_seconds'], poll_seconds=options['poll_seconds']))
            summary = readiness.refresh(binding['session_id'], native['cwd'], definition.data['probes'], SystemClock()).to_dict()
            options = settings.require_summary()
            lines = [options['header_template'].format(ready=summary['ready'], checked_at=summary['checked_at'],
                     workspace=summary['workspace'], snapshot_path=str(readiness.store.path))]
            lines.extend(options['item_template'].format(**item) for item in summary['observations'])
            self.parts.extend(lines)

    def available_bytes(self):
        self._gather()
        return self.limit - len(('\n'.join(self.parts) + '\n').encode('utf-8'))

    def complete(self, card=None):
        self._gather()
        if self.limit is None:
            return self.result
        parts = self.parts + ([] if card is None else [card])
        context = '\n'.join(parts)
        if len(context.encode('utf-8')) > self.limit:
            raise PoiseError('Native context exceeds its configured UTF-8 budget; increase context_limit or reduce configured summary budgets')
        self.result['hookSpecificOutput']['additionalContext'] = context
        return self.result
