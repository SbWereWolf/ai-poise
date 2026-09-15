"""Check current AI-poise Markdown links and anchors, including incoming links."""
import argparse
import json
from pathlib import Path

from poise.infrastructure.documentation_checks import audit_documentation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', required=True, type=Path)
    parser.add_argument('--changed', action='append', help='Changed or deleted concrete path; repeatable')
    args = parser.parse_args()
    try:
        result = audit_documentation(args.checkout, args.changed)
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
