"""Own-store availability facts within an application-owned read transaction."""
import json

from apps.sequencing.domain.registration import RegistrationError


class EligibilityReadRepository:
    def __init__(self, connection):
        self.connection = connection

    def task_state(self, task_id):
        row = self.connection.execute('SELECT state FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            raise RegistrationError('task_not_found')
        return row['state']

    def predecessors(self, task_id):
        rows = self.connection.execute(
            'SELECT d.predecessor,d.kind,p.state,p.terminal_json FROM sprint_dependencies d '
            'JOIN tasks p ON p.task_id=d.predecessor WHERE d.successor=? '
            'ORDER BY d.predecessor,d.successor,d.kind', (task_id,))
        facts = []
        for row in rows:
            commit = None
            if row['kind'] == 'result' and row['state'] == 'completed' and row['terminal_json'] is not None:
                commit = json.loads(row['terminal_json'])['result_commit']
            facts.append({'predecessor': row['predecessor'], 'kind': row['kind'],
                          'state': row['state'], 'result_commit': commit})
        return facts

    def candidate_ids(self, scope_kind, sprint_id):
        if scope_kind == 'all':
            rows = self.connection.execute('SELECT task_id FROM tasks ORDER BY task_id')
        elif scope_kind == 'independent':
            rows = self.connection.execute(
                'SELECT t.task_id FROM tasks t WHERE NOT EXISTS '
                '(SELECT 1 FROM sprint_members m WHERE m.task_id=t.task_id) ORDER BY t.task_id')
        elif scope_kind == 'sprint':
            if self.connection.execute('SELECT 1 FROM sprints WHERE sprint_id=?', (sprint_id,)).fetchone() is None:
                raise RegistrationError('sprint_not_found')
            rows = self.connection.execute(
                'SELECT task_id FROM sprint_members WHERE sprint_id=? ORDER BY task_id', (sprint_id,))
        else:
            raise RegistrationError('Unsupported Sequencing work selector')
        return [row['task_id'] for row in rows]
