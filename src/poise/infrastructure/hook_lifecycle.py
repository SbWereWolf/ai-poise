"""Configured native lifecycle delivery through the existing process owner.

The hook registry owns native facts. The configured application owns its data.
These current-run observations describe delivery, never another session registry.
"""
from copy import deepcopy
import json
import os
import shlex
from pathlib import Path
import tempfile
import time
import uuid

from ..common import PoiseError
from ..execution import RegisteredCheckRunner, RegisteredLaunchFailure
from ..modules.capabilities.domain import positive
from ..modules.foundation.validation import configuration_fields, nonempty
from .goal_config import atomic_write, read_document, strict_json


def consumer_options(raw):
    fields = {'argv', 'cwd', 'environment', 'timeout_seconds', 'cleanup_seconds',
              'poll_seconds', 'completion_reserve_seconds', 'max_output_bytes'}
    configuration_fields(raw, fields, 'lifecycle_consumer')
    value = {key: deepcopy(raw[key]) for key in fields}
    argv = value['argv']
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(arg, str) or '\x00' in arg for arg in argv) or not argv[0]):
        raise PoiseError('lifecycle_consumer.argv: explicit nonempty command required')
    nonempty(value['cwd'], 'lifecycle_consumer.cwd')
    if not Path(value['cwd']).is_absolute():
        raise PoiseError('lifecycle_consumer.cwd: absolute working directory required')
    env = value['environment']
    if (not isinstance(env, dict) or any(
            not isinstance(key, str) or not key or '=' in key or '\x00' in key
            or not isinstance(item, str) or '\x00' in item for key, item in env.items())):
        raise PoiseError('lifecycle_consumer.environment: explicit string environment required')
    for key in ('timeout_seconds', 'cleanup_seconds', 'poll_seconds', 'completion_reserve_seconds'):
        positive(value[key], 'lifecycle_consumer.' + key)
    if type(value['max_output_bytes']) is not int or value['max_output_bytes'] <= 0:
        raise PoiseError('lifecycle_consumer.max_output_bytes: positive integer required')
    return value


def save(settings, path, record):
    atomic_write(path, (json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode(),
                 settings.raw['file_mode'])


def prepare(settings, definition_path, native_event, receipt, deadline):
    path = settings.observations / uuid.uuid4().hex / settings.raw['response_file']
    record = {'schema': 'poise-hook-lifecycle-delivery-1', 'status': 'pending', 'reason': None,
              'settings_path': str(settings.path), 'definition_path': str(Path(definition_path).resolve()),
              'packet': {'schema': 'poise-hook-lifecycle-1', 'native_event': deepcopy(native_event),
                         'event_receipt': receipt, 'deadline_monotonic': deadline}, 'execution': None}
    # Persist immediately after native commitment, before any subject/application work.
    save(settings, path, record)
    return path, record


def execute(settings, options, argv, cwd, environment, packet, deadline, directory, *, timeout):
    remaining = deadline - time.monotonic() - options['cleanup_seconds'] - options['completion_reserve_seconds']
    if remaining <= 0:
        return None, None, 'budget_exhausted'
    directory.mkdir(parents=True, exist_ok=True)
    runner = RegisteredCheckRunner(preview_chars=settings.raw['output_chars'])
    run_id, size, exceeded = directory.name, 0, False

    def capture(stream, chunk):
        nonlocal size, exceeded
        size += len(chunk)
        if size > options['max_output_bytes']:
            exceeded = True
            runner.cancel(run_id)
        return False

    try:
        with tempfile.TemporaryFile(dir=directory) as source:
            if packet is not None:
                source.write((json.dumps(packet, ensure_ascii=False, allow_nan=False) + '\n').encode())
                source.seek(0)
            execution = runner.run(run_id, argv, Path(cwd), environment, min(timeout, remaining),
                directory / settings.raw['probe_files']['stdout'],
                directory / settings.raw['probe_files']['stderr'],
                poll_seconds=options['poll_seconds'], cleanup_seconds=options['cleanup_seconds'],
                capture_observer=capture, stdin_file=source)
    except RegisteredLaunchFailure as error:
        return None, {'error_kind': type(error).__name__, 'detail': str(error)}, 'launch_failed'
    except (PoiseError, OSError, ValueError) as error:
        return None, {'error_kind': type(error).__name__, 'detail': str(error)}, 'execution_unconfirmed'
    if exceeded:
        return None, execution, 'output_limit'
    if execution['timed_out']:
        return None, execution, 'timed_out'
    if not (execution['capture_complete'] and execution['termination']['child_reaped']
            and execution['termination']['process_group_stopped']):
        return None, execution, 'teardown_incomplete'
    if execution['actual_exit_code'] != 0:
        return None, execution, 'nonzero_exit'
    try:
        value = strict_json(Path(execution['stdout']).read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('JSON object required')
    except (PoiseError, OSError, UnicodeError, ValueError, RecursionError):
        return None, execution, 'invalid_output'
    return value, execution, None


def native_output(value, event):
    output = {}
    if 'systemMessage' in value:
        if not isinstance(value['systemMessage'], str):
            raise PoiseError('lifecycle consumer systemMessage must be a string')
        output['systemMessage'] = value['systemMessage']
    if 'hookSpecificOutput' in value:
        hook = value['hookSpecificOutput']
        if (event != 'SessionStart' or not isinstance(hook, dict)
                or hook.get('hookEventName') != event or not isinstance(hook.get('additionalContext'), str)):
            raise PoiseError('lifecycle consumer returned an invalid native context')
        output['hookSpecificOutput'] = {'hookEventName': event, 'additionalContext': hook['additionalContext']}
    return output


def deliver(settings, path, record):
    options = settings.consumer
    packet = record['packet']
    if packet['event_receipt']['native_start_event_id'] is None:
        record.update(status='incomplete', reason='missing_start')
        save(settings, path, record)
        raise PoiseError(f'lifecycle consumer needs a genuine SessionStart; native End is retained at {path}; '
                         'restore the intended native episode through its configured owner')
    value, execution, reason = execute(settings, options, options['argv'], options['cwd'], options['environment'],
        packet, packet['deadline_monotonic'], path.parent, timeout=options['timeout_seconds'])
    if reason is None:
        try:
            value = native_output(value, packet['event_receipt']['event'])
        except PoiseError:
            reason = 'invalid_output'
    record.update(status='delivered' if reason is None else 'incomplete', reason=reason, execution=execution)
    save(settings, path, record)
    if reason is not None:
        raise PoiseError(f'lifecycle consumer delivery {reason}; native event is committed. '
                         f'Correct the configured consumer and retry the saved delivery with '
                         f'poise hook --settings {settings.path} --definition {record["definition_path"]} --retry {path}')
    return value


def deliver_context(settings, path, record, invoke):
    """The built-in context subject shares the genuine delivery/retry owner.

    Inline effects have no subprocess receipt. A final native output is cached
    only after its effects and presentation both complete. An incomplete saved
    delivery remains recoverable; no filesystem/database atomicity is invented.
    """
    previous = record.get('general_context')
    if previous is not None and previous['status'] == 'complete':
        return deepcopy(previous['output'])
    output, complete, reason = invoke()
    record['general_context'] = dict(status='complete' if complete else 'incomplete',
                                     reason=reason, output=deepcopy(output))
    record.update(status='delivered' if complete else 'incomplete', reason=reason)
    try:
        save(settings, path, record)
    except OSError as error:
        raise PoiseError('Native effects may already be committed; retry the genuine saved delivery: '
                         f'poise hook --settings {shlex.quote(str(settings.path))} '
                         f'--definition {shlex.quote(record["definition_path"])} '
                         f'--retry {shlex.quote(str(path))}') from error
    return output


def probe(settings):
    options = settings.consumer
    if options is None:
        raise PoiseError('lifecycle consumer is not configured; configure it through runtime-config reconfigure and retry installation')
    deadline = time.monotonic() + options['timeout_seconds'] + options['cleanup_seconds'] + options['completion_reserve_seconds']
    directory = settings.observations / uuid.uuid4().hex
    value, execution, reason = execute(settings, options, options['argv'] + ['--dry-run'], options['cwd'],
        options['environment'], None, deadline, directory, timeout=options['timeout_seconds'])
    save(settings, directory / settings.raw['response_file'], {
        'schema': 'poise-hook-lifecycle-probe-1', 'status': 'ready' if reason is None else 'incomplete',
        'reason': reason, 'execution': execution})
    if reason is not None:
        raise PoiseError(f'lifecycle consumer dry-run {reason}; correct its configuration and retry installation')
    return value


def subject_context(settings, delivery_path):
    record = read_document(delivery_path)
    options = settings.consumer
    bootstrap = ('import sys;sys.path.insert(0,sys.argv[1]);'
                 'from poise.infrastructure.hook_lifecycle import render_subject_context;'
                 'render_subject_context(sys.argv[2])')
    argv = [settings.raw['python'], '-P', '-s', '-B', '-c', bootstrap,
            settings.raw['source_root'], str(delivery_path)]
    deadline = record['packet']['deadline_monotonic']
    value, execution, reason = execute(settings, options, argv, record['packet']['native_event']['cwd'],
        dict(os.environ), None, deadline, delivery_path.parent / 'context', timeout=max(0, deadline-time.monotonic()))
    record['subject_context'] = {'status': 'restored' if reason is None else 'incomplete',
                                 'reason': reason, 'execution': execution}
    save(settings, delivery_path, record)
    if reason is not None:
        raise PoiseError(f'Optional Task context {reason}; use the bound work launcher with bootstrap to restore the current Task. Evidence: {delivery_path}')
    return native_output(value, 'SessionStart')


def render_subject_context(delivery_path):
    """Existing subject owner in a bounded child; never replay a native event."""
    from .hook_transport import HookService
    from ..modules.hook_transport.domain import parse_native_event
    record = read_document(Path(delivery_path))
    service = HookService(record['settings_path'])
    packet, receipt = record['packet'], record['packet']['event_receipt']
    if receipt['event'] != 'SessionStart':
        raise PoiseError('Task context requires the recorded SessionStart')
    definition = service.definition(record['definition_path'])
    native = parse_native_event(packet['native_event'], definition)
    episode = {key: receipt[key] for key in ('project_id', 'session_id', 'binding_id')}
    episode['start_event_id'] = receipt['native_start_event_id']
    service.bind_lifecycle_receipt(record['definition_path'], receipt, episode)
    latest = service.registry.observe(definition.data['id'], receipt['binding_id'], 'SessionStart', None)['event']
    ended = service.registry.observe(definition.data['id'], receipt['binding_id'], 'SessionEnd', None)['event']
    if latest['id'] != receipt['event_id'] or (ended is not None and ended['id'] > receipt['event_id']):
        raise PoiseError('SessionStart context is superseded; restore the current Task through its bound work launcher')
    binding, _ = service.registry.get(receipt['binding_id'])
    print(json.dumps(service._runtime_event(definition, native, binding, packet['native_event'], receipt['event_id']),
                     ensure_ascii=False, allow_nan=False))
