"""Select literal accepted nodes and retain the existing complete pytest recorder."""
import argparse
import json
import os
from pathlib import Path
import sys
from tests.skills.record_instruction_red import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', required=True)
    args = parser.parse_args()
    groups = json.loads((Path(__file__).parent / 'fixtures/pending_source/groups.json').read_text())
    if args.group not in groups:
        parser.error('Select a declared pending source group')
    actual, output = run(groups[args.group], Path(os.environ['POISE_RUN_OUTPUT_DIR']),
                         'pending-source-' + args.group)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
