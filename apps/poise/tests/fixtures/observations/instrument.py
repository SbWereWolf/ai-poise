"""Declared external instrument: literal result, complete actual input capture."""
import json
from pathlib import Path
import sys

mode, audit = sys.argv[1:]
raw = sys.stdin.buffer.read()
Path(audit).write_bytes(raw)
value = json.loads(raw)
if set(value) != {'texts'} or len(value['texts']) != 2:
    raise SystemExit(9)
if mode == 'fail':
    raise SystemExit(7)
if mode == 'invalid':
    sys.stdout.write('{"counts":[-1,3]}\n')
else:
    sys.stdout.write('{"counts":[7,3]}\n')
