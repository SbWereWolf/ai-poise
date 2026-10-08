"""Facts from one selected manifest and the actual executable source location."""
from pathlib import Path

from ..common import configured_root, configured_storage_path, descendant
from ..modules.foundation.errors import PoiseError
from ..modules.projects.preflight import explicit_config_path, explicit_text


class FileProjectContext:
    def __init__(self, installation_source):
        self.installation_source = str(Path(installation_source).resolve())

    def request_context(self, raw):
        request = raw if isinstance(raw, dict) else {}
        config = request.get('config_path')
        project = request.get('project_id')
        task = request.get('task_id')
        return {
            'installation_source': self.installation_source,
            'config_path': config if explicit_config_path(config) else None,
            'requested_project_id': project if explicit_text(project) else None,
            'project_id': None,
            'requested_task_id': task if explicit_text(task) else None,
            'state': None,
            'task_database': None,
            'task_lock': None,
            'requirements_database': None,
            'requirements_lock': None,
            'repository': None,
            'base_ref': None,
        }

    def manifest_context(self, config_path, manifest, context):
        observed = dict(context)
        project = manifest.get('project')
        observed['project_id'] = project if explicit_text(project) else None
        git = manifest.get('git')
        if isinstance(git, dict):
            repository, base_ref = git.get('repository'), git.get('base_ref')
            observed['repository'] = repository if explicit_config_path(repository) else None
            observed['base_ref'] = base_ref if explicit_text(base_ref) else None
        paths = manifest.get('paths')
        if not isinstance(paths, dict):
            return observed
        try:
            state = configured_root(Path(config_path).resolve().parent, paths.get('state'))
        except (PoiseError, OSError, ValueError):
            return observed
        observed['state'] = str(state)
        for field, key, resolver in (
            ('task_database', 'database', descendant),
            ('task_lock', 'lock', descendant),
            ('requirements_database', 'requirements_database', configured_storage_path),
            ('requirements_lock', 'requirements_lock', configured_storage_path),
        ):
            value = paths.get(key)
            if not explicit_text(value):
                continue
            try:
                observed[field] = str(resolver(state, value))
            except (PoiseError, OSError, ValueError):
                continue
        return observed

    def lookup_failure(self, config_path, manifest, task_id):
        request = {'config_path': str(config_path), 'project_id': manifest['project'],
                   'task_id': task_id}
        context = self.manifest_context(config_path, manifest, self.request_context(request))
        facts = '; '.join(f'{name}={value}' for name, value in context.items())
        return (f'Неизвестный task/sprint ID: {task_id}. Выбранное окружение: {facts}. '
                'Проверьте выбранный проект через poise project list и poise project check; '
                'для poise work явно задайте POISE_CONFIG. Выбор не переносит данные и не меняет владение.')
