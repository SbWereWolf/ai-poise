"""Non-destructive observations; schema creation stays with each real owner."""
import os
from pathlib import Path
import sqlite3
import tempfile

from poise.common import read_json, load_config_document, \
    configured_root, configured_storage_path, PoiseError
from .protocol import MaintenanceError, Observation


def shape(database):
    return database.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()


def selected_path(config, selector, expected_state):
    manifest = Path(config).absolute()
    configuration = read_json(manifest)
    root = manifest.parent
    state = Path(configured_root(root, configuration["paths"][
        "state"])).absolute()
    expected = Path(expected_state).absolute()
    if state != expected:
        raise MaintenanceError("configuration_drift",
                               "Configured state differs from selected state")
    path = Path(configured_storage_path(state, selector(
        configuration))).absolute()
    if not path.is_relative_to(expected):
        raise MaintenanceError("configuration_drift",
                               "Database is outside selected state")
    # Validate the same observed document before allowing any resource effect.
    _, validated, _ = load_config_document(manifest, configuration)
    path = Path(configured_storage_path(state, selector(
        validated))).absolute()
    return path


def inspect(path, version, expected):
    if not path.exists():
        return Observation("missing", "missing")
    if not path.is_file() or not os.access(path, os.R_OK | os.W_OK):
        return Observation("unavailable", "permissions")
    if any(Path(str(path) + suffix).exists() for suffix in
           ("-wal", "-shm", "-journal")):
        return Observation("unavailable", "busy")
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                             timeout=0) as database:
            if database.execute(
                    "PRAGMA integrity_check").fetchall() != [("ok",)]:
                return Observation("unavailable", "corrupt")
            if database.execute("PRAGMA user_version").fetchone()[
                0] != version:
                return Observation("unavailable", "version")
            if shape(database) != expected:
                return Observation("unavailable", "schema")
            if database.execute(
                    "PRAGMA foreign_key_check").fetchall():
                return Observation("unavailable", "foreign_keys")
    except sqlite3.DatabaseError as error:
        return Observation("unavailable", "corrupt", str(error))
    return Observation("satisfied")


def maintain(config, action, selector, initialize, expected_state):
    if not Path(config).exists():
        return Observation("missing", "missing")
    try:
        path = selected_path(config, selector, expected_state)
    except PoiseError as error:
        return Observation("unavailable", "invalid_config",
                           str(error))
    with tempfile.TemporaryDirectory(
            prefix="poise-schema-reference-") as raw:
        reference = Path(raw) / "reference.sqlite"
        initialize(reference, Path(raw) / "reference.lock")
        with sqlite3.connect(reference.as_uri() + "?mode=ro",
                             uri=True) as database:
            version = \
            database.execute("PRAGMA user_version").fetchone()[0]
            expected = shape(database)
        before = inspect(path, version, expected)
        if action == "check" or before.state != "missing":
            return before
        if not path.parent.is_dir():
            return Observation("unavailable", "missing_parent")
        with tempfile.TemporaryDirectory(prefix=".poise-db-",
                                         dir=path.parent) as raw:
            new = Path(raw) / "database.sqlite"
            initialize(new, Path(raw) / "database.lock")
            checked = inspect(new, version, expected)
            if checked.state != "satisfied":
                return checked
            new.chmod(0o600)
            os.link(new, path)
    return inspect(path, version, expected)
