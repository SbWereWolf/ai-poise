"""Configured, non-qualifying Stride UAT execution (T029).

Trusted local commands are not sandboxed. This owner records observations, never
interprets an application's result.json as certification, and never resumes a run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid
from typing import Any

from shell import Problem, SCHEMA, loads

PROGRAM = 'stride-uat-program/v1'
PROTOCOL = 'stride-uat-execution-protocol/v1'
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
ENVIRONMENT_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
PROGRAM_FIELDS = {'schema', 'program_id', 'version', 'application_ids', 'graphs', 'steps'}
STEP_FIELDS = {'id', 'application_id', 'command_id', 'arguments', 'graph_id', 'timeout_seconds'}
PROFILE_FIELDS = {'os_ids', 'os_versions', 'python_versions', 'allow_root', 'isolation_id', 'environment'}


def fail(code: str, path: str, message: str) -> None:
    raise Problem(code, message, {'path': path})


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def shape(value: Any, fields: set[str], path: str, code: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        fail(code, path, 'Object fields are invalid')


def text(value: Any, path: str, code: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or '\0' in value or (not value and not empty):
        fail(code, path, 'Expected a nonempty string without NUL')
    try:
        value.encode('utf-8')
    except UnicodeError:
        fail(code, path, 'String is not Unicode scalar text')
    return value


def identifier(value: Any, path: str, limit: int, code: str) -> str:
    value = text(value, path, code)
    if not IDENTIFIER.fullmatch(value) or len(value.encode('utf-8')) > limit:
        fail(code, path, 'Invalid identifier')
    return value


def array(value: Any, path: str, code: str, *, empty: bool = False) -> list:
    if not isinstance(value, list) or (not value and not empty):
        fail(code, path, 'Expected a nonempty list')
    return value


def ids(value: Any, path: str, limits: dict, code: str) -> list[str]:
    result = array(value, path, code)
    if len(result) > limits['steps']:
        fail('limit_exceeded', path, 'Collection exceeds steps limit')
    for i, item in enumerate(result):
        identifier(item, f'{path}/{i}', limits['id_bytes'], code)
    if len(set(result)) != len(result):
        fail(code, path, 'Duplicate identifier')
    return result


def arguments(value: Any, path: str, limits: dict, code: str,
              *, executable: bool = False) -> list[str]:
    result = array(value, path, code, empty=not executable)
    if len(result) > limits['argv_items']:
        fail('limit_exceeded', path, 'argv_items limit exceeded')
    for i, item in enumerate(result):
        text(item, f'{path}/{i}', code, empty=True)
        if len(item.encode('utf-8')) > limits['argument_bytes']:
            fail('limit_exceeded', path, 'argument_bytes limit exceeded')
        if '${' in item and (executable or item != '${context}'):
            fail(code, f'{path}/{i}', 'Only the whole programme argument ${context} is expanded')
    if executable and (not result[0] or not Path(result[0]).is_absolute()):
        fail(code, path + '/0', 'Executable must be an absolute lexical path')
    return result


def overlaps(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def no_symlinks(path: Path) -> bool:
    return not any(p.is_symlink() for p in (path, *path.parents))


def read_input(path: Path, roots: list[Path], bound: int) -> bytes:
    """Read one bounded regular input; do not open FIFOs or follow symlinks."""
    try:
        resolved = path.resolve(strict=True)
        if not any(resolved.is_relative_to(r) for r in roots) or not no_symlinks(path):
            fail('input_invalid', str(path), 'Input escapes roots or is a symlink')
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                fail('input_invalid', str(path), 'Input is not a regular file')
            if metadata.st_size > bound:
                fail('limit_exceeded', str(path), 'program_bytes limit exceeded')
            raw = stream.read(bound + 1)
            if len(raw) > bound:
                fail('limit_exceeded', str(path), 'program_bytes limit exceeded')
            return raw
    except (OSError, ValueError, RuntimeError) as exc:
        raise Problem('input_invalid', 'Input is unavailable', {'path': str(path)}) from exc


@dataclass
class Prepared:
    program: dict
    program_sha256: str
    profile_id: str
    profile: dict
    config: dict
    state: Path
    workspace: Path
    roots: list[Path]
    hashes: dict[Path, str]
    selected_inputs: set[str] = field(default_factory=set)

    def unchanged(self) -> bool:
        try:
            return all(digest(read_input(p, self.roots, self.config['limits']['program_bytes'])) == h
                       for p, h in self.hashes.items())
        except Problem:
            return False


def prepare(cfg: dict, state: Path, workspace: Path, roots: list[Path],
            base: Path, inp: Any, operation: str, *, qualifying: bool = False) -> Prepared:
    shape(inp, {'program_path', 'profile_id'} | ({'run_id'} if operation == 'execute' else set()),
          '/input', 'invalid_packet')
    limits = cfg['limits']
    for key in ('profiles', 'commands', 'applications', 'inputs', 'isolations'):
        for name in cfg[key]:
            identifier(name, '/config/' + key, limits['id_bytes'], 'invalid_packet')
    if operation == 'execute':
        identifier(inp['run_id'], '/input/run_id', limits['id_bytes'], 'invalid_packet')
    profile_id = identifier(inp['profile_id'], '/input/profile_id', limits['id_bytes'], 'invalid_packet')
    if profile_id not in cfg['profiles']:
        fail('invalid_packet', '/input/profile_id', 'Unknown profile')
    profile = cfg['profiles'][profile_id]
    shape(profile, PROFILE_FIELDS, '/config/profiles/' + profile_id, 'invalid_packet')
    for key in ('os_ids', 'os_versions'):
        values = array(profile[key], '/config/profiles/' + key, 'invalid_packet')
        for value in values:
            text(value, '/config/profiles/' + key, 'invalid_packet')
    for version in array(profile['python_versions'], '/config/profiles/python_versions', 'invalid_packet'):
        if (not isinstance(version, list) or len(version) != 2
                or any(type(x) is not int or x < 0 for x in version)):
            fail('invalid_packet', '/config/profiles/python_versions', 'Expected [major,minor] integer pairs')
    if type(profile['allow_root']) is not bool:
        fail('invalid_packet', '/config/profiles/allow_root', 'Expected boolean')
    environment = profile['environment']
    if not isinstance(environment, dict):
        fail('invalid_packet', '/config/profiles/environment', 'Expected string map')
    for k, v in environment.items():
        if not ENVIRONMENT_KEY.fullmatch(k):
            fail('invalid_packet', '/config/profiles/environment', 'Invalid environment key')
        text(v, '/config/profiles/environment/' + k, 'invalid_packet', empty=True)
    iso_id = identifier(profile['isolation_id'], '/config/profiles/isolation_id', limits['id_bytes'], 'invalid_packet')
    if iso_id not in cfg['isolations']:
        fail('invalid_packet', '/config/isolations', 'Unknown isolation descriptor')
    isolation = cfg['isolations'][iso_id]
    if not qualifying:
        shape(isolation, {'kind', 'forbidden_roots'}, '/config/isolations/' + iso_id, 'invalid_packet')
        if isolation['kind'] != 'trusted-local':
            fail('capability_unavailable', '/config/isolations/' + iso_id, 'Requested isolation is unavailable')
        forbidden = []
        for value in array(isolation['forbidden_roots'], '/config/isolations/forbidden_roots', 'invalid_packet'):
            p = Path(text(value, '/config/isolations/forbidden_roots', 'invalid_packet'))
            if not p.is_absolute():
                fail('invalid_packet', '/config/isolations/forbidden_roots', 'Expected absolute resource roots')
            forbidden.append(p.resolve())
        own_roots = [state, workspace, *roots]
        for i, root in enumerate(own_roots):
            if any(overlaps(root, other) for other in own_roots[i + 1:] + forbidden):
                fail('invalid_packet', '/config', 'Resource roots overlap')
        for root in roots:
            if not root.is_dir():
                fail('input_invalid', str(root), 'Input root is unavailable')
    # Time arithmetic must be representable; there is no hidden duration default.
    for key in ('step_timeout_seconds', 'total_timeout_seconds'):
        try:
            finite = math.isfinite(float(limits[key]))
        except OverflowError:
            finite = False
        if not finite:
            fail('invalid_packet', '/config/limits/' + key, 'Duration is not representable')
    path = Path(text(inp['program_path'], '/input/program_path', 'invalid_packet'))
    path = path if path.is_absolute() else base / path
    raw = read_input(path, roots, limits['program_bytes'])
    try:
        program = loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Problem('invalid_json', 'Invalid programme JSON', {}) from exc
    shape(program, PROGRAM_FIELDS, '/program', 'program_invalid')
    if program['schema'] != PROGRAM:
        fail('unsupported_schema', '/program/schema', 'Unsupported programme schema')
    identifier(program['program_id'], '/program/program_id', limits['id_bytes'], 'program_invalid')
    text(program['version'], '/program/version', 'program_invalid')
    apps = ids(program['application_ids'], '/program/application_ids', limits, 'program_invalid')
    array(program['graphs'], '/program/graphs', 'program_invalid', empty=True)
    if program['graphs'] and not qualifying:
        fail('capability_unavailable', '/program/graphs', 'Graph qualification requires T030')
    steps = array(program['steps'], '/program/steps', 'program_invalid')
    if len(steps) > limits['steps']:
        fail('limit_exceeded', '/program/steps', 'steps limit exceeded')
    required_inputs: set[str] = set()
    for app_id in apps:
        if app_id not in cfg['applications']:
            fail('program_invalid', '/program/application_ids', 'Unknown application')
        app = cfg['applications'][app_id]
        shape(app, {'public_argv', 'delivery_inputs'}, '/config/applications/' + app_id, 'invalid_packet')
        arguments(app['public_argv'], '/config/applications/public_argv', limits, 'invalid_packet', executable=True)
        required_inputs.update(ids(app['delivery_inputs'], '/config/applications/delivery_inputs', limits, 'invalid_packet'))
    seen = set()
    for step in steps:
        shape(step, STEP_FIELDS, '/program/steps', 'program_invalid')
        sid = identifier(step['id'], '/program/steps/id', limits['id_bytes'], 'program_invalid')
        if sid in seen:
            fail('program_invalid', '/program/steps/id', 'Duplicate step')
        seen.add(sid)
        app_id = identifier(step['application_id'], '/program/steps/application_id', limits['id_bytes'], 'program_invalid')
        cid = identifier(step['command_id'], '/program/steps/command_id', limits['id_bytes'], 'program_invalid')
        if app_id not in apps or cid not in cfg['commands']:
            fail('program_invalid', '/program/steps', 'Unknown application or command binding')
        command = cfg['commands'][cid]
        shape(command, {'application_id', 'argv', 'source_inputs'}, '/config/commands/' + cid, 'invalid_packet')
        if command['application_id'] != app_id:
            fail('program_invalid', '/program/steps/command_id', 'Command belongs to another application')
        argv = arguments(command['argv'], '/config/commands/argv', limits, 'invalid_packet', executable=True)
        args = arguments(step['arguments'], '/program/steps/arguments', limits, 'program_invalid')
        if len(argv) + len(args) > limits['argv_items']:
            fail('limit_exceeded', '/program/steps/arguments', 'Combined argv exceeds argv_items')
        required_inputs.update(ids(command['source_inputs'], '/config/commands/source_inputs', limits, 'invalid_packet'))
        if step['graph_id'] is not None and not qualifying:
            fail('capability_unavailable', '/program/steps/graph_id', 'Graph qualification requires T030')
        duration = step['timeout_seconds']
        if type(duration) is not int or duration <= 0 or duration > limits['step_timeout_seconds']:
            fail('program_invalid', '/program/steps/timeout_seconds', 'Invalid step duration')
    hashes = {path: digest(raw)}
    if len(required_inputs) > limits['steps']:
        fail('limit_exceeded', '/config/inputs', 'Selected input count exceeds steps')
    if not qualifying:
        for input_id in sorted(required_inputs):
            if input_id not in cfg['inputs']:
                fail('invalid_packet', '/config/inputs', 'Missing declared input binding')
            item = cfg['inputs'][input_id]
            shape(item, {'kind', 'path'}, '/config/inputs/' + input_id, 'invalid_packet')
            if item['kind'] != 'file':
                fail('capability_unavailable', '/config/inputs/' + input_id, 'Only file inputs are supported in T029')
            selected = Path(text(item['path'], '/config/inputs/' + input_id, 'invalid_packet'))
            selected = selected if selected.is_absolute() else base / selected
            hashes[selected] = digest(read_input(selected, roots, limits['program_bytes']))

    return Prepared(program, digest(raw), profile_id, profile, cfg, state, workspace, roots, hashes, required_inputs)


def observe(profile: dict) -> dict:
    if os.name != 'posix' or not hasattr(os, 'killpg'):
        fail('capability_unavailable', '/environment', 'POSIX process groups are required')
    try:
        release = platform.freedesktop_os_release()
        os_id, os_version = release['ID'], release['VERSION_ID']
    except (OSError, KeyError) as exc:
        raise Problem('environment_mismatch', 'Cannot observe OS identity', {}) from exc
    version = [sys.version_info.major, sys.version_info.minor]
    root = os.geteuid() == 0
    if (os_id not in profile['os_ids'] or os_version not in profile['os_versions']
            or version not in profile['python_versions'] or (root and not profile['allow_root'])):
        fail('environment_mismatch', '/environment', 'Observed runtime does not match selected profile')
    env = profile['environment']
    return {'os_id': os_id, 'os_version': os_version, 'python_version': version,
            'root': root, 'environment_keys': sorted(env),
            'environment_sha256': digest(encoded(env)[:-1])}


def sync_directory(path: Path) -> None:
    """Persist names in this directory, not only the referenced file inode."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_directory(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Publish each new private directory before an owner can have effects."""
    if parents and not path.parent.is_dir():
        durable_directory(path.parent, parents=True, exist_ok=True)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if not (exist_ok and path.is_dir()):
            raise
    else:
        # Sync failure is fatal to this preparation; never treat mkdir alone as
        # a durable run reservation or start a child behind an unsynced name.
        sync_directory(path.parent)


def atomic_json(path: Path, value: dict, bound: int) -> None:
    raw = encoded(value)
    if len(raw) > bound:
        fail('limit_exceeded', str(path), 'result_bytes limit exceeded')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def file_record(path: Path) -> dict:
    with path.open('rb') as stream:
        h = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': h}


def step_record(prepared: Prepared, step: dict, run_id: str, env: dict) -> tuple[dict, dict]:
    cwd = prepared.workspace / run_id / step['id']
    context_path = cwd / 'context.json'
    command = prepared.config['commands'][step['command_id']]
    argv = command['argv'] + [str(context_path) if a == '${context}' else a for a in step['arguments']]
    for arg in argv:
        if len(arg.encode('utf-8')) > prepared.config['limits']['argument_bytes']:
            fail('limit_exceeded', '/program/steps/arguments', 'Expanded context exceeds argument_bytes')
    record = {'step_id': step['id'], 'application_id': step['application_id'],
              'command_id': step['command_id'], 'state': 'RUNNING', 'argv': argv, 'cwd': str(cwd),
              'environment_keys': env['environment_keys'], 'environment_sha256': env['environment_sha256'],
              'exit_code': None, 'signal': None, 'timed_out': False, 'duration_seconds': 0,
              'effects': 'unknown', 'reason': None,
              'stdout': {'path': str(cwd / 'stdout.bin'), 'bytes': 0, 'sha256': digest(b'')},
              'stderr': {'path': str(cwd / 'stderr.bin'), 'bytes': 0, 'sha256': digest(b'')}}
    context = {'schema': 'stride-uat-case-input/v1', 'run_id': run_id, 'step_id': step['id'],
               'application_id': step['application_id'],
               'public_argv': prepared.config['applications'][step['application_id']]['public_argv'],
               'workspace': str(cwd), 'result_path': str(cwd / 'result.json'), 'graph': None}
    return record, context


def collect(record: dict, environment: dict, timeout: float, limits: dict,
            *, launch_argv: list[str] | None = None) -> None:
    """Bound both pipes while draining; terminate the one owned process group."""
    started = time.monotonic()
    proc = None
    with selectors.DefaultSelector() as selector:
        streams = {}
        try:
            for name in ('stdout', 'stderr'):
                fd = os.open(record[name]['path'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                streams[name] = os.fdopen(fd, 'wb')
            executable = Path(record['argv'][0])
            if not executable.is_file() or not os.access(executable, os.X_OK):
                record.update(state='ERROR', effects='none', reason='spawn_failed')
                return
            if timeout <= 0:
                record.update(state='ERROR', effects='none', reason='timeout', timed_out=True)
                return
            # Caller has durably written the RUNNING intent before this attempt.
            try:
                proc = subprocess.Popen(launch_argv or record['argv'], cwd=record['cwd'], env=environment,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True, shell=False)
            except OSError:
                record.update(state='ERROR', effects='unknown', reason='spawn_failed')
                return
            record['effects'] = 'possible'
            for pipe, name in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            counts = {'stdout': 0, 'stderr': 0}
            deadline = started + timeout
            while selector.get_map() or proc.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    record.update(reason='timeout', timed_out=True)
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    try:
                        block = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    name = key.data
                    available = limits[name + '_bytes'] - counts[name]
                    piece = block[:available]
                    streams[name].write(piece)
                    counts[name] += len(piece)
                    if len(block) > available:
                        record['reason'] = 'output_limit'
                        break
                if record['reason']:
                    break
            if record['reason']:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            code = proc.wait()
            if launch_argv is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            record.update(state='EXITED', exit_code=code, signal=-code if code < 0 else None)
            if not record['reason'] and code != 0:
                record['reason'] = 'signal' if code < 0 else 'command_failed'
        finally:
            # Cleanup also applies to exceptions in collection or journal preparation.
            if proc is not None:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                for pipe in (proc.stdout, proc.stderr):
                    if pipe is not None:
                        pipe.close()
            for stream in streams.values():
                try:
                    stream.flush()
                    os.fsync(stream.fileno())
                finally:
                    stream.close()
            record['duration_seconds'] = time.monotonic() - started
            for name in streams:
                record[name] = file_record(Path(record[name]['path']))


def execute(prepared: Prepared, run_id: str) -> tuple[dict, int]:
    cfg = prepared.config
    env = observe(prepared.profile)
    limits = cfg['limits']
    steps = prepared.program['steps']
    state = prepared.state / run_id
    workspace = prepared.workspace / run_id
    path = state / 'execution.json'
    if os.path.lexists(state) or os.path.lexists(workspace):
        fail('run_conflict', '/input/run_id', 'Run or workspace already exists; no replay allowed')
    records = [step_record(prepared, step, run_id, env) for step in steps]
    journal = {'schema': PROTOCOL, 'run_id': run_id, 'program_id': prepared.program['program_id'],
               'program_version': prepared.program['version'], 'profile_id': prepared.profile_id,
               'program_sha256': prepared.program_sha256, 'qualification': 'not_evaluated',
               'execution_status': 'RUNNING', 'environment': env, 'steps': [],
               'remaining_step_ids': [s['id'] for s in steps], 'stop_reason': None}
    snapshot = {'schema': 'stride-uat-input-observations/v1',
                'files': [{'path': str(p), 'sha256': h} for p, h in prepared.hashes.items()]}
    # Reserve space for each terminal scalar before effects. Journal membership is
    # bounded by the programme, and raw command output is never embedded in JSON.
    maximum = {**journal, 'steps': [record for record, _ in records], 'stop_reason': 'inputs_changed'}
    reserve = len(encoded(maximum)) + len(steps) * (256 + len(str(limits['stdout_bytes']))
                                                 + len(str(limits['stderr_bytes'])))
    if reserve > limits['result_bytes'] or len(encoded(snapshot)) > limits['result_bytes']:
        fail('limit_exceeded', '/config/limits/result_bytes', 'Execution records exceed result budget')
    for _, context in records:
        if len(encoded(context)) > limits['result_bytes']:
            fail('limit_exceeded', '/config/limits/result_bytes', 'Context exceeds result budget')
    try:
        durable_directory(prepared.state, parents=True, exist_ok=True)
        durable_directory(state)
    except FileExistsError as exc:
        raise Problem('run_conflict', 'Run exists', {'run_id': run_id}) from exc
    except OSError as exc:
        raise Problem('store_unavailable', 'Cannot reserve run', {'run_id': run_id}) from exc
    try:
        durable_directory(prepared.workspace, parents=True, exist_ok=True)
        durable_directory(workspace)
    except OSError as exc:
        state.rmdir()  # Only our empty exclusive reservation, never a prior run.
        code = 'run_conflict' if isinstance(exc, FileExistsError) else 'store_unavailable'
        raise Problem(code, 'Cannot reserve workspace', {'run_id': run_id}) from exc
    atomic_json(state / 'inputs.json', snapshot, limits['result_bytes'])
    atomic_json(path, journal, limits['result_bytes'])
    deadline = time.monotonic() + limits['total_timeout_seconds']
    for step, (record, context) in zip(steps, records):
        if not prepared.unchanged():
            journal.update(execution_status='FAILED', stop_reason='inputs_changed')
            break
        cwd = Path(record['cwd'])
        try:
            durable_directory(cwd)
            atomic_json(cwd / 'context.json', context, limits['result_bytes'])
        except OSError:
            # No command was attempted for this step. Keep the ordered executed
            # prefix and the unstarted suffix instead of reporting completion.
            journal.update(execution_status='FAILED', stop_reason='io_failed')
            break
        journal['steps'].append(record)
        journal['remaining_step_ids'] = journal['remaining_step_ids'][1:]
        atomic_json(path, journal, limits['result_bytes'])
        try:
            collect(record, prepared.profile['environment'],
                    min(step['timeout_seconds'], deadline - time.monotonic()), limits)
        except OSError:
            record.update(state='ERROR', reason='io_failed')
        if not record['reason'] and not prepared.unchanged():
            record['reason'] = 'inputs_changed'
        if record['reason']:
            journal.update(execution_status='FAILED', stop_reason=record['reason'])
        atomic_json(path, journal, limits['result_bytes'])
        if record['reason']:
            break
    if journal['execution_status'] == 'RUNNING':
        journal['execution_status'] = 'COMPLETED'
    atomic_json(path, journal, limits['result_bytes'])
    result = {'schema': SCHEMA, 'status': 'executed', 'run_id': run_id,
              'execution_status': journal['execution_status'], 'qualification': 'not_evaluated',
              'completed_steps': sum(s['state'] == 'EXITED' for s in journal['steps']),
              'total_steps': len(steps), 'protocol_path': str(path), 'stop_reason': journal['stop_reason']}
    return result, 0 if journal['execution_status'] == 'COMPLETED' else 1


def run(cfg: dict, state: Path, workspace: Path, roots: list[Path], base: Path,
        operation: str, inp: Any) -> tuple[dict, int]:
    prepared = prepare(cfg, state, workspace, roots, base, inp, operation)
    if operation == 'validate':
        return {'schema': SCHEMA, 'status': 'validated', 'program_id': prepared.program['program_id'],
                'program_sha256': prepared.program_sha256, 'profile_id': prepared.profile_id,
                'step_count': len(prepared.program['steps']), 'graph_count': 0}, 0
    return execute(prepared, inp['run_id'])
