"""Independent subprocess producer for the configured consumer boundary."""
import base64
import json
from pathlib import Path
import sys


role, source, context = sys.argv[1:]
spec = json.loads(Path(source).read_text())
with Path(spec['log']).open('a') as stream:
    stream.write(json.dumps({'role': role, 'context': context,
                             'cwd': str(Path.cwd())}, ensure_ascii=False) + '\n')
reply = spec['responses'][role]
sys.stdout.buffer.write(base64.b64decode(reply['stdout']))
sys.stderr.buffer.write(base64.b64decode(reply['stderr']))
raise SystemExit(reply['exit_code'])
