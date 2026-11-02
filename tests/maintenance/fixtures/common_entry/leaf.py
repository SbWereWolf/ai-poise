#!/usr/bin/python3
"""Independent external replies; no Poise/SDK imports or result interpretation."""
import json
import os
from pathlib import Path
import sys

if sys.argv[1:2] == ['--marker']:
    Path(sys.argv[2]).write_bytes(b'later-command\n')
else:
    sample = json.loads(os.environ['COMMON_LEAF_CASE'])
    sys.stdout.buffer.write(sample['stdout'].encode())
    sys.stderr.buffer.write(Path(os.environ['COMMON_LEAF_STDERR']).read_bytes())
    raise SystemExit(sample['exit_code'])
