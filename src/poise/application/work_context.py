"""Capture and continue current work; adapters own all I/O and native authority."""
from copy import deepcopy

from ..modules.work_context.domain import (
    CHECKPOINT_SCHEMA, CARD_SCHEMA, ContextOutcome, compare_observations, continuation_actions, render_card,
)
from ..modules.work_context.ports import CheckpointStore, ContextFacts, ContextSession


class WorkContext:
    def __init__(self, policy, session: ContextSession, store: CheckpointStore, facts: ContextFacts):
        self.policy, self.session, self.store, self.facts = policy, session, store, facts

    def capture(self, event):
        existing = self.store.load()
        if existing.value is not None and existing.value['event'] == event.public():
            return ContextOutcome(None, True)
        generation = self.session.context()
        facts = self.facts.observe()
        self.session.revalidate(generation)
        record = dict(schema=CHECKPOINT_SCHEMA, scope=self.session.scope.public(), event=event.public(),
                      captured=facts, consumption=dict(state='pending'))
        try:
            self.store.save(record, generation)
        except OSError:
            return ContextOutcome(None, False, 'checkpoint_write_failed')
        return ContextOutcome(None, True)

    def compact(self):
        self.session.advance()
        return ContextOutcome(None, True)

    def clear(self):
        generation = self.session.advance()
        self.store.clear(generation)

    def restore(self, event, available_bytes):
        if event.source == 'clear':
            self.clear()
        elif event.source == 'compact':
            self.session.advance()
        generation = self.session.context()
        checkpoint = self.store.load()
        previous = None if checkpoint.value is None else checkpoint.value['captured']
        facts = self.facts.observe()
        self.session.revalidate(generation)
        state = checkpoint.state
        consumed_here = (state == 'used'
                         and checkpoint.value['consumption']['event_id'] == event.id)
        compared = compare_observations(previous, facts)
        card = dict(schema=CARD_SCHEMA, status='ready', scope=self.session.scope.public(),
                    checkpoint=dict(state=state, ref=self.store.ref),
                    generation=dict(before=None if previous is None else previous['coverage']['generation'],
                                    current=generation),
                    git=compared['git'], instructions=compared['instructions'],
                    coverage=facts['coverage'], check=facts['check'], work=facts['work'],
                    omitted={}, actions=[])
        card['actions'] = continuation_actions(card, facts['instructions'].get('materials', []), self.policy)
        if state in ('missing', 'invalid', 'unavailable'):
            card['status'] = 'partial'
            card['actions'].append('restore:checkpoint; use current references and capture at the next PreCompact')
        for owner in ('git', 'instructions', 'coverage', 'check', 'work'):
            if card[owner]['status'] in ('unavailable', 'needs_inputs'):
                card['status'] = 'partial'
                addressed_instruction = (owner == 'instructions' and card[owner]['status'] == 'needs_inputs'
                                         and bool(card[owner]['changed']))
                if not addressed_instruction:
                    card['actions'].append('restore:' + owner + '; read its current owner and retry genuine native delivery')
        if state == 'pending' or consumed_here:
            card['checkpoint']['state'] = 'restored'
        card = render_card(card, self.policy, available_bytes)
        if state == 'pending':
            record = deepcopy(checkpoint.value)
            record['consumption'] = dict(state='used', event_id=event.id)
            try:
                self.store.save(record, generation)
            except (OSError, ValueError) as error:
                card['status'] = 'partial'
                card['checkpoint']['state'] = 'pending'
                card['actions'].append('retry:saved-native-delivery; checkpoint consumption failed: ' + type(error).__name__)
                return ContextOutcome(render_card(card, self.policy, available_bytes), False, 'checkpoint_consume_failed')
        return ContextOutcome(card, True)
