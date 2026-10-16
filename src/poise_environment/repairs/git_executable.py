"""Read-only capability probe of the configured Git executable."""

import argparse
import subprocess

from ..shared.protocol import Observation, serve


def handle(args):
    try:
        result = subprocess.run([args.git, "--version"],
                                capture_output=True, text=True)
    except OSError as error:
        return Observation("unavailable", "missing", str(error))
    if result.returncode:
        return Observation("unavailable", "missing", result.stderr)
    return Observation("satisfied")


def main():
    parser = argparse.ArgumentParser(description="Выбранный Git")
    parser.add_argument("--git", required=True)
    return serve(parser, handle)


if __name__ == "__main__":
    raise SystemExit(main())
