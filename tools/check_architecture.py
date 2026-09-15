"""Check the declared Python architecture boundaries of a concrete AI-poise copy."""
import argparse
import json
from pathlib import Path

from poise.common import PoiseError
from poise.infrastructure.architecture_checks import AiPoiseArchitectureChecks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--path', action='append')
    scope.add_argument('--all', action='store_true', help='Explicitly inspect all policy-covered Python sources, not tests')
    parser.add_argument('--deleted', action='append', default=[])
    args = parser.parse_args()
    try:
        checks = AiPoiseArchitectureChecks.load(args.policy)
        paths = checks.covered_paths(args.checkout) if args.all else args.path
        result = checks.check(args.checkout, paths, deleted_paths=args.deleted)
    except PoiseError as exc:
        print(json.dumps({'status': 'rejected', 'reason': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
