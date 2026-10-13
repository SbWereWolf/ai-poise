#!/usr/bin/env python3
"""Independent fake external owner; never imports the UAT implementation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

if sys.argv[1] == 'subject':
    sys.stdout.buffer.write(b'subject-ok\n')
    raise SystemExit(0)
mode = sys.argv[1]
context_path = Path(sys.argv[2])
context = json.loads(context_path.read_text(encoding='utf-8'))
public = subprocess.run(context['public_argv'], input=b'', capture_output=True, check=False)
observation = {
    'context':context, 'context_path':str(context_path), 'cwd':os.getcwd(),
    'environment':dict(os.environ), 'arguments':sys.argv[3:],
    'public_exit':public.returncode, 'public_stdout':public.stdout.decode('utf-8'),
    'stdin':sys.stdin.read()
}
Path('owner-observation.json').write_text(json.dumps(observation,ensure_ascii=False)+'\n', encoding='utf-8')
# A deliberate misleading result is not a runner-owned PASS certificate.
Path(context['result_path']).write_text('{"status":"PASS"}\n', encoding='utf-8')
if mode == 'stdout':
    sys.stdout.buffer.write(b'x'*50000); sys.stdout.buffer.flush()
elif mode == 'stderr':
    sys.stderr.buffer.write(b'e'*50000); sys.stderr.buffer.flush()
elif mode == 'sleep':
    Path('started').write_bytes(b'possible-effects'); time.sleep(10)
elif mode == 'signal':
    os.kill(os.getpid(), signal.SIGTERM)
elif mode == 'drift':
    with Path(sys.argv[3]).open('ab') as stream: stream.write(b' ')
else:
    sys.stdout.buffer.write((context['step_id']+'\n').encode('utf-8'))
    sys.stderr.buffer.write(('diag-'+context['step_id']+'\n').encode('utf-8'))
raise SystemExit(7 if mode == 'fail' else 0)
