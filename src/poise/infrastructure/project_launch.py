"""File configuration and exec transport; no Task or database mutations."""
from __future__ import annotations

import os
from pathlib import Path

from ..common import configured_root, configured_storage_path, descendant, load_config
from ..modules.foundation.errors import PoiseError
from ..modules.projects.launch import ProjectLaunchSettings
from .goal_config import read_document
from .projects import FileProjectSetup, ProjectSettings


class FileProjectLaunch:
    def read_settings(self, path: str) -> ProjectLaunchSettings:
        try:
            return ProjectLaunchSettings.parse(read_document(Path(path)))
        except (PoiseError, OSError, UnicodeError, ValueError) as exc:
            raise PoiseError(f'launcher settings {path}: {exc}') from exc

    def _root(self, value: str, field: str) -> Path:
        root = Path(value)
        if not root.is_absolute() or not root.is_dir():
            raise PoiseError(f'{field}: an existing absolute directory is required: {value}')
        return root.resolve()

    def list_projects(self, settings: ProjectLaunchSettings) -> dict:
        tools = self._root(settings.poise_root, 'poise_root')
        self._root(settings.data_root, 'data_root')
        setup_path = tools / settings.project_setup
        try:
            setup = ProjectSettings(setup_path)
            if setup.root != tools:
                raise PoiseError('project_setup root does not match poise_root')
        except (PoiseError, OSError, UnicodeError, ValueError) as exc:
            raise PoiseError(f'project_setup {setup_path}: {exc}') from exc
        try:
            return FileProjectSetup(setup).list()
        except (PoiseError, OSError, UnicodeError, ValueError) as exc:
            raise PoiseError(f'project registry {setup.registry}: {exc}') from exc

    def require_data_placement(self, settings: ProjectLaunchSettings, config_path: str) -> None:
        path = Path(config_path)
        root, cfg, _ = load_config(path)
        data = self._root(settings.data_root, 'data_root')
        state = configured_root(root, cfg['paths']['state'])
        stores = {'paths.state': state}
        for name in ('database', 'lock'):
            stores[f'paths.{name}'] = descendant(state, cfg['paths'][name])
        for name in ('requirements_database', 'requirements_lock'):
            stores[f'paths.{name}'] = configured_storage_path(state, cfg['paths'][name])
        for name in ('database', 'lock'):
            stores[f'accounting.storage.{name}'] = descendant(state, cfg['accounting']['storage'][name])
        for field, store in stores.items():
            if not store.is_relative_to(data):
                raise PoiseError(f'{field}: {store} is outside data_root {data}')

    def execute(self, argv: tuple[str, ...], config_path: str) -> None:
        environment = dict(os.environ, POISE_CONFIG=config_path)
        try:
            os.execvpe(argv[0], list(argv), environment)
        except OSError as exc:
            raise PoiseError(f'command {argv[0]}: {exc}') from exc
