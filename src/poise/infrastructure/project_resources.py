"""Read existing runtime authority before allocating mutable project stores."""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from ..common import PoiseError, digest, load_config, configured_root
from ..modules.projects.resources import validate_resources
from .diagnostic_io import readonly_database
from .goal_config import read_document
from .projects import FileProjectSetup


_held_project_leases = ContextVar('held_project_leases', default=frozenset())


class ProjectRuntimeFence:
    """One external setup fence, rereading authority before every owner effect."""
    def __init__(self, config_path, expected_state=None, hook_settings=None):
        from .projects import ProjectSettings
        self.config_path = Path(config_path).resolve()
        self.hook_settings = hook_settings
        root, config, _ = self._configuration()
        if 'runtime_resources' not in config:
            raise PoiseError('Mutable runtime requires explicit runtime_resources association')
        resources = validate_resources(config['runtime_resources'])
        self.settings = ProjectSettings(resources['project_setup'])
        self.state = configured_root(root, config['paths']['state']) if expected_state is None else expected_state
        self._validate()

    def _configuration(self):
        if self.hook_settings is None:
            return load_config(self.config_path)
        # Binding a native entry uses transport authority, not Task workflows.
        from ..modules.foundation.validation import configuration_fields, nonempty
        config = read_document(self.config_path)
        nonempty(config.get('project'), 'hook project')
        paths = config.get('paths')
        if not isinstance(paths, dict) or 'state' not in paths:
            raise PoiseError('Hook project requires explicit paths.state')
        batch = configuration_fields(config.get('batch'), {'message_source'}, 'hook project.batch')
        source = configuration_fields(batch['message_source'], {'id', 'mode'},
                                      'hook project.batch.message_source')
        for field in ('id', 'mode'):
            nonempty(source[field], 'hook project.batch.message_source.' + field)
        return self.config_path.parent, config, None

    def hook_manifest(self):
        if self.hook_settings is None:
            raise PoiseError('Hook manifest requires a selected hook association')
        return self._configuration()[1]

    def _validate(self):
        root, config, _ = self._configuration()
        require_project_association(self.settings, self.config_path, config, config['runtime_resources'])
        if self.hook_settings is None and configured_root(root, config['paths']['state']) != self.state:
            raise PoiseError('Cached runtime configuration state placement changed')
        if self.settings.lock.is_relative_to(self.state):
            raise PoiseError('Project writer fence must remain outside mutable state')
        if self.hook_settings is not None:
            associations = config['runtime_resources']['hooks']
            selected = [item for item in associations
                        if Path(item['settings_path']).resolve() == self.hook_settings.path]
            if len(selected) != 1 or read_document(self.hook_settings.path) != self.hook_settings.raw:
                raise PoiseError('Cached hook runtime association/settings changed')

    @contextmanager
    def locked(self):
        from .locking import exclusive_lock
        key = str(self.settings.lock)
        held = _held_project_leases.get()
        if key in held:
            self._validate()
            yield
            return
        with exclusive_lock(self.settings.lock, self.settings.raw['lock_seconds'],
                            self.settings.raw['lock_poll_seconds']):
            self._validate()
            token = _held_project_leases.set(held | {key})
            try:
                yield
            finally:
                _held_project_leases.reset(token)


def require_project_association(settings, config_path, config, resources):
    validate_resources(resources)
    if Path(resources['project_setup']).resolve() != settings.path:
        raise PoiseError('Project setup association does not match selected settings')
    registry = FileProjectSetup(settings)._registry()['projects']
    entry = registry.get(config['project'])
    if entry is None or (settings.root / entry['config_path']).resolve() != config_path:
        raise PoiseError('Project registry authority does not bind this manifest')


def observe_hook_association(item, config_path):
    """Observe a historical installation without initializing its registry."""
    from .hook_transport import HookSettings, FileHookRepository, _read_definition
    settings = HookSettings(item['settings_path'])
    if settings.project_config != config_path:
        raise PoiseError('Hook installation project binding differs from selected project')
    with readonly_database(settings.database) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise PoiseError('Unsupported hook installation registry version')
        row = db.execute('SELECT data FROM installations WHERE id=?',
                         (item['installation_id'],)).fetchone()
        if row is None:
            raise PoiseError('Hook installation identity is not registered')
        record = json.loads(row['data'])
    receipt = record['receipt']
    definition = _read_definition(settings, receipt['definition_path'])
    if definition.data['id'] != item['installation_id'] or record.get('migrated_to') is not None:
        raise PoiseError('Hook definition installation identity differs from association')
    if receipt['hooks_path'] != str(settings.hooks_file):
        raise PoiseError('Hook installation native file differs from settings')
    groups = FileHookRepository(settings, None)._groups(definition, Path(receipt['definition_path']))
    if groups != record['groups']:
        raise PoiseError('Hook installation definition or settings groups changed')
    native = read_document(settings.hooks_file)
    if any(native.get('hooks', {}).get(event, []).count(group) != 1 for event, group in groups):
        raise PoiseError('Hook installation native group changed')
    return settings, record
