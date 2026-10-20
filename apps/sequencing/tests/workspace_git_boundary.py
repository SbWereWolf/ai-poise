"""Test-owned transparent real-Git boundary; never imported by the application."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def wait_for(path):
    while not Path(path).exists():
        time.sleep(0.01)


def main():
    config = json.loads(Path(os.environ['STRIDE_TEST_GIT_BOUNDARY']).read_text())
    if sys.argv[1:] == ['--smudge']:
        Path(config['filter_entered']).touch()
        wait_for(config['filter_release'])
        sys.stdout.buffer.write(sys.stdin.buffer.read())
        return 0
    arguments = sys.argv[1:]
    with Path(config['trace']).open('a') as stream:
        stream.write(json.dumps(arguments) + '\n')
    is_add = any(arguments[index:index + 2] == ['worktree', 'add']
                 for index in range(len(arguments) - 1))
    inject = is_add and not Path(config['used']).exists() and config['mode'] != 'none'
    if inject:
        Path(config['used']).touch()
    if inject and config['mode'] == 'before':
        Path(config['entered']).touch()
        wait_for(config['release'])
    parent = Path(config['target']).parent
    previous_mode = None
    if inject and config['mode'] == 'permission':
        target = Path(config['target'])
        # The isolated fixture owns this initially empty path and its parent.
        if target.exists():
            target.rmdir()
        previous_mode = parent.stat().st_mode & 0o777
        parent.chmod(0o555)
    try:
        process = subprocess.run([config['real_git'], *arguments], capture_output=True)
    finally:
        if previous_mode is not None:
            parent.chmod(previous_mode)
    sys.stdout.buffer.write(process.stdout)
    sys.stderr.buffer.write(process.stderr)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.flush()
    if inject and config['mode'] == 'after' and process.returncode == 0:
        Path(config['entered']).touch()
        wait_for(config['release'])
    return process.returncode


if __name__ == '__main__':
    raise SystemExit(main())
