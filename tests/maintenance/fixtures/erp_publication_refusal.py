"""Test-owned Python filesystem boundary, not a product hook or test runner.

Forward the real declared Python action. Refuse only its actual selected target
replacement, after independently observing the prepared source bytes.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--target', required=True)
parser.add_argument('--events', required=True)
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ['--'] else args.command
if not command or command[0] != sys.executable:
    raise SystemExit('This arrangement requires the declared Python action runtime')
target = os.path.abspath(args.target)


def refuse_selected(original):
    def boundary(source, destination, *positional, **keywords):
        if os.path.abspath(destination) != target:
            return original(source, destination, *positional, **keywords)
        if keywords.get('src_dir_fd') is not None or keywords.get('dst_dir_fd') is not None:
            raise SystemExit('Explicit directory-fd arrangement is required for this action')
        prepared = Path(source).read_bytes()
        with Path(args.events).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({
                'operation': original.__name__, 'errno': errno.EACCES,
                'prepared_sha256': hashlib.sha256(prepared).hexdigest(),
            }) + '\n')
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), os.fspath(destination))
    return boundary


os.replace = refuse_selected(os.replace)
os.rename = refuse_selected(os.rename)
arguments = command[1:]
while arguments and arguments[0] in ('-B', '-s'):
    arguments.pop(0)
if arguments[:1] == ['-m'] and len(arguments) >= 2:
    module = arguments[1]
    sys.argv = [module, *arguments[2:]]
    runpy.run_module(module, run_name='__main__', alter_sys=True)
elif arguments and arguments[0].endswith('.py'):
    sys.argv = arguments
    runpy.run_path(arguments[0], run_name='__main__')
else:
    raise SystemExit('Unsupported action invocation: no publication fault was injected')
