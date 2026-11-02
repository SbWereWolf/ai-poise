"""telemetry_database: retain the canonical schema initializer and selector."""
import argparse
import os
from pathlib import Path
import sqlite3
from poise.common import configured_storage_path, read_json, PoiseError
from poise.infrastructure.telemetry import TelemetryDatabase
from poise.modules.foundation.errors import SQLiteBoundaryUnavailable
from ..shared.database import maintain, selected_path
from ..shared.protocol import serve, Observation


def initialize(path, lock):
    with TelemetryDatabase(path, lock, 2.0, 0.01).transaction():
        pass


def handle(args):
    selector = lambda configuration: configuration["accounting"]["storage"]["database"]
    if not Path(args.config).exists():
        return Observation("missing", "missing")
    try:
        path = selected_path(args.config, selector, args.state_root)
    except PoiseError as error:
        return Observation("unavailable", "invalid_config", str(error))
    if not path.exists():
        return maintain(args.config, args.action, selector, initialize, args.state_root)
    if not path.is_file() or not os.access(path, os.R_OK | os.W_OK):
        return Observation("unavailable", "permissions")
    configuration = read_json(Path(args.config))
    lock = configured_storage_path(Path(args.state_root),
                                   configuration["accounting"]["storage"]["lock"])
    limits = configuration["limits"]
    owner = TelemetryDatabase(path, lock, limits["lock_seconds"],
                              limits["lock_poll_seconds"])
    try:
        with owner.read_transaction() as database:
            if [row[0] for row in database.execute("PRAGMA integrity_check")] != ["ok"]:
                return Observation("unavailable", "corrupt")
    except SQLiteBoundaryUnavailable as error:
        return Observation("unavailable", "busy", str(error))
    except sqlite3.DatabaseError as error:
        return Observation("unavailable", "corrupt", str(error))
    except PoiseError as error:
        return Observation("unavailable", "schema", str(error))
    return Observation("satisfied")


def main():
    parser = argparse.ArgumentParser(
        description="База выбранного окружения")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-root", required=True)
    return serve(parser, handle)


if __name__ == "__main__":
    raise SystemExit(main())
