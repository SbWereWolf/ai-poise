"""Current project observations and episode-owned checkpoint adapters."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import time

from ..application.work_context import WorkContext
from ..modules.work_context.domain import CheckpointState, ContextScope, ContextEvent
from ..modules.session_establishment.domain import CallerIdentity
from ..modules.document_intake.command import CommandFailure
from ..modules.document_intake.contracts import ReadFailure
from ..common import PoiseError
from .goal_config import atomic_write


class NativeWorkContextSession:
    def __init__(self, service, definition_path, binding, event, receipt, policy, *, snapshots=None):
        from ..composition import intake_session_tools
        from .document_intake.configuration import ReaderConfiguration
        from .document_intake.native import HookNativeAuthority
        from .project_binding import read_project_checkout
        from .session_establishment import establish_project_session
        self.service, self.definition_path, self.receipt = service, definition_path, receipt
        self.event, self.snapshots = event, snapshots
        bound = read_project_checkout(service.settings.project_config, Path(event['cwd']))
        caller = CallerIdentity.native(binding['project'], binding['external_session'])
        establish_project_session(bound, caller)
        self.configuration = ReaderConfiguration.load(bound, policy.data['reader_policy'], snapshots=snapshots)
        def authority():
            admission = self.configuration.policy['admission']
            if (Path(admission['hook_settings']).resolve() != service.settings.path
                    or Path(admission['definition_path']).resolve() != Path(definition_path).resolve()):
                raise PoiseError('Work context reader admission differs; select the installed reader policy')
            return HookNativeAuthority(service=service, project_id=bound.config['project'],
                                       definition_path=Path(definition_path), authority_identity=dict(admission))
        self.resources, self.sessions = intake_session_tools(self.configuration, caller, authority, service.clock)
        self.admit()

    def admit(self):
        r = self.resources
        observed = r.observe_active()
        self.service.bind_lifecycle_receipt(self.definition_path, self.receipt, observed['episode'])
        self.service.require_lifecycle_event(self.definition_path, self.receipt, self.event)
        with r.store.locked():
            r.admit(observed)
        self.scope = ContextScope(r.bound.config['project'], str(r.bound.checkout), deepcopy(r.scope.episode))

    def context(self):
        r = self.resources
        with r.store.locked():
            return r.registry.context(r.scope)[0]

    def advance(self):
        transition = self.service.registry.context_transition(self.receipt, self.scope.checkout, self.scope.episode)
        self.admit()
        current = self.service.registry.context_delivery_current(self.receipt, self.scope.checkout)
        r = self.resources
        with r.store.locked():
            if transition is not None:
                committed = r.registry.native_context_generation(r.scope, transition)
                if committed is not None:
                    return committed
                if not current:
                    raise PoiseError('A newer native context cycle exists; continue its genuine delivery rather than adding a late transition')
                return r.registry.advance_native_context(r.scope, transition)
            return r.registry.context(r.scope)[0]

    def revalidate(self, generation):
        self.admit()
        if self.context() != generation:
            raise PoiseError('Work context generation changed; use current native delivery and read its owners again')

    def require_current_delivery(self):
        if not self.service.registry.context_delivery_current(self.receipt, self.scope.checkout):
            raise PoiseError('A newer native context cycle exists; continue its genuine delivery instead of replacing its checkpoint')


class FileWorkContextStore:
    def __init__(self, session, policy):
        self.session = session
        r = session.resources
        self.path = r.target / policy.data['checkpoint_directory'] / r.scope.checkout_key / policy.data['checkpoint_filename']
        self.ref = str(self.path)

    def load(self):
        try:
            value = json.loads(self.path.read_bytes())
        except FileNotFoundError:
            return CheckpointState('missing', None)
        except (ValueError, UnicodeError, RecursionError):
            return CheckpointState('invalid', None)
        except OSError:
            return CheckpointState('unavailable', None)
        return CheckpointState.parse(value, self.session.scope)

    def save(self, value, generation):
        self.session.revalidate(generation)
        self.session.require_current_delivery()
        r = self.session.resources
        with r.store.locked():
            r.admit(dict(state='active', episode=self.session.scope.episode))
            if r.registry.context(r.scope)[0] != generation:
                raise PoiseError('Work context generation changed; retry its genuine native delivery')
            current = self.load()
            if current.value is not None and current.value['event']['id'] > value['event']['id']:
                raise PoiseError('A newer checkpoint exists; use its current native continuation')
            atomic_write(self.path, (json.dumps(value, ensure_ascii=False, allow_nan=False,
                                               sort_keys=True) + '\n').encode(), r.file_mode)

    def clear(self, generation):
        self.session.revalidate(generation)
        self.session.require_current_delivery()
        r = self.session.resources
        with r.store.locked():
            r.admit(dict(state='active', episode=self.session.scope.episode))
            if r.registry.context(r.scope)[0] != generation:
                raise PoiseError('Work context generation changed; retry its genuine native clear delivery')
            self.path.unlink(missing_ok=True)


class ProjectWorkContextFacts:
    def __init__(self, session, policy, binding, deadline):
        self.session, self.policy, self.binding, self.deadline = session, policy, binding, deadline
        self.optional = None

    def git(self):
        cwd = self.session.scope.checkout
        def run(*arguments):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Native context observation budget exhausted')
            return subprocess.run(['git', '-C', cwd, *arguments], check=True, capture_output=True,
                                  timeout=remaining).stdout
        head = run('rev-parse', 'HEAD').decode().strip()
        raw = run('status', '--porcelain=v1', '-z', '--untracked-files=all')
        records, paths, index = raw.split(b'\0'), [], 0
        while index < len(records):
            row = records[index]
            if len(row) > 3:
                paths.append(row[3:].decode('utf-8'))
                if row[0:1] in (b'R', b'C') or row[1:2] in (b'R', b'C'):
                    index += 1
            index += 1
        return dict(status='available', head=head, status_sha256=hashlib.sha256(raw).hexdigest(),
                    changed_paths=paths)

    def instructions(self):
        from ..application.project_instructions import ProjectInstructions
        from .project_instructions import FileInstructionSource
        options = self.policy.data['instructions']
        result = ProjectInstructions(FileInstructionSource(options['catalog'], options['root'],
            snapshots=self.session.snapshots)).prepare({
            'operation': 'prepare', 'project_config': str(self.session.service.settings.project_config),
            'checkout': self.session.scope.checkout, 'policy': options['policy'],
            'paths': options['paths'], 'previous': None})
        fields = ('kind', 'id', 'root', 'path', 'scopes', 'required', 'reasons', 'available', 'sha256')
        return dict(status='ready', materials=[{key: deepcopy(m[key]) for key in fields} for m in result['materials']])

    def coverage(self):
        from ..application.command_intake import CommandIntake
        from .document_intake.command_configuration import load_command_policy
        from .document_intake.command_source import CommandSource
        r = self.session.resources
        configuration = self.session.configuration
        policy, digest = load_command_policy(configuration.policy, snapshots=self.session.snapshots)
        owner = CommandIntake(r, self.session.sessions, CommandSource(r.bound.checkout, policy),
                              policy, digest, r.clock)
        context, details = owner.coverage()
        return dict(status='available', generation=context['generation'], ref=str(r.registry.path),
                    **details)

    def _optional(self):
        if self.optional is not None:
            return self.optional
        from .session_establishment import establish_poise
        caller = self.session.resources.caller
        h = establish_poise(self.session.service.settings.project_config, caller, [],
                            self.session.service.clock, self.binding['session_id']).runtime
        metadata = h.context_metadata([])
        task = metadata['task']
        current = h.current_task()
        identity = None if current is None else (current['id'], current['version'])
        expected = None if task is None else (task['id'], task['version'])
        if identity != expected:
            raise PoiseError('Optional Task context changed; read its current owner again')
        from .task_paths import task_root
        context = None if current is None else dict(task_root=str(task_root(
            h.state, h.paths, current['id'], current['sprint_id'])))
        self.optional = metadata, context, h
        return self.optional

    def work(self):
        if not self.policy.data['work_owner']['enabled']:
            return dict(status='not_configured')
        metadata, context, _ = self._optional()
        task = metadata['task']
        return dict(status='available', task=None if task is None else task['id'],
                    stage=None if task is None else task['stage'],
                    ref=None if context is None else context['task_root'])

    def check(self):
        if not self.policy.data['check_owner']['enabled']:
            return dict(status='not_configured')
        from ..application.check_attempts import is_check_attempt, validate_identity, validate_termination
        metadata, context, h = self._optional()
        task = metadata['task']
        pending = None if task is None else task['pending']
        if not is_check_attempt(pending) or not pending.get('runs'):
            return dict(status='not_observed')
        if (pending.get('version') != 2 or pending.get('task_id') != task['id']
                or pending.get('actor') != metadata['session'] or pending.get('task_version') != task['version']):
            raise PoiseError('Current check descriptor identity is unavailable; read the check owner')
        validate_identity(pending['identity'])
        # The check owner reserves every future run before starting the first.
        # Report the latest observed start, not the preallocated queue tail.
        run = next((item for item in reversed(pending['runs']) if item['started'] is True),
                   pending['runs'][0])
        validate_termination(pending, run)
        proof = run['termination']
        completed = proof is not None and all(proof[key] is True for key in (
            'child_reaped', 'process_group_stopped', 'capture_complete'))
        return dict(status='completed' if completed else 'pending', run_id=run['run_id'],
                    attempt_id=pending['attempt_id'], ref=str(Path(context['task_root']) / h.paths['runs'] / run['run_id']))

    def observe(self):
        result = {}
        for name in ('git', 'instructions', 'coverage', 'work', 'check'):
            try:
                result[name] = getattr(self, name)()
            except (PoiseError, ReadFailure, CommandFailure, OSError, ValueError, KeyError,
                    subprocess.SubprocessError) as error:
                result[name] = dict(status='unavailable', reason=getattr(error, 'code', type(error).__name__))
                if name == 'instructions':
                    result[name]['materials'] = []
                if name == 'coverage':
                    result[name].update(generation=self.session.context(), ref=str(self.session.resources.registry.path))
        return result


def native_context_event(service, definition_path, binding, event, receipt, deadline, *, policy=None, snapshots=None):
    policy = service.settings.general_context if policy is None else policy
    session = NativeWorkContextSession(service, definition_path, binding, event, receipt, policy, snapshots=snapshots)
    store = FileWorkContextStore(session, policy)
    facts = ProjectWorkContextFacts(session, policy, binding, deadline)
    return WorkContext(policy, session, store, facts), ContextEvent(receipt['event_id'], receipt['event'], event.get('source'))
