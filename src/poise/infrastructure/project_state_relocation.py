"""Replayable file effects composed with artifact and hook publication owners."""
from copy import deepcopy
from contextlib import ExitStack
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
from .saved_materials import SavedMaterialReader, bind_receipt_materials, saved_receipts
from .sqlite.artifacts import SqliteArtifactRepository
from .sqlite.transaction import write_transaction
from .task_paths import task_root, sprint_root
from .project_state_seal import file_snapshot, validate_source, validate_destination


PHASES = ('admitted', 'copied', 'materials_published', 'hooks_published',
          'configuration_published', 'consumers_confirmed', 'source_retired')


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
        if relocation and PHASES.index(phase) < PHASES.index(relocation['phase']):
            raise PoiseError('State relocation phase cannot regress')
        relocation.update(fields, phase=phase)
        pending['relocation'] = relocation
        effects.atomic_write(self.pending_path, effects.encoded(pending, self.settings.raw['json_indent']),
                             self.settings.raw['file_mode'])

    def _done(self, phase):
        current = self._pending()['relocation']['phase']
        return PHASES.index(current) >= PHASES.index(phase)

    def _admit_hook(self, hook):
        """One native admission rule for initial effects and every pending resume."""
        # The outer project fence remains held throughout relocation. Keep the
        # registry read leases local so its owning publication transaction can
        # acquire the same lock during an interrupted publication's exact retry.
        with ExitStack() as locks:
            self._check_hook(hook, locks)

    def _check_hook(self, hook, locks):
        from .hook_transport import HookSettings
        from .sqlite.hook_transport import HookRegistry
        proposed = HookSettings.proposed(hook['target']['settings_path'], hook['target']['settings'])
        moving = (hook['source_settings'] != proposed.raw
                  or Path(hook['source_path']).resolve() != proposed.path)
        candidates = [proposed]
        if self.source.exists():
            candidates.insert(0, HookSettings.proposed(hook['source_path'], hook['source_settings']))
        observed = False
        seen = set()
        locked = set()
        for settings in candidates:
            if settings.path in seen or not settings.database.is_file():
                continue
            seen.add(settings.path)
            published = settings.path.is_file()
            if published and read_document(settings.path) != settings.raw:
                raise PoiseError('Intent-owned hook settings changed during relocation')
            if settings.lock not in locked:
                locks.enter_context(exclusive_lock(settings.lock, settings.raw['lock_seconds'],
                                                   settings.raw['lock_poll_seconds']))
                locked.add(settings.lock)
            with readonly_database(settings.database) as db:
                if moving and any(HookRegistry.binding_liveness(db, row['id']).value != 'dead'
                                  for row in db.execute('SELECT id FROM bindings')):
                    raise PoiseError('Moved hook runtime requires DEAD native session; LIVE/UNCERTAIN refused')
            if not published:
                # A copied target registry precedes publication of its settings.
                # It still participates in native liveness admission above.
                continue
            # A pending hook publication has precisely two intent-owned observable
            # placements. Validate installations and native groups at either valid
            # placement; inspect liveness at both existing registries above.
            try:
                current, _ = observe_hook_association(
                    {'installation_id': hook['target']['installation_id'],
                     'settings_path': str(settings.path)}, self.config_path)
            except PoiseError:
                continue
            if current.raw == settings.raw:
                observed = True
        if not observed:
            raise PoiseError('No valid intent-owned hook installation remains during relocation')

    def _normalize_database(self, path, before, actual):
        if path == self._database(self.destination):
            sequence = max((row['seq'] for row in before['tables']['journal']), default=0)
            with readonly_database(path) as db:
                actual['tables']['artifacts'] = SqliteArtifactRepository(db).relocation_records(sequence)
        from .hook_transport import HookSettings, FileHookRepository, _read_definition
        for hook in self.hooks:
            target = HookSettings.proposed(hook['target']['settings_path'], hook['target']['settings'])
            if path != target.database:
                continue
            installation_id = hook['target']['installation_id']
            desired = FileHookRepository(target, None)._groups(
                _read_definition(target, hook['record']['receipt']['definition_path']),
                Path(hook['record']['receipt']['definition_path']))
            expected = deepcopy(hook['record'])
            expected['groups'] = desired
            expected['receipt']['revision'] = file_digest(target.hooks_file)
            original = next(row for row in before['tables']['installations'] if row['id'] == installation_id)
            for row in actual['tables']['installations']:
                if row['id'] == installation_id and digest(json.loads(row['data'])) == digest(expected):
                    row['data'] = original['data']

    def _validate_seal(self):
        if self.source.exists():
            validate_source(self.source, self.seal)
        if self.destination.exists():
            validate_destination(self.destination, self.seal, self._normalize_database)

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
            self.seal = pending['seal']
            if self.source.exists():
                self._admit_database(self.source)
            elif (self.move['source_disposition'] != 'delete_after_publish'
                  or not self._done('consumers_confirmed')
                  or configured_root(self.config_path.parent, self.live['paths']['state']) != self.destination):
                raise PoiseError('Admitted source missing before confirmed retirement')
            if self.destination.exists():
                self._admit_database(self.destination)
            elif self._done('copied'):
                raise PoiseError('Published relocation destination is missing')
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
                self.hooks.append({'source_path': str(settings.path), 'source_settings': settings.raw,
                                   'record': record, 'target': target})
            for hook in self.hooks:
                self._admit_hook(hook)
            self.seal = file_snapshot(self.source)
            self._save('admitted', tasks=self.tasks, hooks=self.hooks, seal=self.seal)
        else:
            self.hooks = pending['hooks']
            for hook in self.hooks:
                self._admit_hook(hook)
            self._validate_seal()

    def copy(self):
        if self._done('copied'):
            return
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
        if self._done('materials_published'):
            return
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
                for receipt in saved_receipts(db, task_id):
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
        if self._done('hooks_published'):
            return
        from ..application.hook_transport import HookCommands
        from .hook_transport import FileHookRepository, HookSettings
        from .sqlite.hook_transport import HookRegistry
        for hook in self.hooks:
            settings = HookSettings.proposed(hook['source_path'], hook['source_settings'])
            commands = HookCommands(FileHookRepository(settings, HookRegistry(settings)))
            commands.relocate(hook['target'], hook['record'], self.request['request_id'])
        self._save('hooks_published')

    def publish_configuration(self):
        if self._done('configuration_published'):
            return
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
                for receipt in saved_receipts(db, task['id']):
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
        if not self._done('consumers_confirmed'):
            self._save('consumers_confirmed')

    def retire_source(self):
        from . import project_config as effects
        for hook in self.hooks:
            self._admit_hook(hook)
        self._validate_seal()
        if self._done('source_retired'):
            return
        if self.move['source_disposition'] == 'delete_after_publish' and self.source.exists():
            effects.remove_source_state(self.source)
        self._save('source_retired')

    def result(self):
        return {'status': 'completed', 'source': str(self.source), 'destination': str(self.destination),
                'source_disposition': self.move['source_disposition']}
