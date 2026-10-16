"""Explicit process cutover using the registered current project as its source.

The existing publisher owns locks, pending writes, receipts and completion.
An obsolete project-creation receipt is not required to identify this input.
"""
from pathlib import Path

from ..common import digest, load_workflow_migration_config
from ..modules.foundation.errors import PoiseError
from .goal_config import read_document
from .project_config import FileProjectConfigUpdate


class ProcessStagePublication(FileProjectConfigUpdate):
    def stage_criteria_binding(self, config_path):
        path = Path(config_path).resolve()
        _, config, _ = load_workflow_migration_config(path)
        registry = read_document(self.settings.registry)
        projects = registry.get('projects')
        entry = projects.get(config['project']) if isinstance(projects, dict) else None
        if (not isinstance(entry, dict) or not isinstance(entry.get('config_path'), str)
                or (self.settings.root / entry['config_path']).resolve() != path):
            raise PoiseError('Selected project does not identify this manifest in its registry; '
                             'correct its registration through the project owner')
        return {'kind': 'project', 'root': str(self.settings.root),
                'settings_path': str(self.settings.path), 'config_path': str(path),
                'project': config['project']}

    def stage_criteria_source(self, config_path, revision):
        path = Path(config_path).resolve()
        _, config, workflows = load_workflow_migration_config(path)
        if digest({'config': config, 'workflows': workflows}) != revision:
            raise PoiseError('Selected migration input changed; create a fresh backup and mapping')
        return {'kind': 'configured_input', 'config_path': str(path), 'revision': revision}

    def require_stage_criteria_source(self, publication):
        expected = {'kind': 'configured_input', 'config_path': publication['config_path'],
                    'revision': publication['revision']}
        if publication['source'] != expected:
            raise PoiseError('Migration source does not identify its selected project and revision')
