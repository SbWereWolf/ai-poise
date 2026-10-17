"""One invocation of the existing reporter; full actual diagnostics retained."""
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
                        choices=['classifier','coverage','settlement','native','installed'])
    group = parser.parse_args().group
    nodes = json.loads((Path(__file__).with_name('fixtures') / 'nodes.json').read_text())[group]
    actual, output = run(nodes, Path(os.environ['POISE_RUN_OUTPUT_DIR']), group)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
