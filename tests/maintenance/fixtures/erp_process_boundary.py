"""Test-only process boundary: truthful full capture, normal OS/write failure.

Not a check runner or maintenance engine. It forwards a real declared action,
or provides an explicitly labelled wire fixture for ResultHandler-only checks.
"""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--reply-file')
parser.add_argument('--stderr-file')
parser.add_argument('--exit-code', type=int, default=0)
parser.add_argument('--file-size-limit', type=int)
parser.add_argument('--after-effect-failure', action='store_true')
parser.add_argument('--log')
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
if args.reply_file:
    sys.stdout.buffer.write(Path(args.reply_file).read_bytes())
    if args.stderr_file:
        sys.stderr.buffer.write(Path(args.stderr_file).read_bytes())
    raise SystemExit(args.exit_code)
command = args.command[1:] if args.command[:1] == ['--'] else args.command
if not command:
    raise SystemExit('A real subject command is required')
if args.log:
    with Path(args.log).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(command, ensure_ascii=False) + '\n')


def limits():
    if args.file_size_limit is not None:
        resource.setrlimit(resource.RLIMIT_FSIZE,
                           (args.file_size_limit, args.file_size_limit))


result = subprocess.run(command, capture_output=True, preexec_fn=limits)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
if args.after_effect_failure and result.returncode == 0:
    sys.stderr.write('\nFixture interruption after the real publication\n')
    raise SystemExit(2)
raise SystemExit(result.returncode if result.returncode >= 0 else 128 - result.returncode)
