"""Route explicit AI-poise development facts without bootstrap or Task writes."""
import argparse
import json
from pathlib import Path
import sys

from poise.common import PoiseError
from poise.infrastructure.development_routing import load_development_routing
from poise.infrastructure.goal_config import strict_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('catalog', 'selection', 'policy', 'packages'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--snapshot', type=Path)
    args = parser.parse_args()
    try:
        text = sys.stdin.read(4 * 1024 * 1024 + 1)
        if len(text) > 4 * 1024 * 1024:
            raise PoiseError('Route input exceeds 4 MiB characters')
        router = load_development_routing(**{name: getattr(args, name)
            for name in ('catalog', 'selection', 'policy', 'packages')})
        result = router.route(strict_json(text), snapshot=args.snapshot)
        code = 0 if result['status'] == 'ready' else 1
    except (PoiseError, OSError, ValueError) as exc:
        result, code = {'status': 'rejected', 'reason': str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
