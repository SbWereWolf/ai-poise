"""Disposable task-only generator with an observable invocation counter."""
from pathlib import Path
import sys

root, counter = map(Path, sys.argv[1:])
target = root / 'generated' / 'ready'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(b'Regenerated task component\n')
with counter.open('a') as output:
    output.write('called\n')
