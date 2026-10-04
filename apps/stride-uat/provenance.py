"""Selected UAT input inventories and canonical, retained qualification identity.

These are integrity records for administrator-declared inputs, not a dynamic
import detector or signatures against a supervisor rewriting all stored state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import sys
from typing import Any

from shell import Problem, loads
import runner


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def hashed(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def invalid(path: str, code: str = 'invalid_binding') -> None:
    raise Problem('program_invalid', 'Invalid programme or selected binding',
                  {'issues': [{'path': path, 'code': code}]})


def limit(name: str) -> None:
    raise Problem('limit_exceeded', 'Configured representation limit exceeded', {'limit': name})


def absolute(base: Path, value: Any, path: str) -> Path:
    if not isinstance(value, str) or not value or '\0' in value:
        invalid(path)
    try:
        value.encode('utf-8')
    except UnicodeError:
        invalid(path)
    p = Path(value)
    return Path(os.path.abspath(p if p.is_absolute() else base / p))


def normalize_problem(exc: Problem) -> Problem:
    """Preserve T029 diagnostics; qualify the shared parser's errors for T030."""
    path = exc.details.get('path', '')
    if exc.code == 'program_invalid' or (exc.code == 'invalid_packet' and path.startswith('/config')):
        return Problem('program_invalid', exc.message,
                       {'issues': [{'path': path, 'code': 'invalid_binding'}]})
    if exc.code == 'unsupported_schema':
        return Problem(exc.code, exc.message, {'schema': None})
    if exc.code == 'limit_exceeded':
        name = next((x for x in ('argv_items', 'argument_bytes', 'id_bytes', 'result_bytes',
                                'steps', 'program_bytes') if x in exc.message or x in path), 'program_bytes')
        return Problem(exc.code, exc.message, {'limit': name})
    return exc


def shape(value: Any, fields: set[str], path: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        invalid(path)


@dataclass
class Selection:
    parsed: runner.Prepared
    base: Path
    program_path: Path
    raw_program: bytes
    effective: dict
    descriptors: dict[str, dict]
    graph_paths: dict[str, Path]
    direct_paths: list[Path]
    config_path: Path | None = None

    @property
    def limits(self) -> dict:
        return self.parsed.config['limits']

    def observe(self) -> 'Frozen':
        # Re-read only the selected effective configuration, not unrelated maps.
        if self.config_path is not None:
            from shell import load_config
            cfg, state, workspace, roots = load_config(self.config_path)
            fresh = select(cfg, state, workspace, roots, self.base,
                           {'program_path': str(self.program_path), 'profile_id': self.parsed.profile_id})
            return fresh.observe()
        # Re-read programme bytes, not only its parsed identity/version label.
        raw = runner.read_input(self.program_path, self.parsed.roots, self.limits['program_bytes'])
        inventories = {key: inventory(item, self.parsed.roots, self.limits)
                       for key, item in sorted(self.descriptors.items())}
        env = environment(self.parsed.profile)
        graphs = [{'graph_id': graph['id'], 'input_id': graph['input_id'],
                   'sha256': inventories[graph['input_id']]['entries'][0]['sha256']}
                  for graph in sorted(self.parsed.program['graphs'], key=lambda x: x['id'])]
        graph_bytes = {g['id']: runner.read_input(self.graph_paths[g['id']], self.parsed.roots,
                                                self.limits['program_bytes']) for g in self.parsed.program['graphs']}
        return Frozen(raw, self.effective, env, inventories, graphs, graph_bytes)


def validate_durations(limits: dict) -> None:
    """Validate timers without selecting or reading live programme inputs."""
    for key in ('step_timeout_seconds', 'lock_timeout_seconds', 'total_timeout_seconds'):
        try:
            finite = math.isfinite(limits[key])
        except (OverflowError, TypeError):
            finite = False
        if not finite:
            invalid('/config/limits/' + key)


def select(cfg: dict, state: Path, workspace: Path, roots: list[Path], base: Path, inp: dict) -> Selection:
    try:
        p = runner.prepare(cfg, state, workspace, roots, base,
                           {k: inp[k] for k in ('program_path', 'profile_id')}, 'validate', qualifying=True)
    except Problem as exc:
        raise normalize_problem(exc) from exc
    limits = cfg['limits']
    validate_durations(limits)
    iso = cfg['isolations'][p.profile['isolation_id']]
    shape(iso, {'kind', 'launcher', 'uid', 'gid', 'test_roots', 'forbidden_roots', 'lease_root'}, '/config/isolations')
    try:
        runner.text(iso['kind'], '/config/isolations/kind', 'program_invalid')
    except Problem as exc:
        raise normalize_problem(exc) from exc
    for key in ('uid', 'gid'):
        if type(iso[key]) is not int or not 0 < iso[key] < 2**32-1:
            invalid('/config/isolations/' + key)
    for key in ('launcher', 'lease_root'):
        if not isinstance(iso[key], str) or not Path(iso[key]).is_absolute():
            invalid('/config/isolations/' + key)
        absolute(base, iso[key], '/config/isolations/' + key)
    for key in ('test_roots', 'forbidden_roots'):
        values = iso[key]
        if not isinstance(values, list) or not values:
            invalid('/config/isolations/' + key)
        for v in values:
            if not isinstance(v, str) or not Path(v).is_absolute():
                invalid('/config/isolations/' + key)
            absolute(base, v, '/config/isolations/' + key)
    for step in p.program['steps']:
        if step['graph_id'] is not None:
            try:
                runner.identifier(step['graph_id'], '/program/steps/graph_id', limits['id_bytes'], 'program_invalid')
            except Problem as exc:
                raise normalize_problem(exc) from exc
    step_ids = [s['id'] for s in p.program['steps']]
    graph_ids: set[str] = set()
    graph_inputs: set[str] = set()
    graph_paths: dict[str, Path] = {}
    needed = set(p.selected_inputs)
    if len(p.program['graphs']) > limits['steps']:
        limit('steps')
    for g in p.program['graphs']:
        shape(g, {'id', 'input_id', 'required_step_ids'}, '/program/graphs')
        try:
            for key in ('id', 'input_id'):
                runner.identifier(g[key], '/program/graphs/' + key, limits['id_bytes'], 'program_invalid')
            required = runner.ids(g['required_step_ids'], '/program/graphs/required_step_ids', limits, 'program_invalid')
        except Problem as exc:
            raise normalize_problem(exc) from exc
        if g['id'] in graph_ids or g['input_id'] in graph_inputs or any(x not in step_ids for x in required):
            invalid('/program/graphs')
        # Membership is reciprocal; unlisted or multiply attributed steps fail.
        actual = [s['id'] for s in p.program['steps'] if s['graph_id'] == g['id']]
        if set(actual) != set(required):
            invalid('/program/graphs/required_step_ids')
        graph_ids.add(g['id']); graph_inputs.add(g['input_id']); needed.add(g['input_id'])
    if any(s['graph_id'] is not None and s['graph_id'] not in graph_ids for s in p.program['steps']):
        invalid('/program/steps/graph_id')
    if len(needed) > limits['steps']:
        limit('steps')
    descriptors: dict[str, dict] = {}
    for key in sorted(needed):
        if key not in cfg['inputs']:
            invalid('/config/inputs/' + key)
        item = cfg['inputs'][key]
        if not isinstance(item, dict) or item.get('kind') not in ('file', 'tree', 'delivery'):
            invalid('/config/inputs/' + key)
        kind = item['kind']
        shape(item, {'kind', 'path'} | ({'excludes'} if kind != 'file' else set()), '/config/inputs/' + key)
        path = absolute(base, item['path'], '/config/inputs/' + key + '/path')
        try:
            if not any(path.resolve().is_relative_to(r.resolve()) for r in roots):
                invalid('/config/inputs/' + key + '/path', 'outside_input_roots')
        except (OSError, RuntimeError):
            invalid('/config/inputs/' + key + '/path')
        excludes = item.get('excludes', [])
        if not isinstance(excludes, list) or any(not isinstance(x, str) for x in excludes):
            invalid('/config/inputs/' + key + '/excludes')
        for x in excludes:
            try:
                runner.text(x, '/config/inputs/' + key + '/excludes', 'program_invalid')
            except Problem as exc:
                raise normalize_problem(exc) from exc
            px = PurePosixPath(x)
            if not x or px.is_absolute() or '..' in px.parts or '.' == x or str(px) != x or '\\' in x or '\0' in x:
                invalid('/config/inputs/' + key + '/excludes')
        if len(set(excludes)) != len(excludes):
            invalid('/config/inputs/' + key + '/excludes')
        descriptors[key] = {'kind': kind, 'path': str(path), **({'excludes': sorted(excludes)} if kind != 'file' else {})}
    for g in p.program['graphs']:
        d = descriptors[g['input_id']]
        if d['kind'] != 'file':
            invalid('/program/graphs/input_id')
        graph_paths[g['id']] = Path(d['path'])
    commands = {s['command_id']: cfg['commands'][s['command_id']] for s in p.program['steps']}
    apps = {a: cfg['applications'][a] for a in p.program['application_ids']}
    # Absolute argv paths are an explicit declaration, not an import analysis.
    direct = sorted({Path(a) for binding in [*commands.values(), *apps.values()]
                     for a in binding.get('argv', binding.get('public_argv', [])) if Path(a).is_absolute()}, key=str)
    def covered(path: Path) -> bool:
        for d in descriptors.values():
            root = Path(d['path'])
            if d['kind'] == 'file' and path == root:
                return True
            if d['kind'] != 'file' and path.is_relative_to(root):
                rel = path.relative_to(root).as_posix()
                if not any(rel == x or rel.startswith(x + '/') for x in d['excludes']):
                    return True
        return False
    if not all(covered(path) for path in direct):
        invalid('/config/commands', 'undeclared_direct_dependency')
    effective = {'schema': cfg['schema'], 'state_root': str(state), 'workspace_root': str(workspace),
                 'input_roots': sorted(str(x) for x in roots), 'limits': limits,
                 'profile_id': p.profile_id, 'profile': p.profile, 'commands': commands,
                 'applications': apps, 'inputs': descriptors, 'isolation': iso}
    program_path = absolute(base, inp['program_path'], '/input/program_path')
    return Selection(p, base, program_path, program_path.read_bytes(), effective, descriptors, graph_paths, direct)


def environment(profile: dict) -> dict:
    try:
        observed = platform.freedesktop_os_release()
    except OSError:
        observed = {}
    return {'os_id': observed.get('ID', ''), 'os_version': observed.get('VERSION_ID', ''),
            'python_version': [sys.version_info.major, sys.version_info.minor],
            'uid': os.geteuid() if hasattr(os, 'geteuid') else None,
            'configured_environment_sha256': hashed(profile['environment'])}


def inventory(descriptor: dict, roots: list[Path], limits: dict) -> dict:
    """Record complete sorted lexical membership, including in-tree symlinks.

    Reads are bounded and checked for concurrent changes. Directory links are
    walked explicitly, with physical ancestry/cycle checks, never silently skipped.
    """
    path = Path(descriptor['path'])
    entries: list[dict] = []
    byte_count = 0
    excludes = descriptor.get('excludes', [])
    root = path.resolve(strict=True)
    if not any(root.is_relative_to(r) for r in roots):
        raise OSError('Selected input is outside declared roots')
    tree = descriptor['kind'] != 'file'
    if tree and not root.is_dir():
        raise OSError('Selected tree is not a directory')
    if not tree and not root.is_file():
        raise OSError('Selected file is not regular')
    def walk(current: Path, relative: str, ancestors: set[Path]) -> None:
        nonlocal byte_count
        if relative != '.' and any(relative == x or relative.startswith(x + '/') for x in excludes):
            return
        before = current.lstat()
        physical = current.resolve(strict=True)
        if not any(physical.is_relative_to(r) for r in roots) or (tree and not physical.is_relative_to(root)):
            raise OSError('Escaping selected symlink')
        record = {'path': relative, 'mode': stat.S_IMODE(before.st_mode)}
        linked = stat.S_ISLNK(before.st_mode)
        if linked:
            record['target'] = os.readlink(current)
            record['target_mode'] = stat.S_IMODE(physical.stat().st_mode)
        actual = physical.stat()
        if stat.S_ISDIR(actual.st_mode):
            if physical in ancestors:
                raise OSError('Cyclic selected tree')
            record['type'] = 'symlink-directory' if linked else 'directory'
            entries.append(record)
            members = sorted(current.iterdir(), key=lambda p: p.name)
            for child in members:
                walk(child, child.name if relative == '.' else relative + '/' + child.name, ancestors | {physical})
            if [x.name for x in members] != sorted(x.name for x in current.iterdir()):
                raise OSError('Selected directory changed while observed')
        elif stat.S_ISREG(actual.st_mode):
            if actual.st_size > limits['program_bytes'] - byte_count:
                limit('program_bytes')
            h = hashlib.sha256()
            fd = os.open(physical, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                f_before = os.fstat(stream.fileno())
                if not stat.S_ISREG(f_before.st_mode):
                    raise OSError('Selected file changed type')
                size = 0
                while block := stream.read(min(65536, limits['program_bytes'] - byte_count - size + 1)):
                    size += len(block)
                    if size + byte_count > limits['program_bytes']:
                        limit('program_bytes')
                    h.update(block)
                f_after = os.fstat(stream.fileno())
            if (f_before.st_ino, f_before.st_size, f_before.st_mtime_ns, f_before.st_mode) != (f_after.st_ino, f_after.st_size, f_after.st_mtime_ns, f_after.st_mode):
                raise OSError('Selected file changed while observed')
            record.update(type='symlink-file' if linked else 'file', bytes=size, sha256=h.hexdigest())
            byte_count += size
            entries.append(record)
        else:
            raise OSError('Selected input is not a regular file or directory')
        after = current.lstat()
        if (before.st_ino, before.st_mode, before.st_mtime_ns) != (after.st_ino, after.st_mode, after.st_mtime_ns):
            raise OSError('Selected input changed while observed')
        # The serialized inventory is also bounded independently of file bytes.
        if len(canonical(entries)) > limits['result_bytes']:
            limit('result_bytes')
    walk(path, '.', set())
    return {'descriptor': descriptor, 'entries': sorted(entries, key=lambda x: x['path'])}


@dataclass
class Frozen:
    program: bytes
    configuration: dict
    environment: dict
    inventories: dict[str, dict]
    graphs: list[dict]
    graph_bytes: dict[str, bytes]

    @property
    def snapshot(self) -> dict:
        return {'program_sha256': hashlib.sha256(self.program).hexdigest(),
                'config_sha256': hashed(self.configuration), 'environment_sha256': hashed(self.environment),
                'isolation_id': self.configuration['profile']['isolation_id'],
                'inputs': [{'input_id': key, 'kind': value['descriptor']['kind'], 'inventory_sha256': hashed(value)}
                           for key, value in sorted(self.inventories.items())], 'graphs': self.graphs}

    @property
    def fingerprint(self) -> str:
        return hashed(self.snapshot)

    def records(self) -> dict[str, bytes]:
        return {'program.json': self.program, 'configuration.json': canonical(self.configuration),
                'environment.json': canonical(self.environment),
                'inventories.json': canonical(self.inventories), 'graphs.json': canonical(self.graphs),
                **{'graph-inputs/' + key + '.bin': raw for key, raw in self.graph_bytes.items()}}

    def changed(self, selection: Selection) -> bool:
        try:
            return selection.observe().fingerprint != self.fingerprint
        except (OSError, Problem, ValueError, RuntimeError):
            return True
