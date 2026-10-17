"""Concrete ERP application-owned schemas, artifacts and readiness."""
import json
import os
from pathlib import Path
import sqlite3
from xml.etree import ElementTree

from ..shared.protocol import Observation
from .environment import read_environment, selected_paths
from .files import application_databases
from .process import execute, require_success


def queue_schema(options: dict, action: str) -> Observation:
    for path in selected_paths(options):
        if not Path(path).is_file() and action == "check":
            return Observation("missing", "missing", "Queue: " + path)
        if action != "check":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            require_success(
                execute([*options["command"], "bootstrap", path],
                        options["cwd"],
                        dict(os.environ), options["bound_seconds"]))
        checked = execute([*options["command"], "verify", path],
                          options["cwd"],
                          dict(os.environ), options["bound_seconds"])
        if checked.returncode != 0:
            return Observation("invalid", "invalid",
                               checked.stderr.decode("utf-8",
                                                     errors="backslashreplace"))
    return Observation("satisfied")


def artifact(options: dict, action: str) -> Observation:
    path = Path(options["path"])
    if not path.is_file():
        observed = Observation("missing", "missing",
                               "Artifact: " + str(path))
    elif options["format"] == "php":
        result = execute([*options["validator"], str(path)],
                         options["cwd"],
                         dict(os.environ), options["bound_seconds"])
        observed = (
            Observation("satisfied") if result.returncode == 0 else
            Observation("invalid", "invalid",
                        result.stderr.decode("utf-8",
                                             errors="backslashreplace")))
    else:
        try:
            document = ElementTree.parse(path)
            if document.getroot().tag != options["root_tag"]:
                raise ValueError("Unexpected XML root")
            for include in document.getroot().findall(
                    options["include_tag"]):
                included = ElementTree.parse(
                    path.parent / include.attrib["schemaLocation"])
                if included.getroot().tag != options["root_tag"]:
                    raise ValueError("Unexpected included XML root")
            observed = Observation("satisfied")
        except (OSError, ValueError, ElementTree.ParseError) as error:
            observed = Observation("invalid", "invalid", str(error))
    if observed.state != "satisfied" and action != "check":
        require_success(
            execute(options["repair_argv"], options["cwd"],
                    {**os.environ, **options["environment"]},
                    options["repair_bound_seconds"]))
        return artifact(options, "check")
    return observed


def discovery(options: dict, action: str) -> Observation:
    if action != "check":
        require_success(execute(options["command"], options["cwd"],
                                dict(os.environ),
                                options["bound_seconds"]))
    if not Path(options["path"]).is_file():
        return Observation("missing", "missing",
                           "Package discovery result is missing")
    result = execute(options["probe_argv"], options["cwd"],
                     dict(os.environ), options["bound_seconds"])
    return (Observation("satisfied") if result.returncode == 0 else
            Observation("invalid", "invalid",
                        result.stderr.decode("utf-8",
                                             errors="backslashreplace")))


def database_tables(options: dict) -> Observation:
    paths = selected_paths(options)
    for path, required in zip(paths, options["tables"], strict=True):
        if not Path(path).is_file():
            return Observation("missing", "missing",
                               "Application database: " + path)
        try:
            with sqlite3.connect(Path(path).as_uri() + "?mode=ro",
                                 uri=True) as database:
                actual = {row[0] for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type = ?",
                    ("table",))}
        except sqlite3.Error as error:
            return Observation("invalid", "invalid", str(error))
        missing = set(required) - actual
        if missing:
            return Observation("missing", "missing",
                               "Missing tables: " + ", ".join(
                                   sorted(missing)))
    return Observation("satisfied")


def migrations(options: dict, action: str) -> Observation:
    current = database_tables(options)
    if current.state == "satisfied" or action == "check":
        return current
    require_success(
        execute(options["command"], options["command_cwd"],
                dict(os.environ), options["bound_seconds"]))
    return database_tables(options)


def settings_observation(options: dict) -> Observation:
    path = selected_paths(options)[0]
    if not Path(path).is_file():
        return Observation("missing", "missing",
                           "Settings database is missing")
    try:
        with sqlite3.connect(Path(path).as_uri() + "?mode=ro",
                             uri=True) as database:
            actual = set(
                database.execute(options["query"]).fetchall())
    except sqlite3.Error as error:
        return Observation("missing", "missing", str(error))
    if not {tuple(row) for row in options["required_rows"]} <= actual:
        return Observation("missing", "missing",
                           "Required persisted runtime settings are missing")
    return Observation("satisfied")


def bootstrap_settings(options: dict, action: str) -> Observation:
    current = settings_observation(options)
    if current.state == "satisfied" or action == "check":
        return current
    require_success(
        execute(options["command"], options["command_cwd"],
                dict(os.environ), options["bound_seconds"]))
    return settings_observation(options)


def readiness(options: dict, action: str) -> Observation:
    current = database_tables(options["databases"])
    if current.state != "satisfied":
        return current
    current = settings_observation(options["settings"])
    if current.state != "satisfied":
        return current
    result = execute(options["cjm_argv"], options["cwd"],
                     dict(os.environ), options["bound_seconds"])
    if result.returncode != 0:
        return Observation("invalid", "invalid",
                           result.stderr.decode("utf-8",
                                                errors="backslashreplace"))
    json.loads(result.stdout)
    return Observation("satisfied")
