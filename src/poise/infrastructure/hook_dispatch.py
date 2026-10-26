"""Immutable configuration snapshots and short-lock event-delivery persistence."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

from ..common import PoiseError, digest
from ..modules.hook_transport.dispatch import BUILTINS, DispatchPlan, HandlerConfiguration, HandlerInvocation
from ..modules.document_intake.contracts import ReadFailure
from ..modules.document_intake.command import CommandFailure
from .goal_config import atomic_write, read_document, strict_json
from .locking import exclusive_lock


class FileDispatchConfiguration:
    @staticmethod
    def load_plan(definition, event, profile, settings):
        if definition.data['schema'] != 'hook-definition-2':
            raise PoiseError('Active hook delivery requires explicit hook-definition-2 publication')
        invocations, revisions, selected_builtins = [], [], set()
        configurations = {}
        for position, address in enumerate(definition.data['routes'][event]):
            path = Path(address)
            if address not in configurations:
                try:
                    raw = strict_json(path.read_bytes().decode('utf-8'))
                except (OSError, UnicodeError, ValueError) as exc:
                    raise PoiseError(f'Missing or invalid handler configuration: {path}; restore it before delivery') from exc
                configuration = HandlerConfiguration.parse(raw)
                handler_resources = []
                for handler in configuration.raw['handlers']:
                    if handler['implementation']['kind'] == 'builtin':
                        from .hook_handlers import BuiltinHandlers
                        try:
                            handler_resources.append(BuiltinHandlers.preflight(handler, settings, profile))
                        except (ReadFailure, CommandFailure, OSError, UnicodeError) as exc:
                            raise PoiseError(f'Invalid builtin policy in {path}: {exc}; '
                                             'correct the selected policy before delivery') from exc
                    else:
                        handler_resources.append({})
                revision = digest({'configuration': raw, 'resources': handler_resources})
                configurations[address] = configuration, handler_resources, revision
            configuration, handler_resources, revision = configurations[address]
            revisions.append({'path': str(path), 'revision': revision})
            for handler_position, handler in enumerate(configuration.raw['handlers']):
                implementation = handler['implementation']
                if implementation['kind'] == 'builtin':
                    name = implementation['name']
                    if event not in BUILTINS[name]:
                        raise PoiseError(f'Builtin {name} does not support {event}')
                    if name in selected_builtins:
                        raise PoiseError(f'Duplicate effectful builtin {name} for {event}')
                    selected_builtins.add(name)
                    if name in ('task-context', 'task-runtime-cleanup') and profile['task_adapter'] is None:
                        raise PoiseError('task_adapter_required: explicitly connect Task before selecting its handler')
                identity = {'id': configuration.raw['id'], 'handler_id': handler['id'],
                            'configuration_position': position, 'handler_position': handler_position,
                            'path': str(path), 'revision': revision}
                invocations.append(HandlerInvocation(identity, deepcopy(handler),
                                                     deepcopy(handler_resources[handler_position]),
                                                     deepcopy(configuration.raw)))
        return DispatchPlan(tuple(invocations), tuple(revisions))


class FileDispatchDeliveryStore:
    def __init__(self, settings):
        self.settings = settings

    def read(self, path):
        return read_document(Path(path))

    def _locked(self, path):
        return exclusive_lock(Path(path).with_suffix('.lock'), self.settings.raw['lock_seconds'],
                              self.settings.raw['lock_poll_seconds'])

    def _write(self, path, value):
        atomic_write(Path(path), (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+'\n').encode(),
                     self.settings.raw['file_mode'])

    @staticmethod
    def _status(value):
        states = [item['status'] for item in value['invocations']]
        if 'unknown' in states:
            return 'unknown'
        if value['chain_failed'] or 'failed' in states:
            return 'failed'
        if not value['chain_complete'] or any(s in ('launching', 'running') for s in states):
            return 'running'
        return 'completed'

    def prepare(self, definition_path, event, receipt, plan):
        identity = digest([str(definition_path), receipt])
        path = self.settings.observations / identity / self.settings.raw['response_file']
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked(path):
            if path.is_file():
                prior = self.read(path)
                if prior['event_receipt'] != receipt or prior['definition_path'] != str(definition_path):
                    raise PoiseError('Saved event delivery authority differs; select its recorded receipt')
                return path
            rows = [{'invocation_id': str(uuid.uuid4()), 'mode': item.handler['mode'],
                     'configuration': item.configuration, 'configuration_source': item.configuration_source,
                     'handler': item.handler, 'resources': item.resources,
                     'status': 'prepared', 'execution': None, 'result': None}
                    for item in plan.invocations]
            self._write(path, {'schema': 'poise-hook-event-delivery-2', 'event_receipt': receipt,
                'definition_path': str(definition_path), 'settings_path': str(self.settings.path),
                'configuration_revisions': list(plan.revisions),
                'planned_invocations': rows, 'invocations': [],
                'event': deepcopy(event), 'response': {}, 'status': 'prepared',
                'chain_complete': False, 'chain_failed': False})
        return path

    def begin_invocation(self, path, planned):
        with self._locked(path):
            value = self.read(path)
            matches = [item for item in value['invocations'] if item['invocation_id'] == planned['invocation_id']]
            if matches:
                return deepcopy(matches[0])
            value['invocations'].append(deepcopy(planned))
            value['status'] = self._status(value)
            self._write(path, value)
            return deepcopy(planned)

    def update_invocation(self, path, invocation_id, **changes):
        with self._locked(path):
            value = self.read(path)
            matches = [item for item in value['invocations'] if item['invocation_id'] == invocation_id]
            if len(matches) != 1:
                raise PoiseError('Unknown invocation in selected saved delivery')
            matches[0].update(deepcopy(changes))
            value['status'] = self._status(value)
            self._retire_inputs(path, value)
            self._write(path, value)
            return value

    def save_response(self, path, response, *, failed):
        with self._locked(path):
            value = self.read(path)
            value.update(response=deepcopy(response), chain_complete=True, chain_failed=failed)
            value['status'] = self._status(value)
            self._retire_inputs(path, value)
            self._write(path, value)
            return value

    def _retire_inputs(self, path, value):
        """Retire only this delivery's consumed spool; preserve raw outputs."""
        if value['status'] != 'completed' or value.get('inputs_retired') is True:
            return
        for invocation in value['invocations']:
            # Invocation IDs are allocated by prepare, and consumers have all
            # completed before this owner removes their temporary input file.
            identifier = str(uuid.UUID(invocation['invocation_id']))
            (self.invocation_directory(path, identifier) / 'input.json').unlink(missing_ok=True)
        event = value['event']
        if event['hook_event_name'] == 'PostToolUse' and any(
                item['handler']['implementation']['kind'] == 'builtin'
                and item['handler']['implementation']['name'] == 'tool-result'
                for item in value['planned_invocations']):
            # The result owner retains the complete machine source/provenance.
            # Only temporary handler stdin is retired, never that tool result.
            value['inputs_retired'] = True
            return
        value['event'] = {key: deepcopy(event[key]) for key in (
            'hook_event_name', 'session_id', 'cwd', 'source', 'reason', 'turn_id', 'trigger') if key in event}
        if event['hook_event_name'] == 'UserPromptSubmit':
            value['event']['prompt'] = ''  # Required wire shape, never the consumed text.
        value['inputs_retired'] = True

    @staticmethod
    def invocation_directory(path, invocation_id):
        return Path(path).parent / invocation_id

    def save_input(self, directory, packet):
        directory.mkdir(parents=True, exist_ok=True)
        self._write(directory / 'input.json', packet)


class HandlerExecutionFailure(PoiseError):
    def __init__(self, detail, execution=None, *, outcome='failed'):
        super().__init__(detail)
        self.execution, self.outcome = execution, outcome


class HandlerProcessExecutor:
    """Use the common runner; a worker owns asynchronous process lifetime."""
    def __init__(self, settings, store, builtin=None, *, deadline=None):
        self.settings, self.store, self.builtin = settings, store, builtin
        self.deadline = deadline

    def execute(self, handler, packet, directory, *, resources, launch_observer=None):
        if handler['implementation']['kind'] == 'builtin':
            if self.builtin is None:
                raise HandlerExecutionFailure('Builtin capability owner is not composed')
            try:
                return self.builtin(handler, packet, resources=resources)
            except (ReadFailure, CommandFailure) as exc:
                raise HandlerExecutionFailure(f'{type(exc).__name__}: {exc}') from exc
        from ..execution import RegisteredCheckRunner
        import os
        import time
        budget = handler['execution']['run_seconds']
        if self.deadline is not None:
            budget = min(budget, self.deadline-time.monotonic()-handler['execution']['cleanup_seconds'])
        if budget <= 0:
            raise HandlerExecutionFailure('Native callback budget exhausted before handler launch')
        directory = Path(directory)
        runner = RegisteredCheckRunner(preview_chars=self.settings.raw['output_chars'])
        with (directory / 'input.json').open('rb') as source:
            execution = runner.run(packet['invocation_id'], handler['implementation']['argv'],
                Path(packet['event']['cwd']), dict(os.environ), budget,
                directory / self.settings.raw['probe_files']['stdout'],
                directory / self.settings.raw['probe_files']['stderr'],
                cleanup_seconds=handler['execution']['cleanup_seconds'], stdin_file=source,
                launch_observer=launch_observer)
        termination = execution['termination']
        if (execution['actual_exit_code'] != 0 or execution['timed_out'] or execution['cancelled']
                or not execution['capture_complete'] or not termination['child_reaped']
                or not termination['process_group_stopped']):
            raise HandlerExecutionFailure('Handler process failed; read its complete execution capture', execution)
        try:
            raw = strict_json(Path(execution['stdout']).read_bytes().decode('utf-8'))
        except (PoiseError, OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise HandlerExecutionFailure('Handler returned invalid complete JSON output', execution) from exc
        return raw, execution

    def launch(self, handler, packet, directory, delivery_path):
        import os
        import select
        import subprocess
        import time
        identifier = packet['invocation_id']
        deadline = time.monotonic()+handler['execution']['launch_seconds']
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        self.store.update_invocation(delivery_path, identifier, status='launching',
                                     launch_deadline_monotonic=deadline)
        read_fd, write_fd = os.pipe()
        child = None
        try:
            environment = dict(os.environ)
            environment['PYTHONPATH'] = self.settings.source_python_path()
            argv = [self.settings.raw['python'], '-P', '-B', '-m',
                    'poise.infrastructure.hook_worker', '--settings', str(self.settings.path),
                    '--delivery', str(delivery_path), '--invocation', identifier,
                    '--ack-fd', str(write_fd)]
            with (Path(directory) / 'worker.stdout.txt').open('wb') as out, \
                    (Path(directory) / 'worker.stderr.txt').open('wb') as err:
                child = subprocess.Popen(argv, cwd=packet['event']['cwd'], env=environment,
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    start_new_session=True, close_fds=True, pass_fds=(write_fd,))
            os.close(write_fd)
            write_fd = None
            data = b''
            while time.monotonic() < deadline:
                readable, _, _ = select.select([read_fd], [], [], max(0, deadline-time.monotonic()))
                if not readable:
                    break
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                data += chunk
                if b'\n' in data:
                    acknowledgment = strict_json(data.decode('utf-8'))
                    if acknowledgment == {'invocation_id': identifier, 'launched': True}:
                        return
                    raise HandlerExecutionFailure('Worker rejected the actual handler launch')
            row = next(item for item in self.store.read(delivery_path)['invocations']
                       if item['invocation_id'] == identifier)
            if row.get('launched') is True:
                return  # Durable genuine post-Popen fact survived a lost ack.
            if row['status'] == 'failed' or child.poll() is not None:
                child.wait()
                raise HandlerExecutionFailure('Worker failed before confirming the handler launch; read its launch capture')
            raise HandlerExecutionFailure('Handler launch is unconfirmed; use hook --retry on this saved delivery '
                                          'without launching a new event', outcome='unknown')
        except OSError as exc:
            raise HandlerExecutionFailure(f'Worker launch failed: {exc}') from exc
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
