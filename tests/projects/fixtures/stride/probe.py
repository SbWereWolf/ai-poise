"""Test-owned transparent child process; never imports the launcher."""
import json
import os
import sys

if sys.argv[1:] == ['package-version']:
    from importlib.metadata import version
    print(version('ai-poise'))
    raise SystemExit(0)
if sys.argv[1:] == ['terminate']:
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
if sys.argv[1:] == ['streams']:
    print('child stdout')
    print('child stderr', file=sys.stderr)
    raise SystemExit(17)
print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(),
                  'config': os.environ.get('POISE_CONFIG'), 'stdin': sys.stdin.read()},
                 ensure_ascii=False))
