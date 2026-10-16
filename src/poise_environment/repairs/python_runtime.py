"""Observe the executing, explicitly selected interpreter."""

import argparse
import sys

from ..shared.protocol import Observation, serve


def handle(args):
    minimum = tuple(map(int, args.minimum.split(".")))
    maximum = tuple(map(int, args.maximum.split(".")))
    current = sys.version_info[:2]
    if not minimum <= current < maximum:
        return Observation("unavailable", "version", sys.version)
    return Observation("satisfied")


def main():
    parser = argparse.ArgumentParser(
        description="Выбранный интерпретатор")
    parser.add_argument("--minimum", required=True)
    parser.add_argument("--maximum", required=True)
    return serve(parser, handle)


if __name__ == "__main__":
    raise SystemExit(main())
