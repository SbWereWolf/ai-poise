"""Minimal post-setpriv entrypoint and read-only DAC capability observation.

This process starts in a fixed, non-user-controlled environment. Only execve,
AFTER privilege reduction, installs the selected application's environment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys


def replacement_allowed(path: Path) -> bool:
    """Whether this identity may replace any existing ancestor in the path.

    A writable sticky directory does not let a non-owner replace a root-owned
    child. We inspect metadata and access, never try a write to protected input.
    """
    for entry in (path, *path.parents):
        parent = entry.parent
        if entry == parent:
            continue
        if not os.access(parent, os.W_OK | os.X_OK):
            continue
        parent_stat = parent.stat()
        try:
            entry_stat = entry.lstat()
        except FileNotFoundError:
            return True
        if (not parent_stat.st_mode & stat.S_ISVTX
                or parent_stat.st_uid == os.geteuid() or entry_stat.st_uid == os.geteuid()):
            return True
    return False


def probe(spec: dict) -> dict:
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    paths = {}
    for name in spec['paths']:
        path = Path(name)
        try:
            replace = replacement_allowed(path)
        except PermissionError:
            # An inaccessible ancestor prevents replacement of descendants.
            replace = False
        paths[name] = {'read': os.access(path, os.R_OK), 'write': os.access(path, os.W_OK),
                       'search': os.access(path, os.X_OK), 'replace': replace}
    return {'uid': os.getuid(), 'euid': os.geteuid(), 'gid': os.getgid(), 'egid': os.getegid(),
            'groups': os.getgroups(), 'no_new_privs': int(status['NoNewPrivs'].strip()),
            'caps': {key: status[key].strip() for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')},
            'access': paths}


def main() -> int:
    # This trusted specification is outside the writable step, root-owned and
    # immutable to the dedicated identity. Do not import application modules.
    operation, name = sys.argv[1:]
    with open(name, encoding='utf-8') as source:
        spec = json.load(source)
    if operation == 'probe':
        sys.stdout.write(json.dumps(probe(spec), ensure_ascii=False, separators=(',', ':')) + '\n')
        return 0
    if operation != 'exec' or os.geteuid() != spec['uid'] or os.getegid() != spec['gid']:
        return 126
    os.execve(spec['argv'][0], spec['argv'], spec['environment'])
    return 127


if __name__ == '__main__':
    raise SystemExit(main())
