"""Selected test interpreter: records the legacy command boundary, never dispatches.

Fixed mode supplies literal child outcomes. Delegate mode runs the real Poise
transport with exactly the received arguments/input/environment/cwd.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time


options = json.loads(Path(__file__).with_suffix('.json').read_text())
arguments = sys.argv[1:]
module = arguments.index('-m') if '-m' in arguments else -1
if module < 0 or arguments[module + 1:module + 3] != ['poise', 'hook']:
    # Only legacy member callbacks are probes; a new proxy verb and any other
    # configured command execute their actual source through the selected Python.
    os.execv(options['python'], [options['python'], *arguments])
raw = sys.stdin.buffer.read()
folder = Path(options['trace'])
folder.mkdir(parents=True, exist_ok=True)
with (folder / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps({'id': options['id'], 'argv': sys.argv[1:],
        'stdin': base64.b64encode(raw).decode(), 'cwd': os.getcwd(),
        'marker': os.environ.get('INITIAL_PROXY_MARKER')}) + '\n')
if options['mode'] == 'delegate':
    result = subprocess.run([options['python'], *sys.argv[1:]], input=raw)
    raise SystemExit(result.returncode)
if options['mode'] == 'wait':
    time.sleep(30)  # The product's small fixture deadline must terminate this child.
sys.stdout.buffer.write(base64.b64decode(options['stdout']))
sys.stderr.buffer.write(base64.b64decode(options['stderr']))
raise SystemExit(options['exit_code'])
