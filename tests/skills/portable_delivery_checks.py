"""Report one selected pytest run; preserve all streams in runner-owned files."""
import argparse
import json
import os
from pathlib import Path
import sys

from tests.skills.record_instruction_red import run


FIXTURES = Path(__file__).parent / 'fixtures/portable_delivery'


def expected_red(actual):
    expected = json.loads((FIXTURES / 'red-events.json').read_text())
    return actual == expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['red', 'behavior', 'package', 'guard', 'regression'], required=True)
    args = parser.parse_args()
    groups = json.loads((FIXTURES / 'groups.json').read_text())
    actual, output = run(groups[args.group], Path(os.environ['POISE_RUN_OUTPUT_DIR']), args.group)
    events = json.loads(output)
    if args.group == 'red' and expected_red(events):
        print('PORTABLE_DELIVERY_BUDGET_UNSUPPORTED')
        return actual
    # No normalization of setup/collection/teardown errors, unexpected failed
    # calls, skips, or successful RED into the intended behavioral failure.
    calls = [r for r in events['reports'] if r['phase'] == 'call']
    if args.group != 'red' and actual == 0 and calls and all(
            r['outcome'] == 'passed' for r in events['reports']):
        print('PORTABLE_DELIVERY_' + args.group.upper() + '_PASS')
    else:
        sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
