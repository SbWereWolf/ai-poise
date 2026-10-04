"""Read-only observations, composed through existing configuration/Git/store owners."""
from pathlib import Path

from ..common import load_config_document, read_json
from ..modules.foundation.errors import PoiseError
from ..modules.projects.preflight import ConfigurationObservation


class FileProjectPreflight:
    def __init__(self, settings, setup, availability, context):
        self.settings_path = str(settings.path)
        self.setup = setup
        self.availability = availability
        self.context = context

    def request_context(self, raw):
        return self.context.request_context(raw)

    def observe_configuration(self, request):
        context = self.request_context({
            'config_path': request.config_path, 'project_id': request.project_id,
            'task_id': request.task_id})
        try:
            path = Path(request.config_path)
            manifest = read_json(path)
            context = self.context.manifest_context(path, manifest, context)
            _, configuration, _ = load_config_document(path, manifest)
            if configuration['project'] != request.project_id:
                raise PoiseError(f'Manifest project identity is {configuration["project"]!r}, '
                                 f'expected {request.project_id!r}')
            return ConfigurationObservation(context, configuration, 'passed', None)
        except PoiseError as exc:
            return ConfigurationObservation(context, None, 'rejected', str(exc))
        except Exception as exc:
            return ConfigurationObservation(context, None, 'unknown',
                                            f'{type(exc).__name__}: {exc}')

    def probe_git(self, configuration):
        return self.setup._probe(configuration, True)

    def require_task(self, config_path, configuration, task_id):
        self.availability.require_task(config_path, configuration, task_id)
