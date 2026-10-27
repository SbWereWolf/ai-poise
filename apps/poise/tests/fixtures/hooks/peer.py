"""Independent observable public target and read-only probes; no product imports."""
import json
import os
from pathlib import Path
import sys
import time


def trace(value):
    with Path(os.environ['TRACE']).open('a', encoding='utf-8') as output:
        output.write(json.dumps(value, ensure_ascii=False) + '\n')


def main():
    mode, variant = sys.argv[1:]
    raw = sys.stdin.buffer.read()
    entry = {'mode': mode, 'variant': variant, 'pid': os.getpid(), 'cwd': os.getcwd(),
             'environment': dict(os.environ), 'input_hex': raw.hex(),
             'input': json.loads(raw) if raw else None}
    trace(entry)
    if mode == 'primary':
        Path(os.environ['PID_FILE']).write_text(str(os.getpid()))
        sys.stderr.buffer.write((Path(os.environ['RESPONSES']) / 'primary-stderr.txt').read_bytes())
        sys.stderr.buffer.flush()
        if variant == 'timeout':
            sys.stdout.buffer.write(b'PRIMARY-START\n')
            sys.stdout.buffer.flush()
            time.sleep(5)
            return 0
        path = Path(os.environ['RESPONSES']) / ('malformed.txt' if variant == 'malformed' else variant + '.json')
        sys.stdout.buffer.write(path.read_bytes())
        return 7 if variant == 'failed' else 0
    stopped = None
    if variant == 'after-primary':
        pid = int(Path(os.environ['PID_FILE']).read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            stopped = True
        else:
            stopped = False
    value = {'checkout': os.getcwd(), 'message': 'READY' if variant != 'unavailable' else 'NOT_READY'}
    if stopped is not None:
        value['primary_stopped'] = stopped
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode('utf-8') + b'\n')
    return 7 if variant == 'unavailable' else 0


if __name__ == '__main__':
    raise SystemExit(main())
