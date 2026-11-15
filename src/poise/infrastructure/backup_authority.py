"""Resolve a backup decision through the existing read-only Task material owner."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from ..common import configured_root, descendant, read_json
from ..modules.foundation.errors import PoiseError
from .project_resources import ProjectRuntimeFence
from .sqlite.database import Database
from .sqlite.queries import TaskQueries


class TaskMaterialBackupAuthority:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve()

    def __call__(self, reference):
        try:
            if not isinstance(reference, str) or not reference.startswith('task-feedback:'):
                raise ValueError('expected task-feedback:<task-id>:<comment-id>')
            task_id, comment_id = reference[len('task-feedback:'):].rsplit(':', 1)
            if not task_id or str(UUID(comment_id)) != comment_id:
                raise ValueError('canonical committed source identity required')
            config = read_json(self.config_path)
            paths, limits = config['paths'], config['limits']
            state = configured_root(self.config_path.parent, paths['state'])
            fence = ProjectRuntimeFence(self.config_path, state)
            database = Database(descendant(state, paths['database']), descendant(state, paths['lock']),
                                limits['lock_seconds'], limits['lock_poll_seconds'], fence,
                                read_only=True, read_wait=limits['sqlite_read_seconds'])
            page = TaskQueries(database).materials_view(task_id)
            comment = next((item for item in page['comments'] if item['id'] == comment_id), None)
            if comment is None:
                raise PoiseError(f'Authorization source {reference} is absent; read the selected '
                                 'Task materials and select the actual admitted user decision')
            decision = json.loads(comment['text'])
            if not isinstance(decision, dict):
                raise ValueError('recorded decision must describe its exact admitted scope')
            return {**decision, 'reference': reference, 'source_author': comment['author']}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise PoiseError(f'Cannot resolve recorded authorization source {reference}: {exc}; '
                             'select a committed exact Task user decision before retrying') from exc
