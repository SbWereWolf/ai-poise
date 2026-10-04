"""Revisioned qualifying UAT journal, exact replay, and suffix-only resume.

The run lock serializes this owner's journal. It cannot make external command
side effects atomic. Durable RUNNING intent is therefore never inferred complete
from a plausible result file left after a crash.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import time
import uuid
from typing import Any, Iterator

from shell import Problem, SCHEMA, exact, loads, valid_id
import runner
from provenance import Frozen, Selection, absolute, canonical, environment, hashed, inventory, limit, select
from isolation import Boundary, SAFE_ENVIRONMENT, Unavailable, active_identity, owned_directory, seal

PROTOCOL = 'stride-uat-protocol/v1'
JOURNAL_FIELDS = {'schema', 'run_id', 'revision', 'program_id', 'program_version', 'fingerprint',
                  'input_snapshot', 'profile_id', 'environment_observation', 'verdict', 'steps',
                  'remaining_step_ids', 'graph_results', 'stop_reason', 'diagnostics'}
STEP_FIELDS = {'step_id', 'state', 'attempt_id', 'argv', 'context_sha256', 'exit_code', 'signal',
               'timed_out', 'duration_seconds', 'effects', 'reason', 'outputs', 'graph'}
RESULT_FIELDS = {'schema', 'run_id', 'step_id', 'application_id', 'status', 'reason', 'graph'}
CONTEXT_FIELDS = {'schema', 'run_id', 'step_id', 'application_id', 'public_argv', 'workspace', 'result_path', 'graph'}
RECORD_FIELDS = {'path', 'sha256', 'bytes'}
ENVIRONMENT_FIELDS = {'os_id', 'os_version', 'python_version', 'uid', 'configured_environment_sha256'}
SNAPSHOT_FIELDS = {'program_sha256', 'config_sha256', 'environment_sha256', 'isolation_id', 'inputs', 'graphs'}
VERDICTS = {'PASS', 'FAIL', 'BLOCKED', 'RUNNING'}
PROCESS_REASONS = {'spawn_failed', 'timeout', 'signal', 'output_limit'}
FAILURE_REASONS = PROCESS_REASONS | {'invalid_case_result', 'evidence_missing', 'graph_mismatch',
                                   'graph_checks_failed', 'case_failed', 'input_drift'}


def reject(code: str, details: dict, message: str | None = None) -> None:
    raise Problem(code, message or code.replace('_', ' ').capitalize(), details)


def damage(run_id: str, steps: list[str] | None = None) -> None:
    reject('evidence_mismatch', {'run_id': run_id, 'step_ids': steps or []})


def read_bytes(path: Path, bound: int) -> bytes:
    if not runner.no_symlinks(path):
        raise OSError('Symlink in evidence path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > bound:
            raise OSError('Nonregular or oversized retained file')
        raw = stream.read(bound + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > bound or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise OSError('Retained file changed during read')
    return raw


def read_json(path: Path, bound: int) -> Any:
    return loads(read_bytes(path, bound).decode('utf-8'))


def write_raw(path: Path, raw: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    runner.sync_directory(path.parent)


def file_record(path: Path, bound: int) -> tuple[dict, bytes]:
    raw = read_bytes(path, bound)
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, raw


def validate_record(record: Any, path: Path, bound: int) -> bytes:
    if (not isinstance(record, dict) or set(record) != RECORD_FIELDS or record['path'] != str(path)
            or type(record['bytes']) is not int or record['bytes'] < 0):
        raise ValueError('Invalid captured output record')
    actual, raw = file_record(path, bound)
    if actual != record:
        raise ValueError('Captured output bytes differ')
    return raw


def freeze_records(state: Path, frozen: Frozen, limits: dict) -> None:
    directory = state / 'frozen'
    runner.durable_directory(directory)
    records = []
    for relative, raw in frozen.records().items():
        path = directory / relative
        if not path.parent.exists():
            runner.durable_directory(path.parent)
        write_raw(path, raw)
        records.append({'path': relative, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    runner.atomic_json(directory / 'files.json', {'files': records}, limits['result_bytes'])


def load_frozen(state: Path, limits: dict) -> Frozen:
    directory = state / 'frozen'
    manifest = read_json(directory / 'files.json', limits['result_bytes'])
    if not isinstance(manifest, dict) or set(manifest) != {'files'} or not isinstance(manifest['files'], list):
        raise ValueError('Invalid frozen record membership')
    contents = {}
    for record in manifest['files']:
        if (not isinstance(record, dict) or set(record) != RECORD_FIELDS
                or not isinstance(record['path'], str) or Path(record['path']).is_absolute()
                or '..' in Path(record['path']).parts or record['path'] in contents):
            raise ValueError('Invalid frozen record path')
        relative = record['path']
        bound = limits['program_bytes'] if relative == 'program.json' or relative.startswith('graph-inputs/') else limits['result_bytes']
        raw = read_bytes(directory / relative, bound)
        if type(record['bytes']) is not int or record['bytes'] != len(raw) or record['sha256'] != hashlib.sha256(raw).hexdigest():
            raise ValueError('Frozen record changed')
        contents[relative] = raw
    basic = {'program.json', 'configuration.json', 'environment.json', 'inventories.json', 'graphs.json'}
    if not basic <= contents.keys():
        raise ValueError('Missing frozen records')
    graphs = loads(contents['graphs.json'].decode('utf-8'))
    if not isinstance(graphs, list):
        raise ValueError('Invalid frozen graphs')
    expected = basic | {'graph-inputs/' + g['graph_id'] + '.bin' for g in graphs if g['sha256'] is not None}
    if set(contents) != expected:
        raise ValueError('Unexpected frozen records')
    graph_bytes = {g['graph_id']: contents['graph-inputs/' + g['graph_id'] + '.bin'] for g in graphs if g['sha256'] is not None}
    for graph in graphs:
        if graph['sha256'] is not None and hashlib.sha256(graph_bytes[graph['graph_id']]).hexdigest() != graph['sha256']:
            raise ValueError('Frozen graph digest differs')
    return Frozen(contents['program.json'], loads(contents['configuration.json'].decode('utf-8')),
                  loads(contents['environment.json'].decode('utf-8')),
                  loads(contents['inventories.json'].decode('utf-8')), graphs, graph_bytes)


def observe_or_block(selection: Selection) -> tuple[Frozen, str | None]:
    try:
        return selection.observe(), None
    except Problem as exc:
        if exc.code == 'limit_exceeded':
            raise
        # An unavailable runtime observation is never converted to a digest of
        # nonexistent file bytes. The inventory explicitly records incompleteness.
    except (OSError, ValueError, RuntimeError):
        pass
    inventories = {}
    for key, descriptor in sorted(selection.descriptors.items()):
        try:
            inventories[key] = inventory(descriptor, selection.parsed.roots, selection.limits)
        except (OSError, Problem, ValueError, RuntimeError):
            inventories[key] = {'descriptor': descriptor, 'entries': [], 'observation_error': 'unavailable'}
    graph_bytes, graphs = {}, []
    for graph in sorted(selection.parsed.program['graphs'], key=lambda x: x['id']):
        try:
            raw = runner.read_input(selection.graph_paths[graph['id']], selection.parsed.roots, selection.limits['program_bytes'])
            graph_bytes[graph['id']] = raw
            digest = hashlib.sha256(raw).hexdigest()
        except (Problem, OSError):
            digest = None  # Incomplete BLOCKED observation; not a certificate.
        graphs.append({'graph_id': graph['id'], 'input_id': graph['input_id'], 'sha256': digest})
    return Frozen(selection.raw_program, selection.effective, environment(selection.parsed.profile),
                  inventories, graphs, graph_bytes), 'input_unavailable'


def check_input(inp: Any, operation: str, limit_bytes: int) -> None:
    fields = {'run_id'} if operation == 'inspect' else {'run_id', 'program_path', 'profile_id', 'step_limit'}
    if operation == 'resume':
        fields.add('expected_revision')
    if operation == 'validate':
        fields = {'program_path', 'profile_id'}
    exact(inp, fields, '/input')
    if operation != 'validate':
        valid_id(inp['run_id'], limit_bytes, '/input/run_id')
    if operation in ('run', 'resume'):
        if type(inp['step_limit']) is not int or inp['step_limit'] < 0:
            reject('invalid_packet', {'path': '/input/step_limit'})
    if operation == 'resume' and (type(inp['expected_revision']) is not int or inp['expected_revision'] < 1):
        reject('invalid_packet', {'path': '/input/expected_revision'})


@contextmanager
def locked(path: Path, run_id: str, timeout: int, new: bool) -> Iterator[None]:
    flags = os.O_NOFOLLOW | (os.O_RDWR | os.O_CREAT | os.O_EXCL if new else os.O_RDONLY)
    try:
        fd = os.open(path / '.lock', flags, 0o600)
    except OSError:
        damage(run_id)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            damage(run_id)
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    reject('store_busy', {'run_id': run_id})
                time.sleep(min(0.025, max(0, end - time.monotonic())))
        yield
    finally:
        os.close(fd)


class Journal:
    def __init__(self, root: Path, workspace_root: Path, run_id: str, limits: dict):
        self.state = root / run_id
        self.workspace = workspace_root / run_id
        self.run_id, self.limits = run_id, limits
        self.path = self.state / 'protocol.json'

    def context(self, frozen: Frozen, step: dict) -> tuple[dict, list[str], Path]:
        cfg = frozen.configuration
        directory = self.workspace / '.control' / step['id']
        ctxpath = directory / 'context.json'
        graph = next((g for g in frozen.graphs if g['graph_id'] == step['graph_id']), None)
        context = {'schema': 'stride-uat-case-input/v1', 'run_id': self.run_id,
                   'step_id': step['id'], 'application_id': step['application_id'],
                   'public_argv': cfg['applications'][step['application_id']]['public_argv'],
                   'workspace': str(self.workspace / step['id']),
                   'result_path': str(self.workspace / step['id'] / 'result.json'),
                   'graph': None if graph is None else {'id': graph['graph_id'], 'sha256': graph['sha256'],
                                                      'path': str(directory / 'graph.bin')}}
        argv = cfg['commands'][step['command_id']]['argv'] + [str(ctxpath) if x == '${context}' else x for x in step['arguments']]
        if len(argv) > self.limits['argv_items'] or any(len(x.encode('utf-8')) > self.limits['argument_bytes'] for x in argv):
            limit('argument_bytes')
        return context, argv, ctxpath

    def initial(self, selection: Selection, frozen: Frozen) -> dict:
        p = selection.parsed.program
        return {'schema': PROTOCOL, 'run_id': self.run_id, 'revision': 1,
                'program_id': p['program_id'], 'program_version': p['version'],
                'fingerprint': frozen.fingerprint, 'input_snapshot': frozen.snapshot,
                'profile_id': selection.parsed.profile_id, 'environment_observation': frozen.environment,
                'verdict': 'RUNNING', 'steps': [], 'remaining_step_ids': [s['id'] for s in p['steps']],
                'graph_results': [], 'stop_reason': 'step_limit', 'diagnostics': []}

    def graphs(self, journal: dict, frozen: Frozen, program: dict) -> list[dict]:
        result = []
        for graph in program['graphs']:
            binding = next(g for g in frozen.graphs if g['graph_id'] == graph['id'])
            passed = [s['step_id'] for s in journal['steps'] if s['state'] == 'PASS'
                      and s['step_id'] in graph['required_step_ids'] and s['graph'] is not None
                      and s['graph']['id'] == graph['id'] and s['graph']['sha256'] == binding['sha256']]
            result.append({'graph_id': graph['id'], 'graph_sha256': binding['sha256'],
                           'required_step_ids': graph['required_step_ids'], 'passed_step_ids': passed,
                           'verdict': journal['verdict']})
        return result

    def publish(self, journal: dict, frozen: Frozen, program: dict, *, first: bool = False) -> None:
        if not first:
            journal['revision'] += 1
        journal['graph_results'] = self.graphs(journal, frozen, program)
        runner.atomic_json(self.path, journal, self.limits['result_bytes'])

    def reserve_budget(self, selection: Selection, frozen: Frozen) -> None:
        for relative, raw in frozen.records().items():
            bound = self.limits['program_bytes'] if relative == 'program.json' or relative.startswith('graph-inputs/') else self.limits['result_bytes']
            if len(raw) > bound:
                limit('program_bytes' if bound == self.limits['program_bytes'] else 'result_bytes')
        maximum = self.initial(selection, frozen)
        for step in selection.parsed.program['steps']:
            context, argv, _ = self.context(frozen, step)
            if len(canonical(context)) > self.limits['result_bytes']:
                limit('result_bytes')
            spec = {'uid': selection.effective['isolation']['uid'], 'gid': selection.effective['isolation']['gid'],
                    'argv': argv, 'environment': selection.parsed.profile['environment']}
            if len(canonical(spec)) > self.limits['result_bytes']:
                limit('result_bytes')
            maximum['steps'].append(self.intent(step, argv, 'f'*64))
        # Result graph shape is also bounded at capture time; reserve fixed
        # scalars/output references before any effect. No raw output is embedded.
        size = len(canonical(maximum)) + len(maximum['steps']) * 2048
        if size > self.limits['result_bytes']:
            limit('result_bytes')

    def intent(self, step: dict, argv: list[str], context_sha: str) -> dict:
        return {'step_id': step['id'], 'state': 'RUNNING', 'attempt_id': uuid.uuid4().hex,
                'argv': argv, 'context_sha256': context_sha, 'exit_code': None, 'signal': None,
                'timed_out': False, 'duration_seconds': None, 'effects': 'unknown', 'reason': None,
                'outputs': {'result': None, 'stdout': None, 'stderr': None}, 'graph': None}

    def stream_path(self, step_id: str, channel: str) -> Path:
        return self.state / 'evidence' / (step_id + '.' + channel + '.bin')

    def capture_result(self, context: dict) -> tuple[dict | None, dict | None, str | None]:
        path = Path(context['result_path'])
        try:
            record, raw = file_record(path, self.limits['result_bytes'])
        except OSError:
            return None, None, 'evidence_missing'
        try:
            result = loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError):
            return record, None, 'invalid_case_result'
        if (not isinstance(result, dict) or set(result) != RESULT_FIELDS
                or result['schema'] != 'stride-uat-case-result/v1'
                or any(result[key] != context[key] for key in ('run_id', 'step_id', 'application_id'))
                or result['status'] not in ('PASS', 'FAIL', 'BLOCKED')
                or (result['reason'] is not None if result['status'] == 'PASS' else
                    not isinstance(result['reason'], str) or not result['reason'])):
            return record, None, 'invalid_case_result'
        return record, result, None

    def capture_graph(self, context: dict, result: dict) -> tuple[dict | None, str | None]:
        graph, expected = result['graph'], context['graph']
        if expected is None:
            return (None, None) if graph is None else (None, 'graph_mismatch')
        if (not isinstance(graph, dict) or set(graph) != {'id', 'sha256', 'checks'}
                or graph['id'] != expected['id'] or graph['sha256'] != expected['sha256']):
            return None, 'graph_mismatch'
        checks = graph['checks']
        if not isinstance(checks, list) or not checks or len(checks) > self.limits['steps']:
            return None, 'invalid_case_result'
        captured = {'id': graph['id'], 'sha256': graph['sha256'], 'checks': []}
        seen = set()
        for check in checks:
            if (not isinstance(check, dict) or set(check) != {'id', 'status', 'evidence_paths'}
                    or not isinstance(check['id'], str) or not check['id'] or check['id'] in seen
                    or check['status'] not in ('PASS', 'FAIL', 'BLOCKED')
                    or not isinstance(check['evidence_paths'], list) or not check['evidence_paths']):
                return None, 'invalid_case_result'
            seen.add(check['id'])
            paths = check['evidence_paths']
            if len(paths) > self.limits['steps'] or any(not isinstance(x, str) for x in paths) or len(set(paths)) != len(paths):
                return None, 'invalid_case_result'
            records = []
            for name in paths:
                rel = PurePosixPath(name)
                if not name or rel.is_absolute() or '..' in rel.parts or str(rel) != name or '\0' in name:
                    return None, 'evidence_missing'
                path = Path(context['workspace']) / name
                try:
                    record, _ = file_record(path, self.limits['result_bytes'])
                except (OSError, ValueError):
                    return None, 'evidence_missing'
                records.append(record)
            captured['checks'].append({'id': check['id'], 'status': check['status'], 'evidence_paths': records})
        if len(canonical(captured)) > self.limits['result_bytes'] // max(1, self.limits['steps']):
            return None, 'invalid_case_result'
        return captured, None

    def outcome(self, step: dict, context: dict, runner_reason: str | None) -> tuple[str, str | None, dict | None, dict | None]:
        output, result, invalid_result = self.capture_result(context)
        graph, invalid_graph = (None, None) if result is None else self.capture_graph(context, result)
        if runner_reason in PROCESS_REASONS:
            return 'FAIL', runner_reason, output, graph
        if invalid_result or invalid_graph:
            return 'FAIL', invalid_result or invalid_graph, output, graph
        assert result is not None
        if step['exit_code'] == 0 and result['status'] == 'PASS':
            if graph and any(check['status'] != 'PASS' for check in graph['checks']):
                return 'FAIL', 'graph_checks_failed', output, graph
            return 'PASS', None, output, graph
        if step['exit_code'] == 3 and result['status'] == 'BLOCKED':
            return 'BLOCKED', result['reason'], output, graph
        return 'FAIL', 'case_failed', output, graph

    def read(self) -> tuple[dict, Frozen, dict]:
        try:
            j = read_json(self.path, self.limits['result_bytes'])
            if not isinstance(j, dict):
                damage(self.run_id)
            if j.get('schema') != PROTOCOL:
                reject('store_incompatible', {'schema': j.get('schema') if isinstance(j.get('schema'), str) else None})
            if set(j) != JOURNAL_FIELDS or j['run_id'] != self.run_id or type(j['revision']) is not int or j['revision'] < 1:
                damage(self.run_id)
            frozen = load_frozen(self.state, self.limits)
            program = loads(frozen.program.decode('utf-8'))
            if (j['program_id'] != program['program_id'] or j['program_version'] != program['version']
                    or j['profile_id'] != frozen.configuration['profile_id']
                    or j['input_snapshot'] != frozen.snapshot or j['fingerprint'] != frozen.fingerprint
                    or set(j['input_snapshot']) != SNAPSHOT_FIELDS or set(j['environment_observation']) != ENVIRONMENT_FIELDS
                    or j['environment_observation'] != frozen.environment):
                damage(self.run_id)
            if (j['verdict'] not in VERDICTS or not isinstance(j['steps'], list)
                    or not isinstance(j['diagnostics'], list) or any(not isinstance(x, str) for x in j['diagnostics'])):
                damage(self.run_id)
            steps = j['steps']
            ids = [s['id'] for s in program['steps']]
            if [s['step_id'] for s in steps] != ids[:len(steps)] or j['remaining_step_ids'] != ids[len(steps):]:
                damage(self.run_id)
            seen = set()
            for index, step in enumerate(steps):
                sid = step['step_id']
                if (set(step) != STEP_FIELDS or not isinstance(step['attempt_id'], str) or not step['attempt_id']
                        or step['attempt_id'] in seen or step['state'] not in VERDICTS
                        or step['effects'] not in ('none', 'possible', 'unknown') or type(step['timed_out']) is not bool
                        or set(step['outputs']) != {'result', 'stdout', 'stderr'}
                        or any(step[k] is not None and type(step[k]) is not int for k in ('exit_code', 'signal'))):
                    damage(self.run_id, [sid])
                seen.add(step['attempt_id'])
                context, argv, ctxpath = self.context(frozen, program['steps'][index])
                ctxraw = read_bytes(ctxpath, self.limits['result_bytes'])
                if loads(ctxraw.decode('utf-8')) != context or step['argv'] != argv or hashlib.sha256(ctxraw).hexdigest() != step['context_sha256']:
                    damage(self.run_id, [sid])
                if context['graph'] is not None:
                    graphraw = read_bytes(Path(context['graph']['path']), self.limits['program_bytes'])
                    if graphraw != frozen.graph_bytes[context['graph']['id']]:
                        damage(self.run_id, [sid])
                launch = read_json(ctxpath.with_name('launch.json'), self.limits['result_bytes'])
                if launch != {'uid': frozen.configuration['isolation']['uid'], 'gid': frozen.configuration['isolation']['gid'],
                              'argv': argv, 'environment': frozen.configuration['profile']['environment']}:
                    damage(self.run_id, [sid])
                cwd = Path(context['workspace'])
                if not runner.no_symlinks(cwd) or not cwd.is_dir():
                    damage(self.run_id, [sid])
                if step['state'] == 'RUNNING':
                    if (index != len(steps)-1 or any(step[k] is not None for k in ('exit_code','signal','duration_seconds','reason','graph'))
                            or any(x is not None for x in step['outputs'].values()) or step['timed_out']):
                        damage(self.run_id, [sid])
                    continue
                if (type(step['duration_seconds']) not in (int, float) or not math.isfinite(step['duration_seconds'])
                        or step['duration_seconds'] < 0):
                    damage(self.run_id, [sid])
                for channel in ('stdout', 'stderr'):
                    validate_record(step['outputs'][channel], self.stream_path(sid, channel), self.limits[channel+'_bytes'])
                if step['outputs']['result'] is not None:
                    validate_record(step['outputs']['result'], Path(context['result_path']), self.limits['result_bytes'])
                if step['signal'] != (-step['exit_code'] if step['exit_code'] is not None and step['exit_code'] < 0 else None):
                    damage(self.run_id, [sid])
                reason = step['reason'] if step['reason'] in PROCESS_REASONS else None
                if step['timed_out'] != (reason == 'timeout') or (reason == 'signal' and step['signal'] is None):
                    damage(self.run_id, [sid])
                state, outcome_reason, output, graph = self.outcome(step, context, reason)
                if (state != step['state'] or outcome_reason != step['reason'] or output != step['outputs']['result']
                        or graph != step['graph'] or (step['state'] == 'PASS' and step['effects'] != 'possible')):
                    damage(self.run_id, [sid])
                if index != len(steps)-1 and state != 'PASS':
                    damage(self.run_id, [sid])
            all_pass = all(x['state'] == 'PASS' for x in steps)
            if j['verdict'] == 'PASS':
                if len(steps) != len(ids) or not all_pass or j['stop_reason'] is not None:
                    damage(self.run_id)
            elif j['verdict'] == 'RUNNING':
                expected = 'unknown_attempt' if steps and steps[-1]['state'] == 'RUNNING' else 'step_limit'
                if j['stop_reason'] != expected or (expected == 'step_limit' and (not all_pass or not j['remaining_step_ids'])):
                    damage(self.run_id)
            elif j['verdict'] == 'BLOCKED':
                if j['stop_reason'] is None or (steps and (steps[-1]['state'] != 'BLOCKED' or j['stop_reason'] != steps[-1]['reason'])):
                    damage(self.run_id)
            elif (not isinstance(j['stop_reason'], str) or not j['stop_reason']
                  or (j['stop_reason'] != 'input_drift' and (not steps or steps[-1]['state'] != 'FAIL'
                                                           or steps[-1]['reason'] != j['stop_reason']))):
                damage(self.run_id)
            if j['graph_results'] != self.graphs(j, frozen, program):
                damage(self.run_id)
            return j, frozen, program
        except Problem:
            raise
        except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError, RecursionError):
            damage(self.run_id)
        raise AssertionError('unreachable')

    def report(self, journal: dict, total: int, replayed: bool = False) -> tuple[dict, int]:
        return {'schema': SCHEMA, 'status': 'reported', 'run_id': self.run_id,
                'revision': journal['revision'], 'verdict': journal['verdict'], 'protocol_path': str(self.path),
                'fingerprint': journal['fingerprint'], 'completed_steps': sum(s['state'] != 'RUNNING' for s in journal['steps']),
                'total_steps': total, 'graph_results': journal['graph_results'], 'replayed': replayed}, {
                    'PASS': 0, 'FAIL': 1, 'BLOCKED': 3, 'RUNNING': 3}[journal['verdict']]

    def execute(self, selection: Selection, frozen: Frozen, journal: dict, step_limit: int,
                prerequisite: str | None = None) -> tuple[dict, int]:
        p = selection.parsed.program
        env = frozen.environment
        profile = selection.parsed.profile
        if prerequisite is None and (env['os_id'] not in profile['os_ids'] or env['os_version'] not in profile['os_versions']
                                     or env['python_version'] not in profile['python_versions']
                                     or (env['uid'] == 0 and not profile['allow_root'])):
            prerequisite = 'environment_mismatch'
        if prerequisite is not None:
            journal.update(verdict='BLOCKED', stop_reason=prerequisite, diagnostics=['No test command was started'])
            self.publish(journal, frozen, p)
            return self.report(journal, len(p['steps']))
        boundary = Boundary(selection, frozen, self.state, self.workspace)
        try:
            boundary.acquire()
        except (Unavailable, OSError, ValueError) as exc:
            boundary.close()
            journal.update(verdict='BLOCKED', stop_reason='isolation_unavailable', diagnostics=[str(exc)])
            self.publish(journal, frozen, p)
            return self.report(journal, len(p['steps']))
        try:
            deadline = time.monotonic() + self.limits['total_timeout_seconds']
            start = len(journal['steps'])
            chosen = p['steps'][start:] if step_limit == 0 else p['steps'][start:start+step_limit]
            for step in chosen:
                if frozen.changed(selection):
                    journal.update(verdict='FAIL', stop_reason='input_drift')
                    self.publish(journal, frozen, p)
                    break
                context, argv, ctxpath = self.context(frozen, step)
                launch = boundary.prepare_step(step['id'], context, argv, frozen.graph_bytes.get(step['graph_id']))
                intent = self.intent(step, argv, hashlib.sha256(read_bytes(ctxpath, self.limits['result_bytes'])).hexdigest())
                journal['steps'].append(intent)
                journal['remaining_step_ids'] = [s['id'] for s in p['steps'][len(journal['steps']):]]
                journal.update(verdict='RUNNING', stop_reason='unknown_attempt')
                self.publish(journal, frozen, p)  # Required durable boundary BEFORE Popen.
                record = {'argv': argv, 'cwd': context['workspace'], 'state': 'RUNNING', 'effects': 'unknown',
                          'reason': None, 'timed_out': False, 'exit_code': None, 'signal': None,
                          'stdout': {'path': str(self.stream_path(step['id'], 'stdout'))},
                          'stderr': {'path': str(self.stream_path(step['id'], 'stderr'))}}
                # An I/O exception deliberately leaves the durable unknown intent.
                # It is not replaced with a false no-effects result.
                runner.collect(record, SAFE_ENVIRONMENT, min(step['timeout_seconds'], deadline-time.monotonic()),
                               self.limits, launch_argv=launch)
                seal(Path(context['workspace']))
                for key in ('exit_code', 'signal', 'timed_out', 'duration_seconds', 'effects'):
                    intent[key] = record[key]
                state, reason, result_record, graph = self.outcome(intent, context, record['reason'])
                intent.update(state=state, reason=reason, graph=graph,
                              outputs={'result': result_record, 'stdout': record['stdout'], 'stderr': record['stderr']})
                if state == 'PASS':
                    journal.update(verdict='RUNNING' if journal['remaining_step_ids'] else 'PASS',
                                   stop_reason='step_limit' if journal['remaining_step_ids'] else None)
                else:
                    journal.update(verdict=state, stop_reason=reason)
                if frozen.changed(selection):
                    journal.update(verdict='FAIL', stop_reason='input_drift')
                self.publish(journal, frozen, p)
                if journal['verdict'] in ('FAIL', 'BLOCKED'):
                    break
            # Re-hash completed raw evidence and exact membership before returning
            # a new certificate. Historical inspect does not reobserve live inputs.
            checked, _, _ = self.read()
            return self.report(checked, len(p['steps']))
        finally:
            boundary.close()


def run(cfg: dict, state: Path, workspace: Path, roots: list[Path], base: Path,
        operation: str, inp: Any, config_path: Path) -> tuple[dict, int]:
    check_input(inp, operation, cfg['limits']['id_bytes'])
    if operation == 'inspect':
        selection = None
    else:
        selection = select(cfg, state, workspace, roots, base, inp)
        selection.config_path = config_path
        if operation == 'validate':
            p = selection.parsed
            return {'schema': SCHEMA, 'status': 'validated', 'program_id': p.program['program_id'],
                    'program_sha256': p.program_sha256, 'profile_id': p.profile_id,
                    'step_count': len(p.program['steps']), 'graph_count': len(p.program['graphs'])}, 0
    run_id = inp['run_id']
    journal = Journal(state, workspace, run_id, cfg['limits'])
    for key, resolved in (('state_root', state), ('workspace_root', workspace)):
        lexical = absolute(base, cfg[key], '/config/' + key)
        if not runner.no_symlinks(lexical) or not runner.no_symlinks(resolved):
            reject('workspace_conflict', {'path': str(lexical)})
    exists = os.path.lexists(journal.state)
    if exists and (journal.state.is_symlink() or not journal.state.is_dir()):
        reject('workspace_conflict', {'path': str(journal.state)})
    if not exists and operation in ('resume', 'inspect'):
        reject('not_found', {'run_id': run_id})
    if not exists and os.path.lexists(journal.workspace):
        reject('workspace_conflict', {'path': str(journal.workspace)})
    if exists:
        with locked(journal.state, run_id, cfg['limits']['lock_timeout_seconds'], False):
            old, frozen, program = journal.read()
            if operation == 'inspect':
                return journal.report(old, len(program['steps']))
            if operation == 'resume' and inp['expected_revision'] != old['revision']:
                reject('revision_conflict', {'expected': inp['expected_revision'], 'actual': old['revision']})
            assert selection is not None
            current, unavailable = observe_or_block(selection)
            if current.fingerprint != frozen.fingerprint:
                if operation == 'run':
                    reject('run_conflict', {'run_id': run_id})
                changed = [key for key in frozen.snapshot if frozen.snapshot[key] != current.snapshot[key]]
                reject('input_mismatch', {'run_id': run_id, 'changed': changed})
            if operation == 'run':
                return journal.report(old, len(program['steps']), True)
            for step in old['steps']:
                if step['state'] == 'RUNNING':
                    reject('unknown_attempt', {'run_id': run_id, 'step_id': step['step_id']})
            if old['verdict'] in ('FAIL', 'BLOCKED'):
                reject('run_not_resumable', {'run_id': run_id, 'verdict': old['verdict']})
            if old['verdict'] == 'PASS':
                return journal.report(old, len(program['steps']), True)
            return journal.execute(selection, frozen, old, inp['step_limit'], unavailable)
    assert selection is not None
    frozen, unavailable = observe_or_block(selection)
    journal.reserve_budget(selection, frozen)
    # Reserve only own output paths, after all rejection-only preparation checks.
    for root in roots:
        if runner.overlaps(state, root) or runner.overlaps(workspace, root):
            reject('workspace_conflict', {'path': str(state if runner.overlaps(state, root) else workspace)})
    for protected in selection.effective['isolation']['forbidden_roots']:
        if runner.overlaps(state, Path(protected).resolve()):
            reject('workspace_conflict', {'path': str(state)})
    if runner.overlaps(state, workspace):
        reject('workspace_conflict', {'path': str(workspace)})
    try:
        owned_directory(state, 0o700)
        if state.stat().st_mode & 0o077:
            reject('workspace_conflict', {'path': str(state)})
        runner.durable_directory(journal.state)
    except FileExistsError:
        # Another writer won reservation. Re-enter the ordinary existing-lock
        # path rather than taking over or replacing its files.
        return run(cfg, state, workspace, roots, base, operation, inp, config_path)
    except Unavailable:
        reject('workspace_conflict', {'path': str(state)})
    with locked(journal.state, run_id, cfg['limits']['lock_timeout_seconds'], True):
        freeze_records(journal.state, frozen, cfg['limits'])
        runner.durable_directory(journal.state / 'evidence')
        j = journal.initial(selection, frozen)
        journal.publish(j, frozen, selection.parsed.program, first=True)
        return journal.execute(selection, frozen, j, inp['step_limit'], unavailable)
