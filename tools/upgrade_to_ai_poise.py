from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


LEGACY_DISTRIBUTION = "agent-" + "har" + "ness" + "-happy-path"


def _run(argv: list[str]) -> None:
    result = subprocess.run(argv, text=True, capture_output=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Команда завершилась с кодом {result.returncode}: {detail}")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Replace the legacy distribution with an ai-poise wheel."
    )
    value.add_argument("--python", type=Path, required=True)
    value.add_argument("--wheel", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    python = args.python.absolute()
    wheel = args.wheel.resolve(strict=True)
    try:
        if not python.is_file():
            raise RuntimeError(f"Python executable does not exist: {python}")
        _run([str(python), "-m", "pip", "uninstall", "-y", LEGACY_DISTRIBUTION])
        _run([str(python), "-m", "pip", "install", "--no-index", str(wheel)])
    except (OSError, RuntimeError) as exc:
        print(f"Не удалось заменить пакет: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
