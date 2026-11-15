"""Run or reuse one declared AI-poise test package."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache
from poise.common import PoiseError


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--package')
    mode.add_argument('--cleanup-run')
    parser.add_argument('--timeout',type=float)
    parser.add_argument('--fresh',action='store_true')
    args=parser.parse_args()
    if args.package and args.timeout is None:
        parser.error('--timeout is required for a package run')
    if args.cleanup_run and (args.fresh or args.timeout is not None):
        parser.error('--cleanup-run is separate from package execution options')
    root=args.root.resolve()
    try:
        cache=AiPoiseTestPackageCache(root,root/'config/testing/test-packages.json')
        result=(cache.cleanup_run(args.cleanup_run) if args.cleanup_run else
                cache.run(args.package,timeout_seconds=args.timeout,fresh=args.fresh))
    except PoiseError as exc:
        print(json.dumps({'status':'rejected','reason':str(exc)},ensure_ascii=False))
        return 2
    print(json.dumps(result,sort_keys=True,ensure_ascii=False))
    return (0 if result['success'] else 2) if args.cleanup_run else (0 if result['passed'] else 1)

if __name__=='__main__':
    raise SystemExit(main())
