"""Select a finite test group; the existing recorder owns pytest observation."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.skills.record_instruction_red import run


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--group', required=True)
    args = p.parse_args()
    groups = json.loads((Path(__file__).with_name('fixtures') / 'tool_results' / 'groups.json').read_bytes())
    if args.group not in groups:
        p.error('Unknown explicit group')
    code, output = run(['-p', 'tests.conftest', *groups[args.group]],
                       Path(os.environ['POISE_RUN_OUTPUT_DIR']), args.group)
    sys.stdout.write(output)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
