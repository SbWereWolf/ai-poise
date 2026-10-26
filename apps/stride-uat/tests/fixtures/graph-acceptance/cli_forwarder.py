"""Sensitivity control: forward real CLI calls, alter one public state value."""
import json
from pathlib import Path
import subprocess
import sys

payload = sys.stdin.buffer.read()
command = [sys.executable, '-B', sys.argv[1], *sys.argv[2:]]
result = subprocess.run(command, input=payload, capture_output=True, check=False)
original = result.stdout.decode('utf-8')
body = json.loads(original)
packet = json.loads(payload)
changed = False
if packet['operation'] == 'query':
    for query, item in zip(packet['input']['queries'], body.get('results', [])):
        if query['kind'] == 'execution' and item.get('value', {}).get('current_stage'):
            item['value']['current_stage'] = 'unrelated-stage'
            changed = True
output = json.dumps(body, ensure_ascii=False) + '\n' if changed else original
config = Path(sys.argv[sys.argv.index('--config') + 1])
with (config.parent / 'forwarder.jsonl').open('a') as stream:
    stream.write(json.dumps({'argv': command, 'input': packet,
                            'exit_code': result.returncode,
                            'original_stdout': original, 'stdout': output,
                            'stderr': result.stderr.decode('utf-8'),
                            'changed': changed}, ensure_ascii=False) + '\n')
sys.stdout.write(output)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
