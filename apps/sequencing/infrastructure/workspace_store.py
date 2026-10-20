"""Workspace facts and request slots on a caller-owned SQLite connection."""
import json

from apps.sequencing.domain.workspace import WorkspaceError
from apps.sequencing.infrastructure.ownership_store import OwnershipRepository


class WorkspaceRepository:
    def __init__(self, connection):
        self.connection = connection
        self.ownership = OwnershipRepository(connection)

    def request(self, request_id, digest):
        row = self.connection.execute(
            'SELECT digest,response_json FROM requests WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            return None
        if row['digest'] != digest:
            raise WorkspaceError('request_reuse_conflict')
        return json.loads(row['response_json'])

    def roots(self, workspace_id):
        return [dict(row) for row in self.connection.execute(
            'SELECT kind,position,path FROM workspace_roots WHERE workspace_id=? ORDER BY kind,position',
            (workspace_id,))]

    def saved(self, task_id):
        rows = self.connection.execute(
            'SELECT workspace_id,task_id,repository_id,base_commit,branch,status,read_only '
            'FROM workspaces WHERE task_id=?', (task_id,)).fetchall()
        current = [row for row in rows if row['status'] != 'released']
        if len(current) > 1:
            raise WorkspaceError('workspace_conflict')
        row = current[0] if current else (rows[0] if rows else None)
        if row is None:
            return None
        return {**dict(row), 'read_only': bool(row['read_only']),
                'roots': self.roots(row['workspace_id'])}

    def value(self, task_id):
        row = self.connection.execute(
            'SELECT workspace_id,task_id,repository_id,base_commit,branch,status,read_only '
            'FROM workspaces WHERE task_id=? AND status<>?', (task_id, 'released')).fetchone()
        if row is None:
            return None
        roots = self.roots(row['workspace_id'])
        binding = self.connection.execute(
            'SELECT session_id FROM workspace_bindings WHERE workspace_id=?',
            (row['workspace_id'],)).fetchone()
        return {**dict(row), 'read_only': bool(row['read_only']), 'roots': roots,
                'path': next((root['path'] for root in roots if root['kind'] == 'code'), None),
                'owner_session_id': None if binding is None else binding['session_id']}

    def insert_workspace(self, workspace):
        columns = ('workspace_id', 'task_id', 'repository_id', 'base_commit', 'branch', 'status', 'read_only')
        self.connection.execute(
            'INSERT INTO workspaces(workspace_id,task_id,repository_id,base_commit,branch,status,read_only) '
            'VALUES(?,?,?,?,?,?,?)', tuple(workspace[key] for key in columns))
        self.connection.executemany(
            'INSERT INTO workspace_roots(workspace_id,kind,position,path) VALUES(?,?,?,?)',
            [(workspace['workspace_id'], root['kind'], root['position'], root['path'])
             for root in workspace['roots']])

    def save_request(self, request_id, digest, response, *, finish=False):
        encoded = json.dumps(response, ensure_ascii=False, separators=(',', ':'))
        if finish:
            self.connection.execute('UPDATE requests SET response_json=? WHERE request_id=? AND digest=?',
                                    (encoded, request_id, digest))
        else:
            self.connection.execute('INSERT INTO requests(request_id,digest,response_json) VALUES(?,?,?)',
                                    (request_id, digest, encoded))

    def mark_ready(self, workspace_id):
        self.connection.execute('UPDATE workspaces SET status=? WHERE workspace_id=?',
                                ('ready', workspace_id))

    def bump_revision(self):
        revision = int(self.connection.execute(
            "SELECT value FROM meta WHERE key='revision'").fetchone()[0]) + 1
        self.connection.execute("UPDATE meta SET value=? WHERE key='revision'", (revision,))
        return revision

    def record(self, workspace, actor, event, data, at):
        self.ownership.record(workspace['task_id'], actor, event, data, at, workspace['workspace_id'])
