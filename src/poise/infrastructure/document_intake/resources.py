"""Episode-owned resources, short coordination gates and auditable recovery."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from ...modules.document_intake.contracts import ReadFailure
from ...modules.document_intake.domain import identity, ReadScope
from ..goal_config import atomic_write
from ..result_views import SavedResponse
from .registry import PreparedRegistry
from .template import validate_template, TemplateError


class CoordinationStore:
    def __init__(self, root, filename, policy, clock, file_mode):
        self.root, self.filename, self.policy, self.clock, self.file_mode = root, filename, policy, clock, file_mode

    def load(self):
        try:
            return json.loads((self.root / self.filename).read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError) as exc:
            raise ReadFailure('ownership_unknown') from exc

    def save(self, record):
        atomic_write(self.root / self.filename, (json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n').encode(), self.file_mode)

    def deadline(self, seconds):
        start = self.clock.observe()
        return start.comparison_domain, start.monotonic_ns + int(seconds * 1_000_000_000)

    def remaining(self, deadline):
        observed = self.clock.observe()
        if observed.comparison_domain != deadline[0]:
            raise ReadFailure('cleanup_incomplete', status='incomplete')
        return (deadline[1] - observed.monotonic_ns) / 1_000_000_000

    @contextmanager
    def locked(self, deadline=None):
        deadline = deadline or self.deadline(self.policy['wait_seconds'])
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'gate.lock').open('a+b') as stream:
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = self.remaining(deadline)
                    if remaining <= 0:
                        raise ReadFailure('cleanup_incomplete', status='incomplete')
                    time.sleep(min(self.policy['poll_seconds'], remaining))
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class DiagnosticLog:
    def __init__(self, path, clock):
        self.path, self.clock = path, clock

    def append(self, status, episode, reason):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = dict(status=status, episode=episode, reason=reason, at=self.clock.observe().audit_utc)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')


class EpisodeResources:
    def __init__(self, configuration, caller, authority_factory, clock):
        self.configuration, self.caller, self.clock = configuration, caller, clock
        self.authority_factory, self._authority = authority_factory, None
        self.bound = configuration.bound
        self.policy = configuration.policy
        self.session = self.policy['session']
        self.file_mode = self.bound.config['batch']['file_mode']
        self.store = CoordinationStore(Path(self.session['coordination_root']) / caller.key,
                                       self.session['ownership_filename'], self.policy['coordination'], clock, self.file_mode)
        self.log = DiagnosticLog(Path(self.session['diagnostic_file']), clock)
        self.record = None
        self.scope = None
        self.target = None
        self.registry = None
        self.validated_episode = None

    @property
    def authority(self):
        if self._authority is None:
            self._authority = self.authority_factory()
        return self._authority

    def _owner(self, episode, target):
        return dict(schema='poise-intake-owner-1', project=self.bound.config['project'], caller_key=self.caller.key,
                    episode=episode, path=str(target), registry_directory=self.session['registry_directory'],
                    registry_filename=self.policy['registry']['filename'], response_directory=self.session['response_directory'],
                    definition_path=str(self.authority.definition_path), hook_settings=str(self.authority.service.settings.path))

    def _validate_owner(self, record, *, require_tree=True):
        if not isinstance(record, dict) or not isinstance(record.get('episode'), dict):
            raise ReadFailure('ownership_unknown')
        target = Path(self.session['temporary_root']) / self.caller.key / identity(record['episode'])
        expected = self._owner(record['episode'], target)
        if any(record.get(k) != v for k, v in expected.items()):
            raise ReadFailure('ownership_unknown')
        if target.exists():
            try:
                local = json.loads((target / self.session['ownership_filename']).read_bytes())
            except (OSError, ValueError, UnicodeError) as exc:
                raise ReadFailure('ownership_unknown') from exc
            if not isinstance(local, dict) or any(local.get(k) != v for k, v in expected.items()):
                raise ReadFailure('ownership_unknown')
        elif require_tree:
            raise ReadFailure('ownership_unknown')
        return target

    def _activate(self, record, target):
        checkout_key = identity([self.bound.config['project'], str(self.bound.checkout)])
        self.record, self.target = record, target
        self.scope = ReadScope(self.caller.key, checkout_key, identity([self.caller.key, checkout_key]), record['episode'])
        self.registry = PreparedRegistry(target / self.session['registry_directory'] / self.policy['registry']['filename'],
                                         self.policy['registry']['busy_timeout_ms'], self.clock)

    def _ensure_registry(self, *, create=True):
        template = Path(self.session['registry_template'])
        if not template.is_file():
            raise ReadFailure('template_missing')
        try:
            validate_template(template)
        except TemplateError as exc:
            raise ReadFailure('template_invalid') from exc
        if not self.registry.path.exists():
            if not create:
                raise ReadFailure('registry_missing')
            directory = self.registry.path.parent
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=directory, suffix='.sqlite')
            os.close(descriptor)
            candidate = Path(name)
            try:
                shutil.copyfile(template, candidate)
                with candidate.open('rb') as stream:
                    os.fsync(stream.fileno())
                try:
                    os.link(candidate, self.registry.path)
                except FileExistsError:
                    pass
            finally:
                candidate.unlink(missing_ok=True)
        self.registry.validate()

    def observe_active(self):
        """Expensive public native/schema observation precedes the short gate."""
        if self.caller.native_session is None or self.authority is None:
            raise ReadFailure('admission_unavailable')
        observation = self.authority.resolve_episode(self.caller.native_session, None)
        if observation['state'] != 'active':
            code = 'admission_dead' if observation['state'] == 'ended' else 'admission_uncertain'
            cause = observation.get('cause') or ''
            if any(word in cause.lower() for word in ('binding', 'association', 'installation')):
                code = 'authority_mismatch'
            raise ReadFailure(code)
        record = self.store.load()
        if record is not None and record.get('episode') == observation['episode'] and record.get('phase') == 'active':
            target = self._validate_owner(record)
            self._activate(record, target)
            self._ensure_registry(create=False)
            self.validated_episode = observation['episode']
        return observation

    def admit(self, observation, *, adopt=False):
        episode = observation['episode']
        record = self.store.load()
        if record is not None and record.get('episode') != episode and record.get('phase') != 'ended':
            raise ReadFailure('episode_mismatch')
        fresh = record is None or record.get('episode') != episode
        if fresh:
            if adopt:
                raise ReadFailure('adoption_unconfirmed')
            target = Path(self.session['temporary_root']) / self.caller.key / identity(episode)
            if target.exists():
                raise ReadFailure('ownership_unknown')
            # Validate the prepared template before allocating a new episode root.
            template = Path(self.session['registry_template'])
            if not template.is_file():
                raise ReadFailure('template_missing')
            try:
                validate_template(template)
            except TemplateError as exc:
                raise ReadFailure('template_invalid') from exc
            record = self._owner(episode, target) | {'phase': 'active'}
            target.mkdir(parents=True)
            atomic_write(target / self.session['ownership_filename'],
                         (json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n').encode(), self.file_mode)
            self.store.save(record)
        else:
            target = self._validate_owner(record)
            if record.get('phase') != 'active':
                raise ReadFailure('admission_dead')
        self._activate(record, target)
        if self.validated_episode != episode:
            self._ensure_registry()
        return self.scope

    def reserve_response(self):
        return self.target / self.session['response_directory'] / self.scope.checkout_key / uuid.uuid4().hex / self.bound.config['paths']['response']

    def save_response(self, path, result):
        record = self.store.load()
        if (not record or record.get('episode') != self.record['episode'] or record.get('phase') != 'active'):
            raise ReadFailure('admission_dead')
        self._validate_owner(record)
        return SavedResponse(path, self.file_mode).save(result, canonical=True)

    def recover(self):
        record = self.store.load()
        if record is None:
            raise ReadFailure('ownership_unknown')
        target = self._validate_owner(record, require_tree=False)
        episode = record['episode']
        details = dict(path=str(target), episode=episode)
        self.record, self.target = record, target
        deadline = self.store.deadline(self.policy['coordination']['recovery_budget_seconds'])
        try:
            with self.store.locked(deadline):
                host_deadline = self.store.deadline(self.policy['coordination']['host_event_budget_seconds'])
                while True:
                    observed = self.authority.observe(episode)
                    if observed['state'] == 'ended':
                        break
                    remaining = min(self.store.remaining(deadline), self.store.remaining(host_deadline))
                    if remaining <= 0:
                        raise ReadFailure('cleanup_incomplete', details=details, status='incomplete')
                    time.sleep(min(self.policy['coordination']['end_observation_poll_seconds'], remaining))
                receipt = dict(schema_version=1, event='SessionEnd', event_id=observed['end_event_id'], episode=episode)
                self._validate_owner(record, require_tree=False)
                if not target.exists():
                    if not self.authority.verify_end_receipt(receipt):
                        raise ReadFailure('cleanup_incomplete', details=details, status='incomplete')
                    record['phase'] = 'ended'
                    self.store.save(record)
                    self.log.append('already_absent', episode, 'confirmed_end_target_absent')
                    return 'already_absent', details
                if record.get('phase') == 'ended':
                    raise ReadFailure('cleanup_incomplete', details=details, status='incomplete')
                if self.store.remaining(deadline) <= 0:
                    raise ReadFailure('cleanup_incomplete', details=details, status='incomplete')
                record['phase'] = 'ending'
                self.store.save(record)
                # A failed preliminary diagnostic does not undo confirmed authority.
                # Complete the removal, then truthfully report any logging failure.
                diagnostic_failed = False
                try:
                    self.log.append('incomplete', episode, 'confirmed_end_removal_pending')
                except OSError:
                    diagnostic_failed = True
                if (not self.authority.verify_end_receipt(receipt) or self.store.remaining(deadline) <= 0):
                    raise ReadFailure('cleanup_incomplete', details=details, status='incomplete')
                shutil.rmtree(target)
                record['phase'] = 'ended'
                self.store.save(record)
                self.log.append('cleaned', episode, 'confirmed_end_removed')
                if diagnostic_failed:
                    raise ReadFailure('cleanup_incomplete', details=details, status='incomplete')
                return 'cleaned', details
        except (OSError, ReadFailure) as exc:
            try:
                self.log.append('incomplete', episode, str(exc))
            except OSError:
                pass
            raise ReadFailure('cleanup_incomplete', details=details, status='incomplete') from exc
