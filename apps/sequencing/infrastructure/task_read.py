"""Common current Task cards and bounded family snapshots on the caller connection."""
import json

from apps.sequencing.domain.duplicates import family_projection
from apps.sequencing.domain.registration import RegistrationError
from apps.sequencing.infrastructure.duplicate_store import DuplicateRepository
from apps.sequencing.infrastructure.execution_facts_store import ExecutionFactsRepository
from apps.sequencing.infrastructure.workspace_store import WorkspaceRepository


class TaskReadRepository:
    def __init__(self, connection):
        self.connection = connection
        self.duplicates = DuplicateRepository(connection)
        self.workspaces = WorkspaceRepository(connection)
        self.facts = ExecutionFactsRepository(connection)

    def cards(self, task_ids, family):
        rows = self.connection.execute(
            'SELECT t.task_id,t.requirements_snapshot_revision,t.requirements_statement_revision,'
            't.state,t.terminal_json,m.sprint_id,c.claim_id,c.claimant,c.status AS claim_status '
            'FROM tasks t LEFT JOIN sprint_members m ON m.task_id=t.task_id '
            "LEFT JOIN task_claims c ON c.task_id=t.task_id AND c.status='active' "
            'WHERE t.task_id IN (SELECT value FROM json_each(?)) ORDER BY t.task_id',
            (json.dumps(task_ids),)).fetchall()
        workspaces = self.workspaces.values(task_ids)
        values = {}
        for row in rows:
            reference = ('requirements_snapshot_revision' if row['requirements_snapshot_revision'] is not None
                         else 'requirements_statement_revision')
            claim = None if row['claim_id'] is None else {
                'claim_id': row['claim_id'], 'task_id': row['task_id'],
                'claimant': row['claimant'], 'status': row['claim_status']}
            value = {'task_id': row['task_id'], reference: row[reference], 'state': row['state'],
                     'sprint_id': row['sprint_id'], 'claim': claim,
                     'workspace': workspaces.get(row['task_id']),
                     'terminal': None if row['terminal_json'] is None else json.loads(row['terminal_json'])}
            if family is not None:
                value['duplicate'] = family_projection(family, row['task_id'])
            values[row['task_id']] = value
        if set(values) != set(task_ids):
            raise RegistrationError('task_not_found')
        return values

    def task(self, task_id):
        family = self.duplicates.family_ids(task_id)
        return self.cards([task_id], family)[task_id]

    def family(self, task_id):
        family = self.duplicates.family_ids(task_id)
        if family is None:
            return None
        identifiers = [member['task_id'] for member in family['members']]
        cards = self.cards(identifiers, family)
        facts = self.facts.values(identifiers)
        return {'parent_id': family['parent_id'], 'members': [
            {'task': cards[identifier], 'execution_facts': facts[identifier]}
            for identifier in identifiers]}
