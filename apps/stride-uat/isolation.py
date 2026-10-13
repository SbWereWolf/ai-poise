"""Explicit linux-setpriv/v1 execution boundary for trusted UAT adapters.

DAC denial is checked under the actual reduced identity. This is neither a
filesystem namespace nor a hostile-code/network sandbox. No protected production
or selected input is chmod'ed, chown'ed, repaired, or read as database contents.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from typing import Iterator

from shell import loads
from provenance import Frozen, Selection
from runner import atomic_json, durable_directory, no_symlinks, overlaps, sync_directory

SAFE_ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
CHILD = Path(__file__).with_name('isolated_child.py').absolute()


class Unavailable(Exception):
    """A deployment prerequisite was not established; no case may be started."""


def active_identity(uid: int) -> list[int]:
    found = []
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            fields = dict(line.split(':', 1) for line in (item / 'status').read_text().splitlines() if ':' in line)
            if uid in [int(x) for x in fields.get('Uid', '').split()] and not fields.get('State', '').strip().startswith('Z'):
                found.append(int(item.name))
        except FileNotFoundError:
            continue  # Process exited between enumeration and status read.
        except (PermissionError, ValueError) as exc:
            raise Unavailable('Process identity observation unavailable') from exc
    return found


def owned_directory(path: Path, mode: int) -> None:
    """Create only supervisor-owned directories; never change preexisting modes."""
    if not no_symlinks(path):
        raise Unavailable('Symlink in owned resource path')
    if path.exists():
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise Unavailable('Foreign or writable resource directory')
        return
    if not path.parent.exists():
        owned_directory(path.parent, 0o711)
    path.mkdir(mode=mode)
    sync_directory(path.parent)


def immutable_json(path: Path, value: dict, bound: int) -> None:
    atomic_json(path, value, bound)
    path.chmod(0o444)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Boundary:
    def __init__(self, selected: Selection, frozen: Frozen, state: Path, workspace: Path):
        self.selected, self.frozen = selected, frozen
        self.state, self.workspace = state, workspace
        self.spec = selected.effective['isolation']
        self.control = workspace / '.control'
        self.lease_fd: int | None = None

    def launcher(self, operation: str, specification: Path) -> list[str]:
        s = self.spec
        return [s['launcher'], '--reuid', str(s['uid']), '--regid', str(s['gid']),
                '--clear-groups', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all',
                '--no-new-privs', sys.executable, '-I', str(CHILD), operation, str(specification)]

    def check_roots(self) -> None:
        s = self.spec
        if (sys.platform != 'linux' or os.geteuid() != 0 or s['kind'] != 'linux-setpriv/v1'
                or s['uid'] == os.geteuid() or s['gid'] == os.getegid()):
            raise Unavailable('Unsupported privilege-reduction deployment')
        launcher = Path(s['launcher'])
        if not launcher.is_file() or not os.access(launcher, os.X_OK):
            raise Unavailable('Configured launcher unavailable')
        test = [Path(x).resolve() for x in s['test_roots']]
        forbidden = [Path(x).resolve() for x in s['forbidden_roots']]
        resources = [self.selected.parsed.state, self.selected.parsed.workspace, Path(s['lease_root'])]
        for path in resources:
            if not no_symlinks(path) or not any(path.resolve().is_relative_to(r) for r in test):
                raise Unavailable('Owned resource outside explicit test roots')
        for i, path in enumerate(resources):
            if any(overlaps(path.resolve(), other.resolve()) for other in resources[i+1:] + forbidden + self.selected.parsed.roots):
                raise Unavailable('Owned and protected resource roots overlap')
        if any(not p.exists() or p.is_symlink() for p in forbidden):
            raise Unavailable('Protected root cannot be observed')
        # The exact launcher must be covered by the frozen declared file/tree.
        covered = False
        for inv in self.frozen.inventories.values():
            root = Path(inv['descriptor']['path'])
            for entry in inv['entries']:
                path = root if entry['path'] == '.' else root / entry['path']
                if path == launcher and entry.get('sha256'):
                    covered = True
        if not covered:
            raise Unavailable('Launcher is not a frozen declared input')

    def acquire(self) -> None:
        self.check_roots()
        lease = Path(self.spec['lease_root'])
        owned_directory(lease, 0o700)
        fd = os.open(lease / ('uid-' + str(self.spec['uid']) + '.lock'),
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.lease_fd = fd
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise Unavailable('Foreign identity lease')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Unavailable('Dedicated identity is leased') from exc
        if active_identity(self.spec['uid']):
            raise Unavailable('Dedicated identity already has an active process')
        owned_directory(self.selected.parsed.workspace, 0o711)
        owned_directory(self.workspace, 0o711)
        owned_directory(self.control, 0o755)
        self.prove()

    def prove(self) -> None:
        readonly = {str(self.selected.parsed.state), str(self.workspace), str(self.control)}
        readable = {str(x) for x in self.selected.direct_paths}
        searchable = {str(self.workspace), str(self.control)}
        for inv in self.frozen.inventories.values():
            root = Path(inv['descriptor']['path'])
            for entry in inv['entries']:
                path = root if entry['path'] == '.' else root / entry['path']
                readonly.add(str(path))
                readable.add(str(path))
                if entry['type'].endswith('directory'):
                    searchable.add(str(path))
        forbidden = set(self.spec['forbidden_roots'])
        names = sorted(readonly | readable | searchable | forbidden)
        path = self.control / 'probe-input.json'
        immutable_json(path, {'paths': names}, self.selected.limits['result_bytes'])
        try:
            result = subprocess.run(self.launcher('probe', path), env=SAFE_ENVIRONMENT,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=self.workspace, timeout=min(10, self.selected.limits['step_timeout_seconds']),
                                    check=False)
            observation = loads(result.stdout.decode('utf-8')) if len(result.stdout) <= self.selected.limits['result_bytes'] else None
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
            raise Unavailable('Reduced-identity proof did not complete') from exc
        # Retain actual observations without the configured application secrets.
        atomic_json(self.state / 'isolation-observation.json',
                    {'launcher_argv': self.launcher('probe', path), 'exit_code': result.returncode,
                     'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex()},
                    self.selected.limits['result_bytes'])
        if result.returncode or result.stderr or not isinstance(observation, dict):
            raise Unavailable('Reduced-identity proof failed')
        if (observation.get('uid') != self.spec['uid'] or observation.get('euid') != self.spec['uid']
                or observation.get('gid') != self.spec['gid'] or observation.get('egid') != self.spec['gid']
                or observation.get('groups') != [] or observation.get('no_new_privs') != 1
                or set(observation.get('caps', {})) != {'CapInh','CapPrm','CapEff','CapBnd','CapAmb'}
                or any(int(v, 16) for v in observation['caps'].values())):
            raise Unavailable('Kernel identity or capabilities differ')
        access = observation['access']
        if set(access) != set(names):
            raise Unavailable('Incomplete resource access proof')
        if any(any(access[x][k] for k in ('read','write','search','replace')) for x in forbidden):
            raise Unavailable('Protected resource is accessible or replaceable')
        if any(access[x]['write'] or access[x]['replace'] for x in readonly):
            raise Unavailable('Selected input or immutable state is writable')
        if any(not access[x]['read'] for x in readable) or any(not access[x]['search'] for x in searchable):
            raise Unavailable('Selected runtime input is inaccessible')

    def prepare_step(self, step_id: str, context: dict, argv: list[str], graph_raw: bytes | None) -> list[str]:
        cwd = self.workspace / step_id
        if os.path.lexists(cwd):
            raise Unavailable('Step workspace already exists')
        directory = self.control / step_id
        if os.path.lexists(directory):
            raise Unavailable('Step control directory already exists')
        directory.mkdir(mode=0o755)
        sync_directory(self.control)
        immutable_json(directory / 'context.json', context, self.selected.limits['result_bytes'])
        if graph_raw is not None:
            graph = directory / 'graph.bin'
            fd = os.open(graph, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            with os.fdopen(fd, 'wb') as out:
                out.write(graph_raw); out.flush(); os.fsync(out.fileno())
            sync_directory(directory)
        spec = directory / 'launch.json'
        immutable_json(spec, {'uid': self.spec['uid'], 'gid': self.spec['gid'], 'argv': argv,
                              'environment': self.selected.parsed.profile['environment']},
                       self.selected.limits['result_bytes'])
        cwd.mkdir(mode=0o700)
        os.chown(cwd, self.spec['uid'], self.spec['gid'])
        sync_directory(self.workspace)
        return self.launcher('exec', spec)

    def close(self) -> None:
        if self.lease_fd is not None:
            os.close(self.lease_fd)
            self.lease_fd = None

    def __enter__(self) -> 'Boundary':
        try:
            self.acquire()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def seal(path: Path) -> None:
    """Revoke dedicated-UID access to completed own evidence, without following links."""
    for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in files + dirs:
            child = Path(root) / name
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                os.chown(child, os.geteuid(), os.getegid(), follow_symlinks=False)
                continue
            if stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode):
                os.chown(child, os.geteuid(), os.getegid())
                child.chmod(0o700 if stat.S_ISDIR(info.st_mode) else 0o600)
        os.chown(root, os.geteuid(), os.getegid())
        Path(root).chmod(0o700)
