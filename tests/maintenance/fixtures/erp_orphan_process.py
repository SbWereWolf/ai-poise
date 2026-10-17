"""Finite ordinary intermediary failure, without detachment or group changes."""
from pathlib import Path
import subprocess
import sys
import time


def wait_for(path, seconds):
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    return path.exists()


mode, directory = sys.argv[1], Path(sys.argv[2])
if mode == 'root':
    middle = subprocess.Popen([
        sys.executable, '-B', __file__, 'middle', str(directory),
    ])
    result = middle.wait()
    (directory / 'middle-exit').write_text(str(result))
    if result != 1 or not (directory / 'leaf-started').exists():
        raise RuntimeError('Ordinary intermediary failure was not arranged')
    print('ordinary orphan leaf started', flush=True)
    print('ordinary root diagnostic', file=sys.stderr, flush=True)
    time.sleep(10)
elif mode == 'middle':
    leaf = subprocess.Popen([
        sys.executable, '-B', __file__, 'leaf', str(directory),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (directory / 'leaf-pid').write_text(str(leaf.pid))
    if not wait_for(directory / 'leaf-started', 2):
        raise RuntimeError('Fixture leaf did not start')
    raise SystemExit(1)
elif mode == 'leaf':
    (directory / 'leaf-started').write_text('ordinary orphan leaf started')
    if wait_for(directory / 'release-leaf', 8):
        (directory / 'leaf-effect').write_text('late orphan descendant effect')
else:
    raise ValueError(mode)
