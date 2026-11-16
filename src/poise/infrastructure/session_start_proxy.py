"""Working projection and session-capture adapters for original native callbacks."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import uuid

from ..common import PoiseError, digest, encoded, exact_keys
from ..execution import RegisteredCheckRunner, RegisteredLaunchFailure
from ..modules.hook_transport.session_start_proxy import InitialSessionStartProjection
from ..modules.session_establishment.domain import CallerIdentity
from ..modules.result_views.domain import utf8_json_text
from .goal_config import read_document, strict_json
from .session_establishment import project_session_runtime_path, observed_project_session_id



def snapshot_path(settings):
    return settings.hooks_file.with_name(settings.hooks_file.name + '.session-start-proxy.json')


def snapshot(settings):
    path = snapshot_path(settings)
    if not path.exists():
        return None
    value = read_document(path)
    if value.get('schema') != 'initial-session-start-proxy-1' or value['hooks_path'] != str(settings.hooks_file):
        raise PoiseError('Invalid SessionStart projection; restore its owner configuration')
    return value


def require_settled(settings, allowed=None):
    state = snapshot(settings)
    reference = None if state is None else state.get('publication')
    if reference is None or allowed == (reference['owner_settings_path'], reference['request_id']):
        return
    from .hook_transport import HookSettings
    from .diagnostic_io import readonly_database
    owner = HookSettings(reference['owner_settings_path'])
    with readonly_database(owner.database) as db:
        row = db.execute('SELECT data FROM operations WHERE id=?', (reference['request_id'],)).fetchone()
    if row is None or json.loads(row[0])['phase'] != 'completed':
        raise PoiseError('Pending shared SessionStart publication: repeat saved request '
                         + reference['request_id'] + ' through runtime-config --settings '
                         + reference['owner_settings_path'])


def body(value):
    return (utf8_json_text(value, indent=2) + '\n').encode()


def project_document(settings, document, prior=None, settings_overrides=None):
    prior = snapshot(settings) if prior is None else prior
    if not prior or not prior['active']:
        return document, prior
    after = deepcopy(prior)
    after['groups'] = deepcopy(document['hooks']['SessionStart'])
    limits = InitialSessionStartProjection.budgets(after['groups'])
    after['entry']['hooks'][0].update(timeout=limits['timeout'], additionalContextLimit=limits['context'])
    after['budgets'] = limits
    after['max_input_bytes'], after['output_chars'] = original_limits(after['groups'], settings_overrides)
    return InitialSessionStartProjection.physical(document, after), after


def original_limits(groups, overrides=None):
    from .hook_transport import HookSettings
    admissions, outputs = [], []
    for group in groups:
        for handler in group['hooks']:
            tokens = shlex.split(handler['command'])
            try:
                path = tokens[tokens.index('--settings') + 1]
                settings = HookSettings.proposed(path, overrides[path]) if overrides and path in overrides else HookSettings(path)
                definition = Path(tokens[tokens.index('--definition') + 1])
                if handler['command'] != settings.command('hook', '--definition', str(definition)):
                    raise ValueError('different producer')
            except (ValueError, IndexError, OSError) as exc:
                raise PoiseError('Unknown initial callback producer; select its explicit Poise hook settings before publication') from exc
            admissions.append(settings.raw['max_input_bytes'])
            outputs.append(settings.raw['output_chars'])
    return min(admissions), sum(outputs) + 6 * (len(outputs) - 1)


def publish(repository, packet):
    """Use the existing registry journal and native writer, not another registry."""
    from . import hook_transport as transport
    exact_keys(packet, {'action', 'request_id', 'expected_revision'}, 'session-start-proxy input')
    if packet['action'] not in ('publish', 'restore'):
        raise PoiseError('SessionStart proxy action must be publish or restore')
    settings, registry = repository.settings, repository.registry
    key = packet['request_id']
    if not isinstance(key, str) or not key.strip():
        raise PoiseError('SessionStart request_id required')
    identity = digest(['session-start-proxy', packet])
    with settings.native_file_locked():
        require_settled(settings, (str(settings.path), key))
        with registry.transaction() as db:
            row = db.execute('SELECT data FROM operations WHERE id=?', (key,)).fetchone()
        operation = None if row is None else json.loads(row[0])
        if operation is not None:
            if operation.get('digest') != identity:
                raise PoiseError('Request id reused with conflicting SessionStart inputs')
            if operation['phase'] == 'completed':
                return operation['receipt']
        else:
            if repository.revision() != packet['expected_revision']:
                raise PoiseError('SessionStart configuration revision changed; read current revision and submit matching expected_revision')
            native = read_document(settings.hooks_file)
            prior = snapshot(settings)
            logical = InitialSessionStartProjection.logical(native, prior)
            if packet['action'] == 'publish':
                if prior and prior['active']:
                    raise PoiseError('SessionStart proxy already active; use its current owner or restore before republishing')
                groups = logical.get('hooks', {}).get('SessionStart', [])
                budgets = InitialSessionStartProjection.budgets(groups)
                admission, output = original_limits(groups)
                entry = {'matcher': '', 'hooks': [{'type': 'command',
                    'command': settings.command('hook-proxy', '--snapshot', str(snapshot_path(settings))),
                    'timeout': budgets['timeout'], 'additionalContextLimit': budgets['context'],
                    'async': False, 'statusMessage': 'Run original SessionStart handlers'}]}
                after = {'schema': 'initial-session-start-proxy-1', 'active': True,
                    'hooks_path': str(settings.hooks_file), 'owner_project_config': str(settings.project_config),
                    'owner_settings_path': str(settings.path), 'entry': entry, 'groups': groups,
                    'budgets': budgets, 'max_input_bytes': admission, 'output_chars': output}
                candidate = InitialSessionStartProjection.physical(logical, after)
            else:
                if not prior or not prior['active']:
                    raise PoiseError('No active SessionStart projection; inspect current owner state')
                candidate = logical
                after = {k: prior[k] for k in ('schema', 'hooks_path', 'owner_project_config', 'owner_settings_path')}
                after['active'] = False
            after['publication'] = {'owner_settings_path': str(settings.path), 'request_id': key}
            native_body = body(candidate)
            revision = hashlib.sha256(native_body).hexdigest()
            receipt = {'status': 'published' if packet['action'] == 'publish' else 'restored',
                'request_id': key, 'hooks_path': str(settings.hooks_file),
                'snapshot_path': str(snapshot_path(settings)),
                'revision': repository._configuration_revision(revision, proxy_snapshot=after)}
            operation = {'kind': 'session-start-proxy', 'digest': identity, 'phase': 'pending',
                'files': [{'path': str(path), 'before': path.read_bytes().hex() if path.exists() else None,
                           'after': content.hex()} for path, content in (
                               (snapshot_path(settings), body(after)), (settings.hooks_file, native_body))],
                'receipt': receipt}
            with registry.transaction() as db:
                if repository.revision() != packet['expected_revision']:
                    raise PoiseError('SessionStart inputs changed during preparation; read revision and retry')
                db.execute('INSERT INTO operations VALUES(?,?)', (key, encoded(operation)))
        for item in operation['files']:
            path = Path(item['path'])
            current = path.read_bytes().hex() if path.exists() else None
            if current not in (item['before'], item['after']):
                raise PoiseError('Pending SessionStart publication drift; restore recorded inputs then repeat request ' + key)
        for item in operation['files']:
            path = Path(item['path'])
            if not path.exists() or path.read_bytes().hex() != item['after']:
                transport.atomic_write(path, bytes.fromhex(item['after']), settings.raw['file_mode'])
        with registry.transaction() as db:
            operation['phase'] = 'completed'
            db.execute('UPDATE operations SET data=? WHERE id=?', (encoded(operation), key))
            registry.event_in(db, None, 'session-start-proxy.completed', {'request_id': key})
        return operation['receipt']


class SessionStartMemberExecutor:
    def __init__(self, settings):
        self.settings = settings

    def begin(self, configuration, raw, event):
        self.configuration = configuration
        from .harness_config import load_harness_config
        _, profile, _ = load_harness_config(configuration['owner_project_config'])
        CallerIdentity.native(profile['project'], event['session_id'])
        assigned = observed_project_session_id(configuration['owner_project_config'], event['session_id'])
        self.directory = project_session_runtime_path(configuration['owner_project_config'], assigned) / 'initial-session-start-proxy' / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.input = self.directory / 'input.json'
        self.input.write_bytes(raw)
        self.index = 0
        self.record = {'session_id': event['session_id'], 'assigned_session_id': assigned, 'invocation_id': self.directory.name, 'members': []}

    def execute(self, position, handler):
        from .goal_config import atomic_write
        directory = self.directory / str(self.index)
        self.index += 1
        directory.mkdir()
        out, err = directory / 'stdout', directory / 'stderr'
        runner = RegisteredCheckRunner(self.settings.raw['output_chars'])
        exception = None
        try:
            with self.input.open('rb') as source:
                execution = runner.run(uuid.uuid4().hex, ['/bin/sh', '-c', handler['command']],
                    Path.cwd(), dict(os.environ), handler['timeout'], out, err,
                    cleanup_seconds=self.configuration['budgets']['cleanup_seconds'], stdin_file=source)
        except (RegisteredLaunchFailure, OSError) as exc:
            exception = {'type': type(exc).__name__, 'message': str(exc),
                         'cause': None if exc.__cause__ is None else str(exc.__cause__)}
            execution = {'actual_exit_code': None, 'stdout': str(out), 'stderr': str(err),
                         'capture_complete': isinstance(exc, RegisteredLaunchFailure) and out.is_file() and err.is_file(), 'exception': exception}
        stdout = out.read_bytes() if out.exists() else b''
        stderr = err.read_bytes() if err.exists() else b''
        failed = exception is not None or execution['actual_exit_code'] != 0 or execution.get('timed_out', False) or not execution['capture_complete']
        response, parse_error = None, None
        if stdout or not failed:
            try:
                response = strict_json(stdout.decode('utf-8'))
                if not isinstance(response, dict):
                    raise PoiseError('Native member response must be an object')
            except (PoiseError, ValueError, UnicodeError) as exc:
                response, parse_error, failed = None, str(exc), True
        diagnostic = stderr
        if exception:
            diagnostic += (exception['type'] + ': ' + exception['message'] + '\n').encode()
        elif parse_error:
            diagnostic += ('Invalid native member response: ' + parse_error + '\n').encode()
        elif execution.get('timed_out') and not diagnostic:
            diagnostic = b'Native member timed out\n'
        self.record['members'].append({'position': position, 'command': handler['command'],
            'execution': execution, 'response': response, 'parse_error': parse_error})
        atomic_write(self.directory / 'result.json', body(self.record), self.settings.raw['file_mode'])
        return {'exit_code': execution['actual_exit_code'], 'failed': failed,
                'response': response, 'stderr': diagnostic}


def retire_capture(settings, event, definition_path):
    state = snapshot(settings)
    if state and event.get('hook_event_name') == 'SessionEnd':
        from .hook_transport import _read_definition
        from ..modules.hook_transport.domain import parse_native_event
        parse_native_event(event, _read_definition(settings, definition_path))
        from .harness_config import load_harness_config
        from .session_establishment import remove_session_runtime
        _, profile, _ = load_harness_config(state['owner_project_config'])
        CallerIdentity.native(profile['project'], event['session_id'])
        assigned = observed_project_session_id(state['owner_project_config'], event['session_id'])
        remove_session_runtime(project_session_runtime_path(state['owner_project_config'], assigned) / 'initial-session-start-proxy')
