"""Test-owned transparent child process; never imports the launcher."""
import json
import os
import sys

if sys.argv[1:] == ['storage']:
    import sqlite3
    from pathlib import Path
    manifest = Path(os.environ['POISE_CONFIG'])
    cfg = json.loads(manifest.read_text())
    state = Path(cfg['paths']['state'])
    if not state.is_absolute():
        state = manifest.parent / state
    database = state / cfg['paths']['database']
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS probe (value TEXT)')
        connection.execute('INSERT INTO probe(value) VALUES (?)', ('selected operation',))
    print(json.dumps({'config': str(manifest), 'state': str(state), 'database': str(database)}))
    raise SystemExit(0)
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
