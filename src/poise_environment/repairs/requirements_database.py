"""requirements_database: retain the canonical schema initializer and selector."""
import argparse
from poise.infrastructure.requirements_registry import \
    RequirementsStore
from ..shared.database import maintain
from ..shared.protocol import serve


def initialize(path, lock):
    RequirementsStore(path, lock, 2.0, 0.01)


def handle(args):
    return maintain(args.config, args.action,
                    lambda configuration: configuration["paths"][
                        "requirements_database"],
                    initialize, args.state_root)


def main():
    parser = argparse.ArgumentParser(
        description="База выбранного окружения")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-root", required=True)
    return serve(parser, handle)


if __name__ == "__main__":
    raise SystemExit(main())
