"""Finite check selection; the existing recorder owns the single pytest call."""
import argparse
import json
import os
from pathlib import Path
import sys

from tests.skills.record_instruction_red import run


def main():
    groups = json.loads((Path(__file__).parent / 'fixtures/file_proofs/groups.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=tuple(groups), required=True)
    group = parser.parse_args().group
    actual, output = run(groups[group], Path(os.environ['POISE_RUN_OUTPUT_DIR']), group)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
