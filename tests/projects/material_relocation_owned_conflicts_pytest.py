"""Compose the existing complete-report owner for the literal conflict checks."""
import json
import os
from pathlib import Path

from .material_relocation_pytest import FIXTURES, run


def main():
    expected = json.loads((FIXTURES / 'owned-conflict-nodes.json').read_text())
    if (not isinstance(expected, list) or len(expected) != 44
            or any(not isinstance(node, str) or not node.strip() for node in expected)
            or len(set(expected)) != len(expected)):
        raise ValueError('Invalid literal owned-conflict inventory')
    code, summary = run(expected, expected, Path(os.environ['POISE_RUN_OUTPUT_DIR']))
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
