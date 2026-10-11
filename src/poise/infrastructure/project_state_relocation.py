"""Replayable file effects composed with artifact and hook publication owners."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

from ..common import PoiseError, configured_root, descendant, digest, file_digest
from ..modules.artifact_factory.domain import ArtifactRecoveryIntent
from .diagnostic_io import readonly_database
from .goal_config import read_document
from .locking import exclusive_lock
from .project_resources import require_project_association, observe_hook_association
from .projects import ProjectSettings
from .saved_materials import SavedMaterialReader, bind_receipt_materials
from .sqlite.artifacts import SqliteArtifactRepository
from .sqlite.transaction import write_transaction
from .task_paths import task_root, sprint_root


class FileProjectStateRelocation:
    def __init__(self, owner, config_path, live, candidate, move, locks, pending_path, request):
        self.owner, self.config_path = owner, config_path
        self.live, self.candidate, self.move = live, candidate, move
        self.locks, self.pending_path, self.request = locks, pending_path, request
        self.source = Path(move['expected_source']).resolve()
        self.destination = Path(move['destination']).resolve()
        self.settings = owner.settings
        self.hooks = []

    def _pending(self):
        return read_document(self.pending_path)

    def _save(self, phase, **fields):
        from . import project_config as effects
        pending = self._pending()
        relocation = pending.get('relocation', {})
        relocation.update(fields, phase=phase)
        pending['relocation'] = relocation
        effects.atomic_write(self.pending_path, effects.encoded(pending, self.settings.raw['json_indent']),
                             self.settings.raw['file_mode'])

    def _database(self, root):
        return descendant(root, self.live['paths']['database'])

    def _admit_database(self, root):
        database = self._database(root)
        if not database.exists():
            raise PoiseError('State relocation requires an existing Task registry')
        with readonly_database(database) as db:
            writer = db.execute('SELECT id,claimed_by FROM tasks WHERE claimed_by IS NOT NULL LIMIT 1').fetchone()
            if writer is not None:
                raise PoiseError(f"State relocation requires released Task writer: {writer['id']} / {writer['claimed_by']}")
            return [dict(row) for row in db.execute('SELECT id,version,metadata FROM tasks ORDER BY id')]

    def admit(self):
        resources = self.live.get('runtime_resources')
        if resources is None:
            raise PoiseError('State relocation requires explicit runtime resources association')
        require_project_association(self.settings, self.config_path, self.live, resources)
        if self.settings.lock.is_relative_to(self.source) or self.settings.lock.is_relative_to(self.destination):
            raise PoiseError('Project writer fence must be outside relocated state roots')
        pending = self._pending().get('relocation')
        if pending is None:
            self.tasks = self._admit_database(self.source)
        else:
            self.tasks = pending['tasks']
            if self.source.exists():
                self._admit_database(self.source)
            if self.destination.exists():
                self._admit_database(self.destination)
        proposals = self.move['hook_destinations']
        if {item['installation_id'] for item in proposals} != {
                item['installation_id'] for item in resources['hooks']}:
            # After config publication the selected settings may already be the
            # target, but the exact installation set remains unchanged.
            raise PoiseError('State relocation hook destination associations must be complete')
        if len(proposals) != len(resources['hooks']):
            raise PoiseError('Duplicate hook destination installation identity')
        if pending is None:
            from .hook_transport import HookSettings
            for item in resources['hooks']:
                settings, record = observe_hook_association(item, self.config_path)
                target = next(p for p in proposals if p['installation_id'] == item['installation_id'])
                if set(target) != {'installation_id', 'settings_path', 'settings'}:
                    raise PoiseError('Hook destination requires exact settings proposal')
                proposed = HookSettings.proposed(target['settings_path'], target['settings'])
                if proposed.project_config != self.config_path:
                    raise PoiseError('Hook destination project association differs')
                self.locks.enter_context(exclusive_lock(settings.lock, settings.raw['lock_seconds'],
                                                        settings.raw['lock_poll_seconds']))
                if proposed.raw != settings.raw or proposed.path != settings.path:
                    with readonly_database(settings.database) as db:
                        for row in db.execute('SELECT id FROM bindings'):
                            last = db.execute('SELECT event FROM hook_events WHERE binding=? ORDER BY id DESC LIMIT 1',
                                              (row['id'],)).fetchone()
                            if last is None or last['event'] != 'SessionEnd':
                                raise PoiseError('Moved hook runtime requires DEAD native session; LIVE/UNCERTAIN refused')
                self.hooks.append({'source_path': str(settings.path), 'source_settings': settings.raw,
                                   'record': record, 'target': target})
            self._save('admitted', tasks=self.tasks, hooks=self.hooks)
        else:
            self.hooks = pending['hooks']
            from .hook_transport import HookSettings
            for hook in self.hooks:
                settings = HookSettings.proposed(hook['source_path'], hook['source_settings'])
                self.locks.enter_context(exclusive_lock(settings.lock, settings.raw['lock_seconds'],
                                                        settings.raw['lock_poll_seconds']))

    def copy(self):
        from . import project_config as effects
        staging = self.owner._staging_path(self.destination)
        if not self.destination.exists():
            if staging.exists():
                shutil.rmtree(staging)
            effects.copy_state_tree(self.source, staging)
            effects.publish_state_tree(staging, self.destination)
        phase = self._pending()['relocation']['phase']
        if phase == 'admitted':
            if not effects.same_tree(self.source, self.destination):
                raise PoiseError('Copied state differs from admitted source')
            self._save('copied')

    def _transaction(self):
        limits = self.live['limits']
        return write_transaction(self._database(self.destination),
                                 descendant(self.destination, self.live['paths']['lock']),
                                 limits['lock_seconds'], limits['lock_poll_seconds'])

    def publish_materials(self):
        from .sqlite.task_delivery import SqliteTaskDeliveryRepository
        # Existing explicit binding events are carried with the store. Historical
        # receipts are hydrated only here, under the admitted relocation intent.
        with self._transaction() as db:
            registry = SqliteArtifactRepository(db)
            for task in self.tasks:
                task_id = task['id']
                metadata = json.loads(task['metadata'])
                delivery = SqliteTaskDeliveryRepository(db).read(task_id)
                if delivery is not None and delivery.data['retirement_started']:
                    continue
                roots = {'task': task_root(self.source, self.live['paths'], task_id, metadata['sprint_id'])}
                owners = {'task': task_id}
                if metadata['sprint_id'] is not None:
                    roots['sprint'] = sprint_root(self.source, self.live['paths'], metadata['sprint_id'])
                    owners['sprint'] = metadata['sprint_id']
                for row in db.execute('SELECT data FROM evidence WHERE task_id=?', (task_id,)).fetchall():
                    receipt = json.loads(row['data'])
                    bound = db.execute("SELECT 1 FROM journal WHERE task_id=? AND event='artifact.materials_bound' "
                                       "AND json_extract(data,'$.receipt_id')=? LIMIT 1", (task_id, receipt['id'])).fetchone()
                    if bound is None:
                        bind_receipt_materials(db, task_id, receipt, roots, owners, self.request['request_id'])
                records = registry.records(task_id)
                intent = ArtifactRecoveryIntent(
                    self.request['request_id'] + '/' + task_id, task_id, task['version'],
                    tuple(r['id'] for r in records), {key: str(path) for key, path in roots.items()},
                    'Publish admitted project state relocation', self.request['request_id'])
                publications = []
                for record in records:
                    path = Path(record['path'])
                    if path.is_relative_to(self.source):
                        path = self.destination / path.relative_to(self.source)
                    if not path.is_file() or file_digest(path) != record['digest']:
                        raise PoiseError('Destination material missing or digest changed')
                    publications.append({**record, 'path': str(path)})
                request_digest = digest([self.request, task_id])
                saved = registry.recovery_snapshot(intent, request_digest)
                if 'replay' not in saved:
                    registry.rebind(intent, request_digest, records, publications, self.request['request_id'])
        self._save('materials_published')

    def publish_hooks(self):
        from ..application.hook_transport import HookCommands
        from .hook_transport import FileHookRepository, HookSettings
        from .sqlite.hook_transport import HookRegistry
        for hook in self.hooks:
            settings = HookSettings.proposed(hook['source_path'], hook['source_settings'])
            commands = HookCommands(FileHookRepository(settings, HookRegistry(settings)))
            commands.relocate(hook['target'], hook['record'], self.request['request_id'])
        self._save('hooks_published')

    def publish_configuration(self):
        from . import project_config as effects
        self.candidate['paths']['state'] = str(self.destination)
        self.candidate['runtime_resources']['hooks'] = [
            {'installation_id': item['installation_id'], 'settings_path': item['settings_path']}
            for item in self.move['hook_destinations']]
        current = read_document(self.config_path)
        if current != self.candidate:
            effects.switch_project_config(self.config_path,
                effects.encoded(self.candidate, self.settings.raw['json_indent']), self.settings.raw['file_mode'])
        self._save('configuration_published')

    def confirm_consumers(self):
        from .sqlite.task_delivery import SqliteTaskDeliveryRepository
        self._admit_database(self.destination)
        reader = SavedMaterialReader(self._database(self.destination))
        with readonly_database(self._database(self.destination)) as db:
            for task in self.tasks:
                delivery = SqliteTaskDeliveryRepository(db).read(task['id'])
                if delivery is not None and delivery.data['retirement_started']:
                    continue
                for row in db.execute('SELECT data FROM evidence WHERE task_id=?', (task['id'],)):
                    receipt = json.loads(row['data'])
                    reader.physical_receipt(receipt)
                    if receipt.get('acceptance_manifest'):
                        manifest = json.loads(reader.resolve(receipt, 'acceptance_manifest',
                                               receipt['acceptance_manifest']['digest']).read_bytes())
                        for item in manifest['inputs']:
                            reader.resolve(receipt, 'input/' + item['id'], item['digest'])
            for row in db.execute('SELECT path,digest FROM artifacts'):
                path = Path(row['path'])
                if path.is_relative_to(self.source):
                    # Retired materials deliberately remain historical.
                    continue
                if path.is_file() and file_digest(path) != row['digest']:
                    raise PoiseError('Destination registered material digest changed')
        for item in self.candidate['runtime_resources']['hooks']:
            observe_hook_association(item, self.config_path)
        self._save('consumers_confirmed')

    def retire_source(self):
        from . import project_config as effects
        if self.move['source_disposition'] == 'delete_after_publish' and self.source.exists():
            effects.remove_source_state(self.source)
        self._save('source_retired')

    def result(self):
        return {'status': 'completed', 'source': str(self.source), 'destination': str(self.destination),
                'source_disposition': self.move['source_disposition']}
