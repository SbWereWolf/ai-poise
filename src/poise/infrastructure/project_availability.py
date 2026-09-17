"""Read-only adapter for the configured-project registry; no runtime bootstrap."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from ..application.tasks import require_start_execution
from ..artifacts import inspect_paths
from ..common import configured_root, descendant, load_config
from ..modules.content_requirements.domain import ArtifactFact
from ..modules.foundation.errors import DomainError, PoiseError
from ..modules.sprints.domain import Sprint
from .sqlite.artifacts import SqliteArtifactRepository
from .sqlite.database import SCHEMA_VERSION
from .sqlite.sprints import SqliteSprintRepository
from .sqlite.tasks import SqliteTaskRepository, SqliteExecutionRepository
from .task_paths import sprint_root, task_root


class ReadOnlyProjectAvailability:
    def startable(self, project: dict) -> list[dict]:
        try:
            root, cfg, _ = load_config(Path(project['config_path']))
            if cfg['project'] != project['project']:
                raise PoiseError('Configured project identity changed during discovery')
            state = configured_root(root, cfg['paths']['state'])
            path = descendant(state, cfg['paths']['database'])
            if not path.is_file():
                raise PoiseError(f'Configured Task DB does not exist: {path}')
            # Never construct Database/Runtime: both perform initialization writes.
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True,
                                         timeout=cfg['limits']['lock_seconds'])) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
                    raise PoiseError('Task DB schema is not current; next never migrates a store')
                return self._read(db, project, cfg, state)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, RecursionError) as exc:
            raise PoiseError(f'Cannot read configured project Tasks: {exc}') from exc

    def _read(self, db, project, cfg, state):
        tasks = SqliteTaskRepository(db)
        sprints = SqliteSprintRepository(db)
        execution = SqliteExecutionRepository(db)
        artifacts = SqliteArtifactRepository(db)
        eligibility, result = {}, []
        for row in tasks.start_candidates():
            tid = row['id']
            metadata = json.loads(row['metadata'])
            sid = metadata['sprint_id']
            if sid is not None:
                if sid not in eligibility:
                    record = sprints.get(sid)
                    if record is None or record['project'] != project['project']:
                        raise PoiseError(f'Task {tid} has no Sprint in this project: {sid}')
                    eligibility[sid] = Sprint.restore(record['aggregate']).work_overview(
                        sprints.facts(sid))['eligible']
                if tid not in eligibility[sid]:
                    continue
            if execution.exists(tid):
                current, _ = execution.load(tid)
                try:
                    require_start_execution(current, metadata.get('restart_history', []))
                except DomainError:
                    continue
            task = tasks.load(tid)
            facts = self._artifact_facts(artifacts.records(tid), tid, sid, cfg, state)
            if task.can_start(facts):
                result.append({**project, 'task': tid, 'sprint': sid,
                               'goal': metadata['contract']['goal']})
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
