from __future__ import annotations

import json

from ...common import PoiseError
from ...modules.ownership.domain import OwnershipPreflight, OwnershipSnapshot


class SqliteOwnershipRepository:
    def __init__(self, connection):
        self.db = connection

    def snapshot(self, actor):
        tasks = [row[0] for row in self.db.execute(
            "SELECT id FROM tasks WHERE claimed_by=? ORDER BY id", (actor,)
        )]
        if len(tasks) > 1:
            raise PoiseError(f"Session {actor} owns more than one Task")
        row = self.db.execute("SELECT task_id FROM sessions WHERE id=?", (actor,)).fetchone()
        return OwnershipSnapshot(actor, tasks[0] if tasks else None, None if row is None else row[0])

    def preflight(self, actor, task_id):
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
        row = self.db.execute("SELECT metadata FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise PoiseError(f"Unknown ownership Task: {task_id}")
        process = json.loads(row[0])["process"]
        value = process["worktree_required"]
        if type(value) is not bool:
            raise PoiseError("Stored process requires exact worktree_required bool")
        return value

    def bind_worktree(self, actor, task_id):
        self.db.execute(
            "INSERT INTO sessions VALUES(?,?) "
            "ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id",
            (actor, task_id),
        )
