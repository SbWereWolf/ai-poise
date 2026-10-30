"""Operator-composed real Git hook; observes, then invokes public index CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys


config, policy, trace, event = sys.argv[1:5]
root = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def record(position, **data):
    with Path(trace).open('a') as stream:
        stream.write(json.dumps({'position': position, 'event': event, 'root': root,
                                'head': head, 'pid': os.getpid(), **data}) + '\n')


record('foreign-before')
argv = [sys.executable, '-B', '-m', 'poise', 'source-index', '--config', config,
        '--checkout', root, '--policy', policy]
if len(sys.argv) == 6:
    argv = ['bash', sys.argv[5], sys.executable, config, policy]
cp = subprocess.run(argv, input='{"operation":"update","arguments":[]}',
                    text=True, capture_output=True, check=False)
captures = []
if cp.stdout:
    value = json.loads(cp.stdout)
    for observation in value.get('observations', []):
        execution = observation['execution']
        captures.append({'operation': observation['operation'],
                         'stdout': Path(execution['stdout']).read_text(),
                         'stderr': Path(execution['stderr']).read_text()})
record('index', argv=argv, exit_code=cp.returncode, stdout=cp.stdout,
       stderr=cp.stderr, complete_captures=captures)
record('foreign-after')
sys.stdout.write(cp.stdout); sys.stderr.write(cp.stderr)
# A notification failure is distinct from the Git commit already made.
raise SystemExit(cp.returncode)
