"""Explicit, guarded conversion of current Task/Sprint workflow fields.

This owner is invoked for the breaking format change, never on ordinary reads.
Opaque historical payloads and every non-format column remain unchanged.
"""
from copy import deepcopy
import hashlib
import json

from ..modules.foundation.errors import PoiseError


def _rename(document, old, new):
    if old in document:
        if new in document:
            raise PoiseError(f'Workflow key collision: {old}/{new}')
        document[new] = document.pop(old)


def _fields(document, key):
    if isinstance(document, dict) and isinstance(document.get(key), list):
        document[key] = ['workflow' if value == 'process' else value
                         for value in document[key]]


def _planning(contract):
    if not isinstance(contract, dict) or not isinstance(contract.get('planning', {}), dict):
        return
    policy = contract.get('planning', {}).get('restart_revision_policy', {})
    for role in ('user', 'reviewer'):
        _fields(policy, role)


def _task(document):
    result = deepcopy(document)
    _rename(result, 'process', 'workflow')
    _rename(result, 'registry_process', 'registry_workflow')
    _planning(result.get('contract', {}))
    _planning(result.get('newborn', {}).get('draft', {}))
    for entry in result.get('restart_history', []):
        revision = entry.get('planning_revision', {})
        _rename(revision, 'before_process', 'before_workflow')
        _planning(revision.get('before_contract', {}))
        _fields(revision, 'allowed_changes')
        _fields(revision.get('authorization', {}), 'revision_fields')
        _fields(entry.get('authorization', {}), 'revision_fields')
        _fields(entry.get('resolved_revision', {}), 'changed_fields')
    return result


def rename_current_workflow_rows(connection, expected):
    """Convert the complete observed current set in one scoped transaction."""
    connection.execute('SAVEPOINT workflow_format_conversion')
    try:
        tasks = connection.execute('SELECT id,version,metadata FROM tasks ORDER BY id').fetchall()
        sprints = connection.execute('SELECT id,revision,data FROM sprints ORDER BY id').fetchall()
        changes = {'tasks': [], 'sprints': []}
        prepared = []
        for table, rows, version_key, column in (
                ('tasks', tasks, 'version', 'metadata'),
                ('sprints', sprints, 'revision', 'data')):
            observed = {ident: {version_key: version,
                               'sha256': hashlib.sha256(raw.encode()).hexdigest()}
                        for ident, version, raw in rows}
            if observed != expected[table]:
                raise PoiseError(f'Current {table} changed since observation')
            for ident, _, raw in rows:
                before = json.loads(raw)
                if table == 'tasks':
                    after = _task(before)
                else:
                    after = deepcopy(before)
                    _rename(after, 'processes', 'workflows')
                if after != before:
                    changes[table].append(ident)
                    prepared.append((table, column, ident, json.dumps(after, ensure_ascii=False)))
        # Prepare every conversion and collision check before the first update.
        for table, column, ident, raw in prepared:
            connection.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (raw, ident))
        connection.execute('RELEASE SAVEPOINT workflow_format_conversion')
        return changes
    except BaseException:
        connection.execute('ROLLBACK TO SAVEPOINT workflow_format_conversion')
        connection.execute('RELEASE SAVEPOINT workflow_format_conversion')
        raise
