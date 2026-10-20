"""Sequencing-owned claim and immutable history facts on a caller-owned connection."""
import json

from apps.sequencing.domain.ownership import OwnershipError


class OwnershipRepository:
    def __init__(self, connection):
        self.connection = connection

    def task(self, task_id):
        row = self.connection.execute(
            'SELECT task_id,state,requirements_snapshot_revision,requirements_statement_revision '
            'FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            raise OwnershipError('task_not_found')
        field = ('requirements_snapshot_revision' if row['requirements_snapshot_revision'] is not None
                 else 'requirements_statement_revision')
        return {'task_id': row['task_id'], 'state': row['state'], 'reference': {field: row[field]}}

    def active_for_task(self, task_id):
        row = self.connection.execute(
            'SELECT claim_id,task_id,claimant,status FROM task_claims WHERE task_id=? AND status=?',
            (task_id, 'active')).fetchone()
        return None if row is None else dict(row)

    def active_for_session(self, claimant):
        row = self.connection.execute(
            'SELECT claim_id,task_id,claimant,status FROM task_claims WHERE claimant=? AND status=?',
            (claimant, 'active')).fetchone()
        return None if row is None else dict(row)

    def inner_intent(self, claim_request_id):
        row = self.connection.execute(
            'SELECT data FROM sequencing_history WHERE event IN (?,?) '
            "AND json_extract(data,'$.claim_request_id')=? ORDER BY seq LIMIT 1",
            ('claim.created', 'claim.switched', claim_request_id)).fetchone()
        return None if row is None else json.loads(row['data'])

    def handoff(self, handoff_id):
        row = self.connection.execute(
            'SELECT data FROM sequencing_history WHERE event IN (?,?) '
            "AND json_extract(data,'$.handoff.handoff_id')=? ORDER BY seq LIMIT 1",
            ('claim.released', 'claim.recovered', handoff_id)).fetchone()
        if row is None:
            raise OwnershipError('handoff_not_found')
        return json.loads(row['data'])['handoff']

    def handoff_consumed(self, handoff_id):
        return self.connection.execute(
            'SELECT 1 FROM sequencing_history WHERE event=? '
            "AND json_extract(data,'$.consumed_handoff_id')=? LIMIT 1",
            ('claim.resumed', handoff_id)).fetchone() is not None

    def current_workspace_id(self, task_id):
        row = self.connection.execute(
            'SELECT workspace_id FROM workspaces WHERE task_id=? AND status<>?',
            (task_id, 'released')).fetchone()
        return None if row is None else row['workspace_id']

    def create_claim(self, claim_id, task_id, claimant):
        self.connection.execute(
            'INSERT INTO task_claims(claim_id,task_id,claimant,status) VALUES(?,?,?,?)',
            (claim_id, task_id, claimant, 'active'))
        self.connection.execute('UPDATE tasks SET state=? WHERE task_id=?', ('claimed', task_id))

    def release_claim(self, claim_id, task_id):
        result = self.connection.execute(
            'UPDATE task_claims SET status=? WHERE claim_id=? AND task_id=? AND status=?',
            ('released', claim_id, task_id, 'active'))
        if result.rowcount != 1:
            raise OwnershipError('claim_owner_mismatch')
        self.connection.execute('UPDATE tasks SET state=? WHERE task_id=?', ('available', task_id))

    def record(self, task_id, actor, event, data, at, workspace_id=None):
        membership = self.connection.execute(
            'SELECT sprint_id FROM sprint_members WHERE task_id=?', (task_id,)).fetchone()
        self.connection.execute(
            'INSERT INTO sequencing_history(task_id,sprint_id,workspace_id,actor,event,data,at) '
            'VALUES(?,?,?,?,?,?,?)',
            (task_id, None if membership is None else membership['sprint_id'], workspace_id,
             actor, event, json.dumps(data, ensure_ascii=False, separators=(',', ':')), at))
