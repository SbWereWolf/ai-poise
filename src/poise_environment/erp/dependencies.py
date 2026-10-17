"""Real package-manager operations and their declared installed postconditions."""
import json
import os
from pathlib import Path
import shutil

from ..shared.protocol import MaintenanceError, Observation
from .process import execute, require_success


def composer_ready(options: dict, project: dict) -> bool:
    root = Path(project["root"])
    if not all((root / path).is_file() for path in
               project["required_files"]):
        return False
    result = execute([options["php"], options["helper"], "autoload",
                      str(root / project["autoload"]),
                      json.dumps(project["classes"])],
                     str(root), dict(os.environ),
                     options["probe_bound_seconds"])
    if result.returncode != 0:
        return False
    return json.loads(result.stdout)["usable"] is True


def composer_dependencies(options: dict, action: str) -> Observation:
    for project in options["projects"]:
        if composer_ready(options, project):
            continue
        if action == "check":
            return Observation("missing", "missing",
                               "Composer dependencies: " + project[
                                   "root"])
        require_success(
            execute(project["install_argv"], project["root"],
                    {**os.environ, **options["environment"]},
                    options["install_bound_seconds"]))
        if not composer_ready(options, project):
            return Observation("invalid", "invalid",
                               "Composer postcondition failed: " +
                               project["root"])
    return Observation("satisfied")


def node_environment(options: dict) -> dict:
    selected = shutil.which(options["runtime"])
    if selected is None:
        raise MaintenanceError("process",
                               "Selected Node executable is unavailable: " +
                               options["runtime"])
    process_environment = {**os.environ, **options["environment"]}
    process_environment["PATH"] = str(
        Path(selected).parent) + os.pathsep + process_environment[
                                      "PATH"]
    return process_environment


def node_readiness(options: dict, process_environment: dict) -> Observation:
    root = Path(options["cwd"])
    if not all((root / path).is_file() for path in options["required_files"]):
        return Observation("missing", "missing",
                           "Node dependencies: " + str(root))
    if not options["check_argvs"]:
        raise ValueError("Node readiness requires configured probe commands")
    for argv in options["check_argvs"]:
        result = execute(argv, str(root), process_environment,
                         options["bound_seconds"])
        if result.returncode != 0:
            detail = (f"Node dependency probe exited {result.returncode}\n"
                      + result.stdout.decode("utf-8", errors="backslashreplace")
                      + result.stderr.decode("utf-8", errors="backslashreplace"))
            return Observation("invalid", "invalid", detail)
    return Observation("satisfied")


def node_dependencies(options: dict, action: str) -> Observation:
    process_environment = node_environment(options)
    observed = node_readiness(options, process_environment)
    if observed.state == "satisfied" or action == "check":
        return observed
    root = Path(options["cwd"])
    require_success(execute(options["install_argv"], str(root),
                            process_environment,
                            options["bound_seconds"]))
    return node_readiness(options, process_environment)


def mirrored_packages(options: dict, action: str) -> Observation:
    if action != "check":
        require_success(
            execute(options["repair_argv"], options["cwd"],
                    {**os.environ, **options["environment"]},
                    options["bound_seconds"]))
    result = execute(options["paths_argv"], options["cwd"],
                     dict(os.environ), options["bound_seconds"])
    if result.returncode != 0:
        return Observation("missing", "missing",
                           result.stderr.decode("utf-8",
                                                errors="backslashreplace"))
    paths = json.loads(result.stdout)
    for name, source in options["packages"].items():
        installed = paths.get(name)
        if not installed:
            return Observation("missing", "missing",
                               "Installed package: " + name)
        source_root, target_root = Path(source), Path(installed)
        for path in source_root.rglob("*"):
            relative = path.relative_to(source_root)
            if any(part in options["excluded_directories"] for part in
                   relative.parts) or not path.is_file():
                continue
            target = target_root / relative
            if not target.is_file() or target.read_bytes() != path.read_bytes():
                return Observation("invalid", "invalid",
                                   "Mirrored package differs: " + name + "/" + str(
                                       relative))
    return Observation("satisfied")


def playwright(options: dict, action: str) -> Observation:
    if all(Path(path).is_file() for path in
           options["required_files"]):
        return Observation("satisfied")
    return Observation("missing", "missing",
                       "Install the selected Playwright dependency project")
