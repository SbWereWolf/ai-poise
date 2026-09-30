#!/usr/bin/env python3
"""App-owned Bootstrap selection and read-only profile qualification."""
from __future__ import annotations

import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

SCHEMA = "stride-bootstrap/v1"
CONFIG_SCHEMA = "stride-bootstrap-config/v1"
PROJECT_SCHEMA = "stride-bootstrap-project/v1"
CONFIG_FIELDS = {"schema", "projects_root", "default_target", "target_env", "limits"}
PROJECT_FIELDS = {"schema", "id", "catalog", "repairs", "values", "parameter_values_files"}
LIMIT_FIELDS = {"max_targets", "max_target_bytes", "max_output_bytes", "target_timeout_seconds", "run_timeout_seconds"}
TARGET_RE = re.compile(r"^[^/\\\x00]+$")
CONFIG_BYTE_LIMIT = 1024 * 1024
JSON_DEPTH_LIMIT = 128
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


class ShellError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def error_value(code, message, retryable=False):
    return {"code": code, "message": message, "retryable": retryable}


def reject(code, message, retryable=False):
    return {"schema": SCHEMA, "status": "rejected", "exit_code": 2,
            "error": error_value(code, message, retryable), "targets": []}


def emit(value):
    # The protocol is UTF-8, not the ambient text stream's encoding.
    sys.stdout.buffer.write((compact(value) + "\n").encode("utf-8"))
    return value["exit_code"]


def exact_fields(value, expected, label, code="invalid_bootstrap_config"):
    if not isinstance(value, dict) or set(value) != expected:
        # Neither private keys nor values may enter diagnostics.
        raise ShellError(code, label + " has invalid fields")


def strict_json(data, code):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("non-finite number")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=invalid_constant)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > JSON_DEPTH_LIMIT:
                raise ValueError("JSON depth limit exceeded")
            if isinstance(item, str):
                item.encode("utf-8", errors="strict")
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("non-finite exponent")
            elif isinstance(item, dict):
                pending.extend((key, depth + 1) for key in item)
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise ShellError(code, "Input is not a supported strict UTF-8 JSON document") from exc


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def read_regular(parent_fd, name, remaining, *, unavailable, unsafe):
    """Never follow a link or read a special node; detect observed input drift."""
    fd = None
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ShellError(unsafe, "Input must be a regular file without symbolic links")
        if before.st_size > remaining:
            raise ShellError(unavailable, "Input exceeds the available byte budget")
        fd = os.open(name, FILE_FLAGS, dir_fd=parent_fd)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or stamp(opened) != stamp(before):
            raise ShellError(unsafe, "Input identity changed while opening")
        chunks = []
        size = 0
        while True:
            chunk = os.read(fd, min(65536, remaining - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > remaining:
                raise ShellError(unavailable, "Input exceeds the available byte budget")
            chunks.append(chunk)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stamp(before) != stamp(after) or stamp(after) != stamp(named) or size != after.st_size:
            raise ShellError(unavailable, "Input changed during the bounded read")
        return b"".join(chunks)
    except OSError as exc:
        code = unsafe if exc.errno in {errno.ELOOP, errno.ENOTDIR} else unavailable
        raise ShellError(code, "Input file is unavailable or unsafe") from exc
    finally:
        if fd is not None:
            os.close(fd)


def directory_at(parent_fd, name, unavailable, unsafe):
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ShellError(unsafe, "Input directory must not be a symbolic link or special node")
        fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)
        opened = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            os.close(fd)
            raise ShellError(unsafe, "Input directory identity changed while opening")
        return fd
    except OSError as exc:
        code = unsafe if exc.errno in {errno.ELOOP, errno.ENOTDIR} else unavailable
        raise ShellError(code, "Input directory is unavailable or unsafe") from exc


def open_root(path):
    fd = None
    try:
        fd = os.open(path.anchor, DIRECTORY_FLAGS)
        for part in path.parts[1:]:
            child = directory_at(fd, part, "projects_unavailable", "projects_unavailable")
            os.close(fd)
            fd = child
        return fd
    except (OSError, ValueError, UnicodeError) as exc:
        if fd is not None:
            os.close(fd)
        raise ShellError("projects_unavailable", "Configured projects_root is unavailable") from exc
    except ShellError:
        if fd is not None:
            os.close(fd)
        raise


def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ShellError("invalid_bootstrap_config", "Bootstrap config " + name + " must be a positive integer")
    return value


def positive_number(value, name):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("not numeric")
        result = float(value)
        if not math.isfinite(result) or result <= 0:
            raise ValueError("not positive finite")
        return result
    except (ValueError, OverflowError) as exc:
        raise ShellError("invalid_bootstrap_config", "Bootstrap config " + name + " must be a positive finite number") from exc


def utf8_string(value):
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
        return True
    except UnicodeError:
        return False


def valid_target(value, *, allow_all=True):
    if not utf8_string(value) or not value or not TARGET_RE.fullmatch(value) or value in {".", ".."}:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    return allow_all or value != "all"


def load_config(path: Path):
    data = read_regular(None, str(path), CONFIG_BYTE_LIMIT,
                        unavailable="invalid_bootstrap_config", unsafe="invalid_bootstrap_config")
    raw = strict_json(data, "invalid_bootstrap_config")
    exact_fields(raw, CONFIG_FIELDS, "Bootstrap config")
    if raw["schema"] != CONFIG_SCHEMA:
        raise ShellError("invalid_bootstrap_config", "Bootstrap config schema is unsupported")
    root = raw["projects_root"]
    if not isinstance(root, str) or not root or "\x00" in root:
        raise ShellError("invalid_bootstrap_config", "Bootstrap config projects_root must be a nonempty path")
    root_path = Path(root)
    if not root_path.is_absolute():
        root_path = path.parent / root_path
    # Normalize lexical dot components only; resolving here would conceal links.
    root_path = Path(os.path.abspath(root_path))
    default = raw["default_target"]
    if not valid_target(default):
        raise ShellError("invalid_bootstrap_config", "Bootstrap config default_target is invalid")
    env_name = raw["target_env"]
    if env_name is not None and (not isinstance(env_name, str) or not env_name or "\x00" in env_name or "=" in env_name):
        raise ShellError("invalid_bootstrap_config", "Bootstrap config target_env is invalid")
    limits = raw["limits"]
    exact_fields(limits, LIMIT_FIELDS, "Bootstrap config limits")
    parsed_limits = {
        "max_targets": positive_int(limits["max_targets"], "limits.max_targets"),
        "max_target_bytes": positive_int(limits["max_target_bytes"], "limits.max_target_bytes"),
        "max_output_bytes": positive_int(limits["max_output_bytes"], "limits.max_output_bytes"),
        "target_timeout_seconds": positive_number(limits["target_timeout_seconds"], "limits.target_timeout_seconds"),
        "run_timeout_seconds": positive_number(limits["run_timeout_seconds"], "limits.run_timeout_seconds"),
    }
    return {"projects_root": root_path, "default_target": default, "target_env": env_name, "limits": parsed_limits}


def parse_cli(argv):
    if len(argv) < 3 or argv[0] != "--config" or not argv[1] or "\x00" in argv[1]:
        raise ShellError("invalid_request", "Usage: bootstrap --config PATH <infra|deps|check> [options]")
    config = Path(argv[1])
    command = argv[2]
    if command not in {"infra", "deps", "check"}:
        raise ShellError("invalid_request", "Unknown Bootstrap command")
    target = None
    dry_run = False
    sets = []
    i = 3
    while i < len(argv):
        token = argv[i]
        if token == "--dry-run":
            if dry_run:
                raise ShellError("invalid_request", "Duplicate --dry-run")
            dry_run = True
            i += 1
            continue
        if token in {"--target", "--set"}:
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                raise ShellError("invalid_request", "Missing option value")
            value = argv[i + 1]
            if token == "--target":
                if target is not None:
                    raise ShellError("invalid_request", "Duplicate --target")
                if not valid_target(value):
                    raise ShellError("invalid_selector", "Target selector is invalid")
                target = value
            else:
                if "=" not in value or value.startswith("="):
                    raise ShellError("invalid_request", "--set requires NAME=VALUE")
                sets.append(value)
            i += 2
            continue
        raise ShellError("invalid_request", "Unknown Bootstrap argument")
    return config, command, target, dry_run, sets


def selector(config, explicit):
    if explicit is not None:
        return explicit, "argument"
    env_name = config["target_env"]
    if env_name is not None and env_name in os.environ:
        value = os.environ[env_name]
        if not valid_target(value):
            raise ShellError("invalid_selector", "Bootstrap target environment selector is invalid")
        return value, "environment"
    return config["default_target"], "configuration"


def discover(root_fd, requested, maximum):
    if requested != "all":
        try:
            os.stat(requested + ".json", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ShellError("target_not_found", "Requested target does not exist") from exc
        except OSError as exc:
            raise ShellError("projects_unavailable", "Selected project cannot be located") from exc
        return [requested]
    names = []
    try:
        with os.scandir(root_fd) as entries:
            for entry in entries:
                if not entry.name.endswith(".json") or entry.is_dir(follow_symlinks=False):
                    continue
                name = entry.name[:-5]
                if not valid_target(name, allow_all=False):
                    raise ShellError("invalid_selector", "Discovered profile has an invalid or reserved identifier")
                names.append(name)
                if len(names) > maximum:
                    raise ShellError("limit_exceeded", "Selected target count exceeds the configured limit")
    except OSError as exc:
        raise ShellError("projects_unavailable", "Configured projects_root cannot be enumerated") from exc
    return sorted(names)


def asset_parts(value):
    if not utf8_string(value) or not value or "\x00" in value or "\\" in value:
        raise ShellError("unsafe_target_config", "Asset reference is not a safe relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ShellError("unsafe_target_config", "Asset reference is not a safe relative path")
    return parts


class TargetInputs:
    def __init__(self, root_fd, budget):
        self.root_fd = root_fd
        self.remaining = budget

    def read(self, reference, code):
        parts = asset_parts(reference)
        parent = os.dup(self.root_fd)
        try:
            for part in parts[:-1]:
                child = directory_at(parent, part, "target_input_unavailable", "unsafe_target_config")
                os.close(parent)
                parent = child
            data = read_regular(parent, parts[-1], self.remaining,
                                unavailable="target_input_unavailable", unsafe="unsafe_target_config")
            self.remaining -= len(data)
            return strict_json(data, code)
        except OSError as exc:
            raise ShellError("target_input_unavailable", "Target input is unavailable") from exc
        finally:
            os.close(parent)


def qualify(root_fd, name, budget):
    row = {"target": name, "config_digest": None, "status": "action_required", "exit_code": 3,
           "effects": "none", "core": None,
           "error": error_value("maintenance_unavailable", "Maintenance execution is not available in this Bootstrap slice")}
    try:
        inputs = TargetInputs(root_fd, budget)
        project = inputs.read(name + ".json", "invalid_target_config")
        exact_fields(project, PROJECT_FIELDS, "Project record", "invalid_target_config")
        if (project["schema"] != PROJECT_SCHEMA or project["id"] != name
                or not valid_target(project["id"], allow_all=False)
                or not isinstance(project["catalog"], str) or not project["catalog"]
                or (project["repairs"] is not None and (not isinstance(project["repairs"], str) or not project["repairs"]))
                or not isinstance(project["values"], dict)
                or not isinstance(project["parameter_values_files"], list)
                or any(not isinstance(path, str) or not path for path in project["parameter_values_files"])):
            raise ShellError("invalid_target_config", "Project record fields have invalid types or identity")
        catalog = inputs.read(project["catalog"], "invalid_target_catalogue")
        if (not isinstance(catalog, dict) or catalog.get("schema") != "environment-maintenance/requirements/v1"
                or not isinstance(catalog.get("parameters"), dict) or not isinstance(catalog.get("requirements"), list)):
            raise ShellError("invalid_target_catalogue", "Catalogue header or containers are invalid")
        repairs = None
        if project["repairs"] is not None:
            repairs = inputs.read(project["repairs"], "invalid_target_catalogue")
            if (not isinstance(repairs, dict) or repairs.get("schema") != "environment-maintenance/repairs/v1"
                    or not isinstance(repairs.get("repairs"), list)):
                raise ShellError("invalid_target_catalogue", "Repairs header or container is invalid")
        values_files = []
        for reference in project["parameter_values_files"]:
            value = inputs.read(reference, "invalid_target_parameters")
            if not isinstance(value, dict):
                raise ShellError("invalid_target_parameters", "Parameter values file must be an object")
            values_files.append(value)
        # Unknown secrecy is treated as secret. Execution semantics belong to T027.
        safe_digest = all(isinstance(p, dict) and p.get("secret") is False
                          for p in catalog["parameters"].values())
        if safe_digest:
            snapshot = {"project": project, "catalog": catalog, "repairs": repairs, "values_files": values_files}
            encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
            row["config_digest"] = hashlib.sha256(encoded).hexdigest()
    except ShellError as exc:
        row.update(status="failed", exit_code=4, config_digest=None,
                   error=error_value(exc.code, exc.message, exc.retryable))
    return row


def run(argv):
    root_fd = None
    try:
        config_path, command, explicit, dry_run, _sets = parse_cli(argv)
        config = load_config(Path(os.path.abspath(config_path)))
        requested, source = selector(config, explicit)
        root_fd = open_root(config["projects_root"])
        names = discover(root_fd, requested, config["limits"]["max_targets"])
        rows = [qualify(root_fd, name, config["limits"]["max_target_bytes"]) for name in names]
        exit_code = max((row["exit_code"] for row in rows), default=0)
        result = {"schema": SCHEMA, "command": command, "dry_run": dry_run,
                  "selection": {"requested": requested, "source": source, "resolved": names},
                  "status": {0: "not_required", 3: "action_required", 4: "failed"}[exit_code],
                  "exit_code": exit_code, "targets": rows}
        if len((compact(result) + "\n").encode("utf-8")) > config["limits"]["max_output_bytes"]:
            raise ShellError("limit_exceeded", "Complete report exceeds the configured output limit")
        return emit(result)
    except ShellError as exc:
        # A complete constant diagnostic is emitted even if the configured normal
        # report limit is too small to contain any protocol envelope.
        return emit(reject(exc.code, exc.message, exc.retryable))
    finally:
        if root_fd is not None:
            os.close(root_fd)


def main():
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
