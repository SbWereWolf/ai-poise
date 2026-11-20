"""Native foreign callback probe. No Poise producer or proxy implementation."""
import base64
import json
import os
from pathlib import Path
import sys
import time

options = json.loads(Path(sys.argv[1]).read_text())
root = Path(options['trace'])
root.mkdir(parents=True, exist_ok=True)
raw = sys.stdin.buffer.read()
(root / 'metric-input.json').write_text(json.dumps({
    'stdin': base64.b64encode(raw).decode(), 'argv': sys.argv[1:],
    'cwd': os.getcwd(), 'marker': os.environ.get('INITIAL_PROXY_MARKER'),
    'pid': os.getpid()}))
(root / 'metric-started').touch()
while options.get('barrier') and not (root / 'metric-release').exists():
    time.sleep(.01)
sys.stdout.buffer.write(base64.b64decode(options['stdout']))
sys.stderr.buffer.write(base64.b64decode(options['stderr']))
sys.stdout.buffer.flush()
sys.stderr.buffer.flush()
(root / 'metric-finished').touch()
raise SystemExit(options['exit_code'])
