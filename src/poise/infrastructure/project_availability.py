"""Read-only adapter for the configured-project registry; no runtime bootstrap."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from ..modules.tasks.start_admission import require_start_execution
from ..artifacts import inspect_paths
from ..common import configured_root, descendant, load_config
from ..modules.content_requirements.domain import ArtifactFact
from ..modules.foundation.errors import DomainError, PoiseError
from ..modules.sprints.domain import Sprint
from .sqlite.artifacts import SqliteArtifactRepository
from .sqlite.database import SCHEMA_VERSION
from .sqlite.readonly import readonly_snapshot
from .sqlite.sprints import SqliteSprintRepository
from .sqlite.tasks import SqliteTaskRepository
from .sqlite.start_admission import capture_start_migrations
from .task_paths import sprint_root, task_root


class ReadOnlyProjectAvailability:
    def startable(self, project: dict) -> list[dict]:
        try:
            root, cfg, _ = load_config(Path(project['config_path']))
            if cfg['project'] != project['project']:
                raise PoiseError('Configured project identity changed during discovery')
            capture, state = self._with_store(root, cfg, self._capture)
            return self._project(capture, project, cfg, state)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, RecursionError) as exc:
            raise PoiseError(f'Cannot read configured project Tasks: {exc}') from exc

    def require_task(self, config_path, cfg, task_id):
        def require(db, state):
            if not SqliteTaskRepository(db).exists(task_id):
                raise PoiseError(f'Configured Task DB has no Task: {task_id}')
        self._with_store(Path(config_path).resolve().parent, cfg, require)

    @staticmethod
    def _with_store(root, cfg, consumer):
        try:
            state = configured_root(root, cfg['paths']['state'])
            path = descendant(state, cfg['paths']['database'])
            if not path.is_file():
                raise PoiseError(f'Configured Task DB does not exist: {path}')
            # Never construct Database/Runtime: both perform initialization writes.
            with readonly_snapshot(path, lock_seconds=cfg['limits']['lock_seconds']) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
                    raise PoiseError('Task DB schema is not current; next never migrates a store')
                return consumer(db, state)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, RecursionError) as exc:
            raise PoiseError(f'Cannot read configured project Tasks: {exc}') from exc

    @staticmethod
    def _capture(db, state):
        from .sqlite.task_snapshot import capture_task
        tasks = SqliteTaskRepository(db)
        artifacts = SqliteArtifactRepository(db)
        candidates = []
        sprints = {}
        for row in tasks.start_candidates():
            tid = row['id']
            sid = db.execute("SELECT json_extract(metadata,'$.sprint_id') FROM tasks WHERE id=?", (tid,)).fetchone()[0]
            if sid is not None and sid not in sprints:
                record = db.execute('SELECT data FROM sprints WHERE id=?', (sid,)).fetchone()
                sprints[sid] = (None if record is None else record[0], SqliteSprintRepository.capture_facts(db, sid))
            execution = db.execute('SELECT data FROM task_execution WHERE task_id=?', (tid,)).fetchone()
            candidates.append((tid, row['metadata'], capture_task(db, tid),
                None if execution is None else execution[0], artifacts.records(tid),
                capture_start_migrations(db, json.loads(row['metadata']))))
        return (tuple(candidates), sprints), state

    def _project(self, capture, project, cfg, state):
        candidates, sprint_rows = capture
        eligibility, result = {}, []
        for tid, raw_metadata, raw_task, raw_execution, records, migrations in candidates:
            metadata = json.loads(raw_metadata)
            sid = metadata['sprint_id']
            if sid is not None:
                if sid not in eligibility:
                    raw, facts = sprint_rows[sid]
                    record = None if raw is None else json.loads(raw)
                    if record is None or record['project'] != project['project']:
                        raise PoiseError(f'Task {tid} has no Sprint in this project: {sid}')
                    eligibility[sid] = Sprint.restore(record['aggregate']).work_overview(
                        SqliteSprintRepository.restore_facts(record, facts))['eligible']
                if tid not in eligibility[sid]:
                    continue
            try:
                require_start_execution(tid, metadata,
                    None if raw_execution is None else json.loads(raw_execution),
                    tuple(json.loads(raw) for raw in migrations))
            except DomainError:
                continue
            task = raw_task.restore()
            facts = self._artifact_facts(records, tid, sid, cfg, state)
            if task.can_start(facts):
                result.append({**project, 'task':tid, 'sprint':sid, 'goal':metadata['contract']['goal']})
        return result

    @staticmethod
    def _artifact_facts(records, tid, sid, cfg, state):
        roots = {'task': task_root(state, cfg['paths'], tid, sid)}
        owners = {'task': tid}
        if sid is not None:
            roots['sprint'] = sprint_root(state, cfg['paths'], sid)
            owners['sprint'] = sid
        facts = []
        for old in records:
            if old['scope'] not in roots or old['owner'] != owners[old['scope']]:
                continue  # Taskless discovery cannot assume a runtime identity.
            try:
                current = inspect_paths([old['path']], roots, owners)[0]
            except (PoiseError, OSError):
                continue  # Missing/changed files cannot satisfy an entry requirement.
            if any(current[k] != old[k] for k in ('id', 'owner', 'scope', 'digest')):
                continue
            relative = current['relative_path']
            prefix = cfg['batch']['artifact_directories'][current['scope']].rstrip('/') + '/'
            if relative.startswith(prefix):
                relative = relative[len(prefix):]
            facts.append(ArtifactFact(current['id'], current['scope'], relative))
        return tuple(facts)
