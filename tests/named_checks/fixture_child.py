"""A real argv/stream/effect fixture, never a check-runner or result oracle."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--marker', required=True)
    parser.add_argument('--stdout', default='work done\n')
    parser.add_argument('--stderr', default='')
    parser.add_argument('--exit', type=int, default=0)
    parser.add_argument('--exit-file')
    parser.add_argument('--sleep', type=float, default=0)
    parser.add_argument('--progress', action='store_true')
    parser.add_argument('--paced-progress', action='store_true')
    parser.add_argument('--descendant', action='store_true')
    parser.add_argument('--barrier', action='store_true')
    parser.add_argument('--evidence')
    parser.add_argument('--subject-mode', choices=['working', 'committed'])
    args, remaining = parser.parse_known_args()
    root = Path(os.environ['F07_FIXTURE_EVENTS'])
    record = {'marker': args.marker, 'argv': sys.argv[1:], 'cwd': os.getcwd(),
              'pid': os.getpid(), 'scope': os.environ.get('POISE_CHECK_DOCUMENTATION_SCOPE_JSON'),
              'remaining': remaining}
    if args.descendant:
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(100)'])
        record['descendant_pid'] = child.pid
    with (root / 'markers.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    if args.evidence:
        target = Path(os.environ['REPO_CHECK_EVIDENCE_DIR']) / args.evidence
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'independent evidence\n')
    if args.barrier:
        (root / 'started').write_text('started\n')
        deadline = time.monotonic() + 5
        while not (root / 'continue').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('The test did not release its explicit child barrier')
            time.sleep(0.01)
    if args.progress:
        for number in range(3):
            print(f'PROGRESS {number}/2', flush=True)
            time.sleep(0.02)
    if args.paced_progress:
        for number in range(3):
            print(f'PROGRESS {number}/2', flush=True)
            deadline = time.monotonic() + 5
            acknowledgement = root / ('captured-progress-' + str(number))
            while not acknowledgement.exists():
                if time.monotonic() > deadline:
                    raise RuntimeError('The capture owner did not acknowledge actual fixture output')
                time.sleep(0.005)
    sys.stdout.buffer.write(args.stdout.encode('utf-8'))
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(args.stderr.encode('utf-8'))
    sys.stderr.buffer.flush()
    time.sleep(args.sleep)
    return int(Path(args.exit_file).read_text()) if args.exit_file is not None else args.exit


if __name__ == '__main__':
    raise SystemExit(main())
