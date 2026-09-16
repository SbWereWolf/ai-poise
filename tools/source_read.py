"""Bounded explicit file reads with generation-scoped, acknowledged receipts."""
import argparse
import json
from pathlib import Path
import sys

from poise.common import PoiseError
from poise.infrastructure.goal_config import strict_json
from poise.infrastructure.source_reader import FileSourceReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--policy', required=True, type=Path)
    args = parser.parse_args()
    try:
        raw = sys.stdin.read(1024*1024+1)
        if len(raw)>1024*1024:
            raise PoiseError('Reader request exceeds 1 MiB characters')
        request = strict_json(raw)
        if not isinstance(request, dict) or set(request) != {'operation', 'input'}:
            raise PoiseError('Expected operation and input')
        reader = FileSourceReader(args.state, strict_json(args.policy.read_text()))
        op, payload = request['operation'], request['input']
        if op == 'context' and payload == {}:
            result = reader.context()
        elif op == 'reset' and isinstance(payload, dict) and set(payload) == {'reason', 'event_id'}:
            result = reader.reset(**payload)
        elif op == 'read':
            result = reader.read(payload)
        else:
            raise PoiseError('Unsupported reader operation or input')
        code = 0
    except (PoiseError, OSError, ValueError) as exc:
        result, code = {'status': 'rejected', 'reason': str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
