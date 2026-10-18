"""Registration persistence on an application-owned SQLite transaction."""
from dataclasses import asdict
import json

from apps.sequencing.domain.registration import (
    Dependency, RegistrationError, RequirementsReference, SprintDefinition,
)


class RegistrationRepository:
    def __init__(self, connection):
        self.connection = connection

    def register_task(self, task_id, reference):
        row = self.connection.execute(
            'SELECT requirements_snapshot_revision,requirements_statement_revision FROM tasks WHERE task_id=?',
            (task_id,),
        ).fetchone()
        if row is not None:
            kind = 'snapshot' if row['requirements_snapshot_revision'] is not None else 'statement'
            saved = RequirementsReference(kind, row['requirements_' + kind + '_revision'])
            reference.require_same(saved)
            return False
        self.connection.execute(
            'INSERT INTO tasks(task_id,requirements_snapshot_revision,requirements_statement_revision,state,terminal_json) '
            'VALUES(?,?,?,?,?)',
            (task_id, reference.revision if reference.kind == 'snapshot' else None,
             reference.revision if reference.kind == 'statement' else None, 'available', None),
        )
        return True

    def sprint_value(self, sprint_id):
        row = self.connection.execute('SELECT revision FROM sprints WHERE sprint_id=?', (sprint_id,)).fetchone()
        if row is None:
            raise RegistrationError('sprint_not_found')
        members = [row['task_id'] for row in self.connection.execute(
            'SELECT task_id FROM sprint_members WHERE sprint_id=? ORDER BY task_id', (sprint_id,))]
        edges = [dict(row) for row in self.connection.execute(
            'SELECT predecessor,successor,kind FROM sprint_dependencies WHERE sprint_id=? '
            'ORDER BY predecessor,successor,kind', (sprint_id,))]
        return {'sprint_id': sprint_id, 'sprint_revision': row['revision'],
                'members': members, 'dependencies': edges}

    def register_sprint(self, definition):
        prior = self.connection.execute(
            'SELECT revision FROM sprints WHERE sprint_id=?', (definition.sprint_id,),
        ).fetchone()
        if prior is not None:
            value = self.sprint_value(definition.sprint_id)
            existing = SprintDefinition.create(value['sprint_id'], value['members'],
                [Dependency(**edge) for edge in value['dependencies']])
            definition.require_same(existing)
            return False, prior['revision']
        slots = ','.join('?' for _ in definition.members)
        known = {row['task_id'] for row in self.connection.execute(
            'SELECT task_id FROM tasks WHERE task_id IN (' + slots + ')', definition.members)}
        definition.require_registered(known)
        memberships = {row['task_id']: row['sprint_id'] for row in self.connection.execute(
            'SELECT task_id,sprint_id FROM sprint_members WHERE task_id IN (' + slots + ')', definition.members)}
        definition.require_memberships(memberships)
        self.connection.execute('INSERT INTO sprints(sprint_id,revision,state) VALUES(?,?,?)',
                                (definition.sprint_id, 1, 'draft'))
        self.connection.executemany('INSERT INTO sprint_members(sprint_id,task_id) VALUES(?,?)',
            [(definition.sprint_id, identifier) for identifier in definition.members])
        self.connection.executemany(
            'INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) VALUES(?,?,?,?)',
            [(definition.sprint_id, edge.predecessor, edge.successor, edge.kind) for edge in definition.dependencies])
        value = {'sprint_id': definition.sprint_id, 'sprint_revision': 1,
                 'members': list(definition.members), 'dependencies': [asdict(edge) for edge in definition.dependencies]}
        self.connection.execute('INSERT INTO sprint_revisions(sprint_id,revision,state,data) VALUES(?,?,?,?)',
            (definition.sprint_id, 1, 'draft', json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))))
        return True, 1
