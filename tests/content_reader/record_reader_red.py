"""Reuse the existing single-invocation reporter without altering outcomes."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.skills.record_instruction_red import run


def main():
    nodes = json.loads((Path(__file__).with_name('fixtures') / 'nodes.json').read_text())
    actual, output = run(nodes, Path(os.environ['POISE_RUN_OUTPUT_DIR']), 'reader')
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
