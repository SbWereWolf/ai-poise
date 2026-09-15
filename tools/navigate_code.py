"""Read-only IDE/AST operation with explicit copy and indexed-content proof."""
import argparse
import json
from pathlib import Path
import sys

from poise.common import PoiseError
from poise.application.navigation import CodeNavigation
from poise.infrastructure.capabilities import LocalProbeExecutor
from poise.infrastructure.goal_config import strict_json
from poise.infrastructure.navigation import NavigationFiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipts', required=True, type=Path)
    args = parser.parse_args()
    executor = LocalProbeExecutor(args.receipts, 0o600,
        {'stdout': 'stdout', 'stderr': 'stderr', 'receipt': 'receipt.json'})
    try:
        result = CodeNavigation(executor, NavigationFiles()).run(strict_json(sys.stdin.read()))
        code = 0 if result['status'] == 'proved' else 1
    except (PoiseError, OSError, ValueError) as exc:
        result, code = {'status': 'rejected', 'reason': str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__': raise SystemExit(main())
