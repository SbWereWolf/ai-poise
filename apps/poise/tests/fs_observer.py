"""Observe the actual unchanged CLI; never supply a producer result."""
import argparse
import json
from pathlib import Path
import resource
import runpy
import signal
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--entry', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--report')
    parser.add_argument('--file-limit-zero', action='store_true')
    args = parser.parse_args()
    if args.file_limit_zero and args.report:
        raise RuntimeError('Resource-failure observation uses pipes, not a report file')
    events = []

    def observed(event, values):
        if event == 'open':
            path, mode, flags = values
            if isinstance(path, bytes):
                path = __import__('os').fsdecode(path)
            events.append({'event': event, 'path': path, 'mode': mode, 'flags': flags})
        elif event == 'sqlite3.connect':
            events.append({'event': event, 'database': str(values[0])})

    sys.addaudithook(observed)
    if args.file_limit_zero:
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    sys.argv = [args.entry, '--config', args.config]
    exit_code = 0
    try:
        runpy.run_path(args.entry, run_name='__main__')
    except SystemExit as error:
        exit_code = error.code
    finally:
        if args.report:
            # Snapshot before the observer's own report open event.
            payload = json.dumps({'events': list(events)}, ensure_ascii=False, indent=2)
            Path(args.report).write_text(payload + '\n', encoding='utf-8')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
