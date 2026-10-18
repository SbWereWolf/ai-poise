"""Finite test-owned processes: startup, post-terminal effect and success controls."""
import subprocess
import sys
import time
from pathlib import Path


def wait_for(path, seconds):
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    return path.exists()


mode, directory = sys.argv[1], Path(sys.argv[2])
if mode == 'descendant':
    (directory / 'started').write_text('owned descendant started')
    if wait_for(directory / 'release', 8):
        (directory / 'effect').write_text('late descendant effect')
elif mode == 'parent':
    child = subprocess.Popen(
        [sys.executable, '-B', __file__, 'descendant', str(directory)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (directory / 'child-pid').write_text(str(child.pid))
    if not wait_for(directory / 'started', 5):
        raise RuntimeError('Fixture descendant did not start')
    print('owned descendant started', flush=True)
    print('owned parent diagnostic', file=sys.stderr, flush=True)
    time.sleep(10)
elif mode == 'success':
    (directory / 'effect').write_text('ordinary successful effect')
    print('ordinary stdout', flush=True)
    print('ordinary stderr', file=sys.stderr, flush=True)
elif mode == 'unrelated':
    time.sleep(10)
else:
    raise ValueError(mode)
