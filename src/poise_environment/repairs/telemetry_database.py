"""telemetry_database: retain the canonical schema initializer and selector."""
import argparse
from poise.infrastructure.telemetry import TelemetryDatabase
from ..shared.database import maintain
from ..shared.protocol import serve


def initialize(path, lock):
    with TelemetryDatabase(path, lock, 2.0, 0.01).transaction():
        pass


def handle(args):
    return maintain(args.config, args.action,
                    lambda configuration:
                    configuration["accounting"]["storage"][
                        "database"],
                    initialize, args.state_root)


def main():
    parser = argparse.ArgumentParser(
        description="База выбранного окружения")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-root", required=True)
    return serve(parser, handle)


if __name__ == "__main__":
    raise SystemExit(main())
