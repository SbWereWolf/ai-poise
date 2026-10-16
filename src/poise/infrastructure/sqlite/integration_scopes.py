"""Owned append-only placement authority for unchanged terminal integration proof."""
from datetime import datetime, timezone
import json

from ...modules.foundation.errors import PoiseError
from ...modules.result_integration.domain import (
    IntegrationHistoricalBackupScope, IntegrationRun, IntegrationScopeRelocation,
    integration_proof_digest,
)
from .tasks import encode


_EVENT = "integration.backup_scope_relocated"


class SqliteIntegrationScopeRepository:
    def __init__(self, connection):
        self.db = connection

    @staticmethod
    def _edges(rows):
        result = []
        for row in rows:
            edge = IntegrationScopeRelocation.parse(json.loads(row['data']))
            if row['task_id'] != edge.task_id or row['session_id'] != edge.relocation_request_id:
                raise PoiseError('Integration placement journal identity differs')
            result.append((row['seq'], edge))
        return result

    def records(self, task_ids):
        if not task_ids:
            return []
        slots = ','.join('?' for _ in task_ids)
        return self._edges(self.db.execute(
            'SELECT seq,task_id,session_id,data FROM journal WHERE event=? '
            f'AND task_id IN ({slots}) ORDER BY seq', (_EVENT, *task_ids)).fetchall())

    @staticmethod
    def _placements(tasks, executions, records, source, target, request_id, publication_target):
        versions = {row['id']: row['version'] for row in tasks}
        prior = [(seq, edge) for seq, edge in records if edge.relocation_request_id != request_id]
        scope = IntegrationHistoricalBackupScope(source, versions, prior)
        placements = {}
        for row in tasks:
            task_id = row['id']
            if row['status'] != 'completed' or row['claimed_by'] is not None:
                continue
            execution = executions.get(task_id)
            if execution is None:
                continue
            proof, report = execution['pending'], execution['last_report']
            if (not isinstance(report, dict) or not IntegrationRun.terminal_receipt_for(
                    proof, task_id=task_id, result_commit=report['commit'],
                    task_branch=execution['branch'], task_worktree=execution['worktree'],
                    publication_target=publication_target, backup_scope=scope)):
                continue
            placements[task_id] = IntegrationScopeRelocation(
                request_id, task_id, row['version'], proof['intent']['request_id'],
                integration_proof_digest(proof), source, target)
        current = [edge for _, edge in records if edge.relocation_request_id == request_id]
        if (len({edge.task_id for edge in current}) != len(current)
                or any(placements.get(edge.task_id) != edge for edge in current)):
            raise PoiseError('Integration placement request has conflicting saved proof')
        return placements

    def relocate(self, source, target, request_id, publication_target):
        tasks = [dict(row) for row in self.db.execute(
            'SELECT id,status,claimed_by,version FROM tasks ORDER BY id')]
        executions = {row['task_id']: json.loads(row['data']) for row in self.db.execute(
            'SELECT task_id,data FROM task_execution')}
        records = self.records([row['id'] for row in tasks])
        placements = self._placements(
            tasks, executions, records, source, target, request_id, publication_target)
        saved = {edge.task_id for _, edge in records if edge.relocation_request_id == request_id}
        for task_id, edge in placements.items():
            if task_id in saved:
                continue
            self.db.execute(
                'INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                (datetime.now(timezone.utc).isoformat(), request_id, task_id, _EVENT, encode(edge.to_dict())))

    @classmethod
    def normalize_relocation(cls, before, actual, source, target, request_id, publication_target):
        """Consume only exact admitted effects in a comparison, never stored data."""
        original, current = before['tables'], actual['tables']
        sequence = max((row['seq'] for row in original['journal']), default=0)
        appended = [row for row in current['journal'] if row['seq'] > sequence]
        owned = [row for row in appended if row['event'] == _EVENT]
        if not owned:
            return
        prior = cls._edges([row for row in original['journal'] if row['event'] == _EVENT])
        executions = {row['task_id']: json.loads(row['data']) for row in original['task_execution']}
        expected = cls._placements(
            original['tasks'], executions, prior, source, target, request_id, publication_target)
        edges = cls._edges(owned)
        if (set(expected) != {edge.task_id for _, edge in edges}
                or len({edge.task_id for _, edge in edges}) != len(edges)
                or any(expected.get(edge.task_id) != edge for _, edge in edges)):
            raise PoiseError('Integration placement differs from admitted original proof')
        identity = actual['row_identity_fields']['journal']
        for row, (_, edge) in zip(owned, edges, strict=True):
            if (row['data'] != encode(edge.to_dict())
                    or (identity is not None and row[identity] != row['seq'])
                    or datetime.fromisoformat(row['at']).tzinfo is None):
                raise PoiseError('Integration placement has unconfirmed journal fields')
        remaining = [row for row in appended if row['event'] != _EVENT]
        if remaining and min(row['seq'] for row in remaining) < max(row['seq'] for row in owned):
            raise PoiseError('Integration placement was appended outside its admitted phase')
        maximum = max(row['seq'] for row in current['journal'])
        allocation = [row for row in current['sqlite_sequence'] if row['name'] == 'journal']
        if len(allocation) != 1 or allocation[0]['seq'] != maximum:
            raise PoiseError('Integration placement journal allocation differs')
        current['journal'] = [row for row in current['journal'] if row not in owned]
        if not remaining:
            previous = next((row for row in original['sqlite_sequence'] if row['name'] == 'journal'), None)
            if previous is None:
                current['sqlite_sequence'] = [row for row in current['sqlite_sequence'] if row['name'] != 'journal']
            else:
                allocation[0]['seq'] = previous['seq']
