"""Run or reuse one declared AI-poise test package."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--package',required=True)
    parser.add_argument('--timeout',required=True,type=float)
    parser.add_argument('--fresh',action='store_true')
    args=parser.parse_args()
    root=args.root.resolve()
    cache=AiPoiseTestPackageCache(root,root/'config/testing/test-packages.json')
    result=cache.run(args.package,timeout_seconds=args.timeout,fresh=args.fresh)
    print(json.dumps(result,sort_keys=True,ensure_ascii=False))
    return 0 if result['passed'] else 1

if __name__=='__main__':
    raise SystemExit(main())
