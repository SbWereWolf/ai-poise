"""Observe the selected local repository; no implicit repository mutation."""
import argparse
from pathlib import Path
import subprocess
from ..shared.protocol import Observation, serve


def handle(args):
    repository = Path(args.repository).absolute()
    if not repository.is_dir():
        return Observation("unavailable", "missing")

    def probe(*arguments):
        return subprocess.run(
            [args.git, "-C", str(repository), *arguments],
            capture_output=True, text=True)

    try:
        top = probe("rev-parse", "--show-toplevel")
        if top.returncode or Path(top.stdout.strip()) != repository:
            return Observation("unavailable", "root", top.stderr)
        revision = probe("rev-parse", "--verify", "--end-of-options",
                         args.base_ref + "^{commit}")
        if revision.returncode:
            return Observation("unavailable", "base", revision.stderr)
    except OSError as error:
        return Observation("unavailable", "missing", str(error))
    return Observation("satisfied")


def main():
    parser = argparse.ArgumentParser(
        description="Локальный репозиторий")
    for name in ("repository", "base-ref", "git"):
        parser.add_argument("--" + name, required=True)
    return serve(parser, handle)


if __name__ == "__main__":
    raise SystemExit(main())
