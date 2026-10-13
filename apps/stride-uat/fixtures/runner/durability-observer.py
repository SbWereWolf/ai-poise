"""Test-owned forwarding observer; optional single fsync fault before effects.

Loaded as sitecustomize only by the real UAT CLI, never by -I owner commands.
No production import, generated oracle, replaced success, or fake process launch.
"""
import errno
import json
import os
import sys
from pathlib import Path

_trace = os.environ['POISE_DURABILITY_TRACE']
_fail = os.environ.get('POISE_DURABILITY_FAIL_PARENT')
_fail_occurrence = int(os.environ.get('POISE_DURABILITY_FAIL_OCCURRENCE', '1'))
_seen = 0
_original_fsync = os.fsync
_original_mkdir = os.mkdir


def _record(event, **values):
    with open(_trace, 'a', encoding='utf-8') as output:
        output.write(json.dumps({'event': event, **values}, ensure_ascii=True) + '\n')


def _fsync(fd):
    global _seen
    path = os.readlink('/proc/self/fd/' + str(fd))
    if path == _fail:
        _seen += 1
        if _seen == _fail_occurrence:
            _record('fsync_failed', path=path)
            raise OSError(errno.EIO, 'test-injected directory sync failure')
    result = _original_fsync(fd)
    _record('fsync_completed', path=path)
    return result


def _mkdir(path, mode=0o777, *, dir_fd=None):
    result = _original_mkdir(path, mode, dir_fd=dir_fd)
    _record('mkdir_completed', path=str(Path(path).absolute()))
    return result


def _audit(event, args):
    if event == 'subprocess.Popen':
        journal_path = Path(os.environ['POISE_DURABILITY_JOURNAL'])
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        _record('spawn_attempted', argv=args[1], cwd=args[2], journal=journal)


os.fsync = _fsync
os.mkdir = _mkdir
sys.addaudithook(_audit)
