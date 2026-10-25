"""Select an accepted literal group; preserve the existing pytest recorder."""
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
    fixture = Path(__file__).parent / 'fixtures/registry_publication/groups.json'
    groups = json.loads(fixture.read_text())
    if args.group not in groups:
        parser.error('Select a declared registry publication group')
    actual, output = run(groups[args.group], Path(os.environ['POISE_RUN_OUTPUT_DIR']),
                         'registry-' + args.group)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
