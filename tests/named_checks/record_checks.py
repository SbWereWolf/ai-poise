"""Select literal nodes; the existing reporter invokes pytest exactly once."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.skills.record_instruction_red import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', required=True,
                        choices=['contracts', 'execution', 'history', 'packages'])
    args = parser.parse_args()
    nodes = json.loads((Path(__file__).parent / 'fixtures/groups.json').read_text())[args.group]
    actual, output = run(nodes, Path(os.environ['POISE_RUN_OUTPUT_DIR']), args.group)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
