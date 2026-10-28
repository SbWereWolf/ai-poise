"""Transparent external transport peer; never performs harness decisions."""
import json
import os
from pathlib import Path
import sys
import time


control = json.loads(Path(sys.argv[1]).read_text())
arguments = sys.argv[2:]
operation = arguments[0]
database = Path(os.environ['AST_INDEX_DB_PATH'])
events = Path(control['events'])


def record(phase):
    with events.open('a') as stream:
        stream.write(json.dumps({'operation': operation, 'phase': phase,
                                'pid': os.getpid(), 'parent_pid': os.getppid(),
                                'cwd': os.getcwd(), 'argv': arguments,
                                'root': os.environ.get('AST_INDEX_ROOT'),
                                'database': str(database)}) + '\n')


record('entered')
if operation == 'update' and control['barrier'] and (
        control['barrier_checkout'] is None or control['barrier_checkout'] == os.getcwd()):
    barrier = Path(control['barrier'])
    barrier.with_suffix('.reached').write_text(str(os.getpid()))
    deadline = time.monotonic() + 12
    while not barrier.with_suffix('.release').exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('Test peer barrier was not released')
        time.sleep(0.01)

code = 0
if operation == 'stats':
    if database.exists() and database.read_bytes() == b'UNINTERPRETABLE':
        print('unexpected statistics representation')
    elif database.exists():
        health = {'db_path': str(database), 'db_size_bytes': database.stat().st_size}
        if control['large']:
            health['padding'] = json.loads(Path(__file__).with_name('large-query.json').read_text())['padding']
        health['stats'] = {'file_count': 1, 'symbol_count': 2, 'refs_count': 1}
        print(json.dumps(health))
    else:
        print("Index not found. Run 'ast-index rebuild' first.")
elif operation in ('rebuild', 'update'):
    code = control['update_exit'] if operation == 'update' else 0
    if code:
        print('UPDATE_STDOUT_SENTINEL'); print('UPDATE_STDERR_SENTINEL', file=sys.stderr)
    else:
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b'FIXTURE_INDEX')
        if operation == 'update' and control['after_update'] == 'missing-after-update':
            database.unlink()
        elif operation == 'update' and control['after_update'] == 'uninterpretable':
            database.write_bytes(b'UNINTERPRETABLE')
        print(json.dumps({'operation': operation, 'updated': True}))
elif operation in ('search', 'usages'):
    selected = control['query_by_checkout'].get(os.getcwd())
    payload = json.loads((Path(selected) if selected else Path(__file__).with_name('query.json')).read_text())
    if control['large']:
        sys.stdout.write(Path(__file__).with_name('large-query.json').read_text())
    else:
        print(json.dumps(payload))
    print('QUERY_STDERR_TAIL', file=sys.stderr)
    code = control['query_exit']
else:
    raise AssertionError('Unexpected peer operation: ' + operation)
record('finished')
raise SystemExit(code)
