"""One declared directory resource: existence, mode and applicable effects."""

import argparse
from pathlib import Path

from ..shared.protocol import Observation, serve


def handle(args):
    path = Path(args.path).absolute()
    mode = int(args.permissions, 8)
    if path.exists() and not path.is_dir():
        return Observation("unavailable", "type")
    if args.action != "check":
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        path.chmod(mode)
    if not path.exists():
        return Observation("missing", "missing")
    if path.stat().st_mode & 0o777 != mode:
        return Observation("invalid", "permissions")
    return Observation("satisfied")


def main():
    parser = argparse.ArgumentParser(
        description="Каталог выбранного окружения")
    parser.add_argument("--path", required=True)
    parser.add_argument("--permissions", required=True)
    return serve(parser, handle)


if __name__ == "__main__":
    raise SystemExit(main())
