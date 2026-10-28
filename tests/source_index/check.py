"""Finite selections only; existing reporter owns pytest and actual outcomes."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.skills.record_instruction_red import run


def main():
    groups = json.loads((Path(__file__).with_name('fixtures') / 'groups.json').read_text())
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', required=True, choices=sorted(groups))
    selected = parser.parse_args().group
    actual, output = run(groups[selected], Path(os.environ['POISE_RUN_OUTPUT_DIR']), selected)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
