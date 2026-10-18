"""ERP environment generator and resolved application-unit boundaries."""
import json
import os
from pathlib import Path

from ..shared.protocol import MaintenanceError, Observation
from .process import execute, require_success


def read_environment(options: dict) -> dict[str, str]:
    output = require_success(
        execute(options["read_argv"], options["cwd"],
                dict(os.environ), options["bound_seconds"]))
    value = json.loads(output)
    if not isinstance(value, dict):
        raise MaintenanceError("input",
                               "Environment probe must return an object")
    return value


def selected_paths(options: dict) -> list[str]:
    values = read_environment(options)
    paths = []
    for name in options["keys"]:
        path = Path(values[name])
        paths.append(str(path if path.is_absolute() else Path(
            options["cwd"]) / path))
    return paths


def environment(options: dict, action: str) -> Observation:
    require_success(
        execute(options["validation_argv"], options["cwd"],
                dict(os.environ), options["bound_seconds"]))
    source = read_environment(options)
    manifest = json.loads(
        Path(options["settings"]).read_text(encoding="utf-8"))
    ready = True
    for application in manifest["applications"].values():
        path = Path(options["cwd"]) / application["environmentFile"]
        if not path.is_file():
            ready = False
            continue
        target_options = {**options, "read_argv": [*options["reader"],
                                                   str(path)]}
        actual = read_environment(target_options)
        for entry in application["variables"]:
            name = entry["name"]
            if name in source and actual.get(name) != source[name]:
                ready = False
    if ready:
        return Observation("satisfied")
    if action == "check":
        return Observation("missing", "missing",
                           "Application environment needs generation")
    require_success(execute(options["configure_argv"], options["cwd"],
                            dict(os.environ),
                            options["bound_seconds"]))
    return environment(options, "check")


def unit_values(options: dict, application: dict, operation: str,
                values: dict | None = None) -> dict:
    argv = [options["php"], options["helper"], operation,
            application["autoload"], application["environment"]]
    if values is not None:
        argv.append(json.dumps(values, ensure_ascii=False))
    return json.loads(require_success(execute(argv, options["cwd"],
                                              dict(os.environ),
                                              options[
                                                  "bound_seconds"])))


def installation_units(options: dict, action: str) -> Observation:
    expected = options["values"]
    source_application = options["applications"][0]
    source = unit_values(options, source_application, "units-read")
    canonical = {name: source["values"].get(name, value) for
                 name, value in expected.items()}
    if any(canonical[name] != expected[name] for name in
           options["canonical_keys"]):
        return Observation("invalid", "invalid",
                           "Canonical unit dimensions disagree")
    ready = True
    for application in options["applications"]:
        parsed = unit_values(options, application, "units-read")
        for name, value in canonical.items():
            observed = parsed["values"].get(name)
            if (parsed["counts"].get(name) != 1 or observed != value
                    or not observed or "${" in observed):
                ready = False
        if not ready and action != "check":
            unit_values(options, application, "units-repair",
                        canonical)
    if action != "check":
        return installation_units(options, "check")
    return Observation("satisfied") if ready else Observation(
        "invalid", "invalid", "Unit declarations disagree")
