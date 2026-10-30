"""Real effects/signals/barriers only; no Poise or result implementation."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--events', required=True)
    parser.add_argument('--marker', required=True)
    parser.add_argument('--mode', choices=['exit', 'hold', 'term', 'ignore'], default='exit')
    parser.add_argument('--code', type=int, default=0)
    parser.add_argument('--stdout-file', required=True)
    parser.add_argument('--stderr-file', required=True)
    parser.add_argument('--descendant', action='store_true')
    args, rest = parser.parse_known_args()
    root = Path(args.events)

    def emit(kind, **values):
        with (root / (args.marker + '.jsonl')).open('a') as stream:
            stream.write(json.dumps({'event': kind, 'pid': os.getpid(), **values}) + '\n')

    def term(*_):
        emit('TERM')
        if args.mode == 'term':
            sys.stdout.write('gracefully stopped\n')
            sys.stdout.flush()
            raise SystemExit(17)

    if args.mode in ('term', 'ignore'):
        signal.signal(signal.SIGTERM, term)
    child = None
    if args.descendant:
        ready = root / (args.marker + '.descendant-ready')
        child = subprocess.Popen([sys.executable, '-B', '-c',
                                  'import signal,time,pathlib,sys;'
                                  'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                                  'pathlib.Path(sys.argv[1]).write_text("ready");time.sleep(60)', str(ready)])
        deadline = time.monotonic() + 5
        while not ready.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('owned descendant did not initialize')
            time.sleep(0.01)
    emit('STARTED', cwd=os.getcwd(), argv=sys.argv[1:], rest=rest,
         descendant_pid=None if child is None else child.pid,
         scope=os.environ.get('POISE_CHECK_DOCUMENTATION_SCOPE_JSON'))
    sys.stdout.buffer.write(Path(args.stdout_file).read_bytes())
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(Path(args.stderr_file).read_bytes())
    sys.stderr.buffer.flush()
    if args.mode != 'exit':
        deadline = time.monotonic() + 20
        while not (root / (args.marker + '.release')).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('test driver did not release its owned barrier')
            time.sleep(0.01)
    emit('EXIT', code=args.code)
    return args.code


if __name__ == '__main__':
    raise SystemExit(main())
