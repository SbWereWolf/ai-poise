"""Build missing requirements stores from their configured migration histories."""
import argparse
from pathlib import Path
from poise.common import load_config
from poise.infrastructure.requirements_data_migrations import RequirementsMigrations
from ..shared.database import maintain
from ..shared.protocol import serve


def handle(args):
    def initialize(path, lock):
        root, configuration, _ = load_config(args.config)
        sources = {name: (root / configuration['paths'][name]).resolve()
                   for name in ('requirements_schema_migrations', 'requirements_data_migrations')}
        RequirementsMigrations.initialize(Path(path), sources, configuration['limits']['lock_seconds'])

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
