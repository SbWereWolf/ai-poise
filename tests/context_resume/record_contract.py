"""Select one accepted group for the existing single-invocation pytest recorder."""
import argparse
import json
import os
from pathlib import Path
import sys

from tests.skills.record_instruction_red import run


def main():
    nodes = json.loads((Path(__file__).with_name('fixtures') / 'nodes.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=sorted(nodes), required=True)
    group = parser.parse_args().group
    actual, output = run(nodes[group], Path(os.environ['POISE_RUN_OUTPUT_DIR']), group)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
