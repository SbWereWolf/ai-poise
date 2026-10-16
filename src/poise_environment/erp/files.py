"""Whole-file JSON publication and non-destructive filesystem resources."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

from ..repairs.directory import handle as directory
from ..shared.protocol import MaintenanceError, Observation


def publish_json(source: str, target: str) -> None:
    content = Path(source).read_bytes()
    json.loads(content)
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb",
                                         dir=destination.parent,
                                         prefix=".configuration-",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def json_document(options: dict, action: str) -> Observation:
    try:
        content = Path(options["source"]).read_bytes()
        json.loads(content)
    except (OSError, ValueError) as error:
        raise MaintenanceError("input", str(error)) from error
    if action != "check":
        publish_json(options["source"], options["target"])
    try:
        actual = Path(options["target"]).read_bytes()
    except FileNotFoundError:
        return Observation("missing", "missing")
    return Observation(
        "satisfied") if actual == content else Observation("invalid",
                                                           "invalid")


def directories(options: dict, action: str) -> Observation:
    for path in options["paths"]:
        observed = directory(
            SimpleNamespace(path=path, permissions=options["mode"],
                            action=action))
        if observed.state != "satisfied":
            return Observation(observed.state, observed.cause,
                               f"Directory: {path}")
    return Observation("satisfied")


def application_databases(paths: list[str],
                          action: str) -> Observation:
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            if action == "check":
                return Observation("missing", "missing",
                                   f"Database: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as connection:
                connection.execute("PRAGMA user_version = 0")
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro",
                                 uri=True) as connection:
                result = connection.execute(
                    "PRAGMA quick_check").fetchone()
                if result != ("ok",):
                    return Observation("invalid", "invalid",
                                       f"Database: {path}")
        except sqlite3.Error as error:
            return Observation("invalid", "invalid", str(error))
    return Observation("satisfied")
