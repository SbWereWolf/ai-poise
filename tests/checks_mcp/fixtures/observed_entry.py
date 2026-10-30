"""Forward existing boundaries unchanged, holding a finite test-owned schedule."""
import json
import os
from pathlib import Path
import sys
import time

root = Path(sys.argv[1])
mode = sys.argv[2]
sys.argv = [sys.argv[0], *sys.argv[3:]]


def barrier(boundary):
    with (root / 'observer.jsonl').open('a') as stream:
        stream.write(json.dumps({'boundary': boundary, 'pid': os.getpid()}) + '\n')
    deadline = time.monotonic() + 20
    while not (root / 'observer.release').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('F13 test did not release its reached boundary')
        time.sleep(0.01)


if mode == 'before-spawn':
    from poise.execution import RegisteredCheckRunner
    original = RegisteredCheckRunner.run

    def observed(self, *args, **kwargs):
        barrier('runner-before-spawn')
        return original(self, *args, **kwargs)

    RegisteredCheckRunner.run = observed
elif mode == 'after-first':
    from poise.application.project_checks import ProjectChecks
    original = ProjectChecks.execute_step
    count = 0

    def observed(self, *args, **kwargs):
        global count
        value = original(self, *args, **kwargs)
        count += 1
        if count == 1:
            barrier('after-first-owned-step')
        return value

    ProjectChecks.execute_step = observed
else:
    raise ValueError('Unknown test observer mode')

from poise.__main__ import main
raise SystemExit(main())
