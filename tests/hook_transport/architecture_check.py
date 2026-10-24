"""Finite architecture selection; the existing reporter owns pytest execution.

No expected outcome, retry, exit transformation or test implementation lives here.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.skills.record_instruction_red import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True,
                        choices=["dispatch", "async", "general", "formatter",
                                 "test-guards", "preservation"])
    args = parser.parse_args()
    groups = Path(__file__).parent / "fixtures/architecture/groups.json"
    nodes = json.loads(groups.read_text(encoding="utf-8"))[args.group]
    actual, summary = run(nodes, Path(os.environ["POISE_RUN_OUTPUT_DIR"]), args.group)
    sys.stdout.write(summary)
    return actual


if __name__ == "__main__":
    raise SystemExit(main())
