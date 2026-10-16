"""Use the existing raw pytest reporter once; Poise owns execution/output."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.skills.record_instruction_red import run

if __name__ == '__main__':
    nodes = json.loads((Path(__file__).parent / 'fixtures/nodes.json').read_text())
    actual, output = run(nodes, Path(os.environ['POISE_RUN_OUTPUT_DIR']), 'placement')
    sys.stdout.write(output)
    raise SystemExit(actual)
