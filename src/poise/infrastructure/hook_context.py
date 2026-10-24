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
        self.parts = ([result['hookSpecificOutput']['additionalContext']]
                      if 'hookSpecificOutput' in result else [])

    def _gather(self):
        # Selected handlers already produced every contribution. Presentation
        # never probes capabilities or constructs optional business owners.
        self.collected = True

    def available_bytes(self):
        self._gather()
        return self.limit - len(('\n'.join(self.parts) + '\n').encode('utf-8'))

    def complete(self, card=None):
        self._gather()
        if self.limit is None:
            return self.result
        if not self.parts and card is None:
            return self.result
        parts = self.parts + ([] if card is None else [card])
        context = '\n'.join(parts)
        if len(context.encode('utf-8')) > self.limit:
            raise PoiseError('Native context exceeds its configured UTF-8 budget; increase context_limit or reduce configured summary budgets')
        self.result['hookSpecificOutput'] = {
            **self.result.get('hookSpecificOutput', {}),
            'hookEventName': self.native['event'], 'additionalContext': context}
        return self.result
