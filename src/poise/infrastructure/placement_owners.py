"""Optional current-owner observation; never bootstrap or mutate Task stores."""
import sqlite3

from ..common import configured_root, descendant, PoiseError


class PoiseCurrentPlacementOwners:
    def __init__(self, bound, caller_key):
        self.bound, self.caller_key = bound, caller_key

    def current(self, kinds):
        if not kinds:
            return {}
        # No Task slice is imported or opened until an enabled policy needs it.
        from .sqlite.readonly import readonly_snapshot, SnapshotWorkerError
        from .sqlite.database import SCHEMA_VERSION
        from .sqlite.runtime_adapter import RuntimeRegistry
        from .sqlite.ownership import SqliteOwnershipRepository
        from .sqlite.tasks import SqliteTaskRepository
        from .sqlite.sprints import SqliteSprintRepository
        from .task_paths import task_root, sprint_root
        from ..modules.tasks.domain import is_terminal_task_status
        cfg = self.bound.config
        try:
            state = configured_root(self.bound.root, cfg['paths']['state'])
            database = descendant(state, cfg['paths']['database'])
            if not database.is_file():
                return {}
            result = {}
            with readonly_snapshot(database, lock_seconds=cfg['limits']['lock_seconds']) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
                    return {}
                actor = RuntimeRegistry.session_for_identity(db, self.caller_key)
                if actor is None:
                    return {}
                ownership = SqliteOwnershipRepository(db, self.bound.workflows)
                snapshot = ownership.snapshot(actor)
                if snapshot.task_id is None:
                    return {}
                ownership.preflight(actor, snapshot.task_id)
                current = SqliteTaskRepository(db).membership_snapshot(snapshot.task_id)
                if current['claimed_by'] != actor or is_terminal_task_status(current['status']):
                    return {}
                sid = current['sprint_id']
                if sid is not None:
                    sprint = SqliteSprintRepository(db).get(sid)
                    if sprint is None or sprint['project'] != cfg['project']:
                        return {}
                if 'task' in kinds:
                    result['task'] = {'kind': 'task', 'id': current['task_id'],
                                      'root': str(task_root(state, cfg['paths'], current['task_id'], sid))}
                if 'sprint' in kinds and sid is not None:
                    result['sprint'] = {'kind': 'sprint', 'id': sid,
                                        'root': str(sprint_root(state, cfg['paths'], sid))}
            # Exiting the protected reader is part of proving this observation.
            return result
        except (PoiseError, SnapshotWorkerError, OSError, sqlite3.Error,
                ValueError, KeyError, TypeError, RecursionError):
            return {}
