from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime, timezone

from ...common import PoiseError
from ...modules.ownership.domain import OwnershipPreflight, OwnershipSnapshot


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def legacy_conflicts(db):
    """Connected ownership components; Task and worktree claims stay independent."""
    if db.execute('PRAGMA user_version').fetchone()[0] != 12:
        return []
    tasks = [dict(row) for row in db.execute(
        'SELECT id,status,version,claimed_by FROM tasks ORDER BY id')]
    sessions = [dict(row) for row in db.execute('SELECT id,task_id FROM sessions ORDER BY id')]
    graph = {}
    def connect(left, right):
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
    task_owners, tree_owners = {}, {}
    for task in tasks:
        owner = task['claimed_by']
        if owner is not None:
            connect(('task', task['id']), ('actor', owner))
            task_owners.setdefault(owner, []).append(task['id'])
    for session in sessions:
        if session['task_id'] is not None:
            connect(('actor', session['id']), ('task', session['task_id']))
            tree_owners.setdefault(session['task_id'], []).append(session['id'])
    seeds = {('actor', a) for a, ids in task_owners.items() if len(ids) > 1}
    seeds.update(('task', t) for t, ids in tree_owners.items() if len(ids) > 1)
    visited, components = set(), []
    for seed in sorted(seeds):
        if seed in visited:
            continue
        nodes, pending = set(), [seed]
        while pending:
            node = pending.pop()
            if node not in nodes:
                nodes.add(node)
                pending.extend(graph.get(node, ()))
        visited.update(nodes)
        ids = sorted(n[1] for n in nodes if n[0] == 'task')
        actors = sorted(n[1] for n in nodes if n[0] == 'actor')
        component = {'task_ids': ids, 'actors': actors,
                     'tasks': [t for t in tasks if t['id'] in ids],
                     'sessions': [s for s in sessions if s['id'] in actors]}
        component['expected_snapshot'] = sha256(encode(component).encode()).hexdigest()
        components.append(component)
    return sorted(components, key=lambda item: item['task_ids'])


def require_unambiguous(db, task_id):
    if any(task_id in component['task_ids'] for component in legacy_conflicts(db)):
        raise PoiseError(f'Task {task_id} has ambiguous legacy ownership; use show '
                         'ownership_conflicts and recover_ownership with the exact snapshot')


def upgrade_legacy_ownership(db, *, clear_terminal=True):
    """Defer only uniqueness installation; never infer ownership from another resource."""
    if db.execute('PRAGMA user_version').fetchone()[0] != 12:
        return
    if clear_terminal:
        db.execute("UPDATE sessions SET task_id=NULL WHERE task_id IN "
                   "(SELECT id FROM tasks WHERE status IN ('completed','cancelled') "
                   "AND claimed_by IS NULL)")
    if legacy_conflicts(db):
        return
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS tasks_single_claimant '
               'ON tasks(claimed_by) WHERE claimed_by IS NOT NULL')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS sessions_single_worktree_owner '
               'ON sessions(task_id) WHERE task_id IS NOT NULL')
    db.execute('PRAGMA user_version=13')


class SqliteOwnershipRepository:
    def __init__(self, connection, processes):
        self.db = connection
        self.processes = processes

    def snapshot(self, actor):
        tasks = [row[0] for row in self.db.execute(
            "SELECT id FROM tasks WHERE claimed_by=? ORDER BY id", (actor,)
        )]
        if len(tasks) > 1:
            raise PoiseError(f"Session {actor} owns more than one Task; use show ownership_conflicts and recover_ownership")
        row = self.db.execute("SELECT task_id FROM sessions WHERE id=?", (actor,)).fetchone()
        return OwnershipSnapshot(actor, tasks[0] if tasks else None, None if row is None else row[0])

    def preflight(self, actor, task_id):
        require_unambiguous(self.db, task_id)
        row = self.db.execute("SELECT claimed_by FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f"Unknown ownership Task: {task_id}")
        owners = [item[0] for item in self.db.execute(
            "SELECT id FROM sessions WHERE task_id=? ORDER BY id", (task_id,)
        )]
        if len(owners) > 1:
            raise PoiseError(f"Worktree {task_id} has multiple owners")
        return OwnershipPreflight(actor, task_id, row[0], owners[0] if owners else None)

    def worktree_required(self, task_id):
        row = self.db.execute("SELECT status,metadata FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f"Unknown ownership Task: {task_id}")
        metadata = json.loads(row['metadata'])
        if row['status'] == 'newborn' and not metadata.get('restart_history'):
            return False
        process = metadata["process"]
        value = process.get("worktree_required")
        if type(value) is not bool:
            raise PoiseError("Stored process requires exact worktree_required bool")
        return value

    def bind_worktree(self, actor, task_id):
        self.db.execute(
            "INSERT INTO sessions VALUES(?,?) "
            "ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id",
            (actor, task_id),
        )

    def conflicts(self):
        pending = legacy_conflicts(self.db)
        return {'status': 'ownership_migration_pending' if pending else 'ownership_consistent',
                'schema_version': self.db.execute('PRAGMA user_version').fetchone()[0],
                'conflicts': pending}

    def legacy_receipt(self, request_id):
        row = self.db.execute(
            "SELECT data FROM journal WHERE event='ownership.legacy_reconciled' "
            "AND json_extract(data,'$.request.request_id')=? ORDER BY seq DESC LIMIT 1",
            (request_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def reconcile_legacy(self, intent, actor, before):
        # Caller validated scope/snapshot/liveness in this same UnitOfWork.
        for row in before['sessions']:
            desired = intent['worktree_bindings'][row['id']]
            if desired != row['task_id']:
                changed = self.db.execute(
                    'UPDATE sessions SET task_id=NULL WHERE id=? AND task_id IS ?',
                    (row['id'], row['task_id']))
                if changed.rowcount != 1:
                    raise PoiseError('Ownership snapshot changed')
        upgrade_legacy_ownership(self.db, clear_terminal=False)
        remaining = self.conflicts()
        result = {'status': 'ownership_reconciled', 'request_id': intent['request_id'],
                  'task_ids': before['task_ids'], 'schema_version': remaining['schema_version'],
                  'remaining_conflicts': len(remaining['conflicts']), 'replayed': False}
        self.db.execute(
            'INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(), actor, None, 'ownership.legacy_reconciled',
             encode({'request': intent, 'before': before, 'result': result})))
        return result
