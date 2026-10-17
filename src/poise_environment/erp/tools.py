"""Observe selected execution tools and extensions, without guessed installers."""
import json
import os
import shutil

from .process import execute, require_success
from ..shared.protocol import Observation


def tool(options: dict, action: str) -> Observation:
    result = execute(options["argv"], options["cwd"],
                     dict(os.environ), options["bound_seconds"])
    output = require_success(result)
    if options["expected"]:
        observed = json.loads(output)
        if any(observed.get(name) != value for name, value in
               options["expected"].items()):
            return Observation("invalid", "invalid",
                               output.decode("utf-8"))
    return Observation("satisfied")


def service_tools(options: dict, action: str) -> Observation:
    missing = [name for name in options["commands"] if
               shutil.which(name) is None]
    if missing:
        return Observation("unavailable", "process",
                           "Unavailable commands: " + ", ".join(
                               missing))
    return Observation("satisfied")


def php_extension(options: dict, action: str) -> Observation:
    output = require_success(execute(options["argv"], options["cwd"],
                                     dict(os.environ),
                                     options["bound_seconds"]))
    extensions = {name.lower() for name in json.loads(output)}
    if options["extension"] not in extensions:
        return Observation("missing", "missing",
                           "PHP extension: " + options["extension"])
    return Observation("satisfied")
