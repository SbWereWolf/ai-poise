"""Immutable addressed facts and explicit confirmation decisions, without I/O."""
from copy import deepcopy
import json

from poise.modules.foundation.errors import DomainError


class OutcomeLedger:
    @classmethod
    def restore(cls, outcomes, decisions):
        ledger = cls()
        ledger._saved = {row['outcome_id']: dict(row) for row in outcomes}
        ledger._facts = {}
        ledger._decisions = deepcopy(decisions)
        ledger._new_decisions = []
        return ledger

    def fact(self, outcome_id):
        if outcome_id in self._facts:
            return deepcopy(self._facts[outcome_id])
        row = self._saved.get(outcome_id)
        if row is None:
            raise DomainError(f'unknown outcome: {outcome_id}')
        return {
            'outcome_id': row['outcome_id'], 'task_id': row['task_id'],
            'snapshot_revision': row['snapshot_revision'], 'kind': row['kind'],
            'satisfied_task_requirements': json.loads(row['keys']),
        }

    def _choices(self, task_id, snapshot_revision):
        choices = {}
        for row in self._decisions:
            if (row['task_id'], row['snapshot_revision']) == (task_id, snapshot_revision):
                choices[row['key']] = row['outcome_id']
        return choices

    def choice(self, task_id, snapshot_revision, key):
        return self._choices(task_id, snapshot_revision).get(key)

    def select(self, task_id, snapshot_revision, key, outcome_id):
        if outcome_id is not None:
            if not isinstance(outcome_id, str) or not outcome_id.strip():
                raise DomainError('outcome_id must be a nonempty identifier or null')
            fact = self.fact(outcome_id)
            if (fact['task_id'] != task_id or fact['snapshot_revision'] != snapshot_revision
                    or fact['kind'] != 'positive'
                    or key not in fact['satisfied_task_requirements']):
                raise DomainError('confirmation requires a matching positive outcome for this key')
        choices = self._choices(task_id, snapshot_revision)
        if key in choices and choices[key] == outcome_id:
            return
        sequence = 1 + max((row['sequence'] for row in self._decisions
                            if (row['task_id'], row['snapshot_revision'])
                            == (task_id, snapshot_revision)), default=0)
        row = {'task_id': task_id, 'snapshot_revision': snapshot_revision,
               'sequence': sequence, 'key': key, 'outcome_id': outcome_id}
        self._decisions.append(row)
        self._new_decisions.append(row)

    def record(self, outcome, snapshot, is_current, max_items):
        fields = {'outcome_id', 'task_id', 'snapshot_revision', 'kind',
                  'satisfied_task_requirements'}
        if not isinstance(outcome, dict) or set(outcome) != fields:
            raise DomainError('outcome requires its exact addressed fact fields')
        if not isinstance(outcome['outcome_id'], str) or not outcome['outcome_id'].strip():
            raise DomainError('outcome_id must be a nonempty identifier')
        keys = outcome['satisfied_task_requirements']
        known = {entry['key'] for entry in snapshot['statement']['task_requirements']}
        if (not isinstance(keys, list) or len(keys) > max_items
                or any(not isinstance(key, str) or key not in known for key in keys)
                or len(set(keys)) != len(keys)):
            raise DomainError('outcome keys must be unique keys of the addressed snapshot')
        if outcome['kind'] not in ('positive', 'negative', 'cancelled'):
            raise DomainError('outcome kind must be positive, negative or cancelled')
        if outcome['kind'] != 'positive' and keys:
            raise DomainError('negative and cancelled outcomes have no satisfied keys')
        identifier = outcome['outcome_id']
        if identifier in self._saved or identifier in self._facts:
            if self.fact(identifier) != outcome:
                raise DomainError('outcome_id already identifies a different immutable fact')
            return
        self._facts[identifier] = deepcopy(outcome)
        if is_current and outcome['kind'] == 'positive':
            choices = self._choices(outcome['task_id'], outcome['snapshot_revision'])
            for key in keys:
                if key not in choices:
                    self.select(outcome['task_id'], outcome['snapshot_revision'], key, identifier)

    def state(self, snapshot):
        task_id, revision = snapshot['task_id'], snapshot['snapshot_revision']
        identifiers = {identifier for identifier, row in self._saved.items()
                       if (row['task_id'], row['snapshot_revision']) == (task_id, revision)}
        identifiers.update(identifier for identifier, fact in self._facts.items()
                           if (fact['task_id'], fact['snapshot_revision']) == (task_id, revision))
        choices = self._choices(task_id, revision)
        return {
            'task_id': task_id, 'snapshot_revision': revision,
            'statement_revision': snapshot['statement_revision'],
            'outcomes': [self.fact(identifier) for identifier in sorted(identifiers)],
            'confirmations': [{'key': key, 'outcome_id': choices.get(key)} for key in sorted(
                entry['key'] for entry in snapshot['statement']['task_requirements'])],
            'decisions': [{field: row[field] for field in ('sequence', 'key', 'outcome_id')}
                          for row in self._decisions
                          if (row['task_id'], row['snapshot_revision']) == (task_id, revision)],
        }

    def pending(self):
        return {'outcomes': deepcopy(list(self._facts.values())),
                'decisions': deepcopy(self._new_decisions)}
