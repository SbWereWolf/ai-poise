"""Reuse the existing single-pytest reporter; no result rewriting or retries."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tests.skills.record_instruction_red import run


if __name__ == '__main__':
    nodes = json.loads(Path(__file__).with_name('red-nodes.json').read_text())
    code, summary = run(nodes, Path(os.environ['POISE_RUN_OUTPUT_DIR']), 'initial-proxy')
    sys.stdout.write(summary)
    raise SystemExit(code)
