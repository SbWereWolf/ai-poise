#!/usr/bin/env python3
"""Standalone Bootstrap shell foundation owned by apps/bootstrap."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys

SCHEMA = "stride-bootstrap/v1"
CONFIG_SCHEMA = "stride-bootstrap-config/v1"
CONFIG_FIELDS = {"schema", "projects_root", "default_target", "target_env", "limits"}
LIMIT_FIELDS = {"max_targets", "max_target_bytes", "max_output_bytes", "target_timeout_seconds", "run_timeout_seconds"}
TARGET_RE = re.compile(r"^[^/\\\x00]+$")


class ShellError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def reject(code, message, retryable=False):
    return {"schema": SCHEMA, "status": "rejected", "exit_code": 2,
            "error": {"code": code, "message": message, "retryable": retryable}, "targets": []}


def exact_fields(value, expected, label):
    if not isinstance(value, dict):
        raise ShellError("invalid_bootstrap_config", f"{label} must be an object")
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise ShellError("invalid_bootstrap_config", f"{label}: missing {missing}; unknown {unknown}")


def load_json(path: Path):
    def hook(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ShellError("invalid_bootstrap_config", f"Bootstrap config has duplicate key: {key}")
            out[key] = value
        return out
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text, object_pairs_hook=hook, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except ShellError:
        raise
    except Exception as exc:
        raise ShellError("invalid_bootstrap_config", f"Bootstrap config is unreadable: {exc}") from exc


def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ShellError("invalid_bootstrap_config", f"Bootstrap config {name} must be a positive integer")
    return value


def positive_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ShellError("invalid_bootstrap_config", f"Bootstrap config {name} must be a positive finite number")
    return float(value)


def valid_target(value, *, allow_all=True):
    if not isinstance(value, str) or value == "" or not TARGET_RE.fullmatch(value) or value in {".", ".."}:
        return False
    if not allow_all and value == "all":
        return False
    return True


def load_config(path: Path):
    raw = load_json(path)
    exact_fields(raw, CONFIG_FIELDS, "Bootstrap config")
    if raw["schema"] != CONFIG_SCHEMA:
        raise ShellError("invalid_bootstrap_config", f"Bootstrap config schema must be {CONFIG_SCHEMA}")
    root = raw["projects_root"]
    if not isinstance(root, str) or not root:
        raise ShellError("invalid_bootstrap_config", "Bootstrap config projects_root must be a nonempty string")
    root_path = Path(root)
    if not root_path.is_absolute():
        root_path = (path.parent / root_path).resolve()
    else:
        root_path = root_path.resolve()
    default = raw["default_target"]
    if not valid_target(default):
        raise ShellError("invalid_bootstrap_config", "Bootstrap config default_target is invalid")
    env_name = raw["target_env"]
    if env_name is not None and (not isinstance(env_name, str) or not env_name):
        raise ShellError("invalid_bootstrap_config", "Bootstrap config target_env must be a nonempty string or null")
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
    if len(argv) < 3 or argv[0] != "--config" or not argv[1]:
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
            dry_run = True; i += 1; continue
        if token in {"--target", "--set"}:
            if i + 1 >= len(argv):
                raise ShellError("invalid_request", f"Missing value for {token}")
            value = argv[i + 1]
            if token == "--target":
                if target is not None:
                    raise ShellError("invalid_request", "Duplicate --target")
                if value == "":
                    raise ShellError("invalid_selector", "Target selector is empty")
                if not valid_target(value):
                    raise ShellError("invalid_selector", "Target selector is invalid")
                target = value
            else:
                if "=" not in value or value.startswith("="):
                    raise ShellError("invalid_request", "--set requires NAME=VALUE")
                sets.append(value)
            i += 2; continue
        raise ShellError("invalid_request", f"Unknown Bootstrap argument: {token}")
    return config, command, target, dry_run, sets


def selector(config, explicit):
    if explicit is not None:
        return explicit, "argument"
    env_name = config["target_env"]
    if env_name is not None and env_name in os.environ:
        value = os.environ[env_name]
        if value == "" or not valid_target(value):
            raise ShellError("invalid_selector", "Bootstrap target environment selector is invalid")
        return value, "environment"
    return config["default_target"], "configuration"


def run(argv):
    try:
        config_path, command, explicit, dry_run, _sets = parse_cli(argv)
        config = load_config(config_path.resolve())
        root = config["projects_root"]
        if not root.is_dir():
            raise ShellError("projects_unavailable", "Configured projects_root is unavailable")
        requested, source = selector(config, explicit)
        # T022 owns the standalone process/config boundary only. The empty-root case is
        # contract-complete and side-effect free; dynamic record discovery is extended by T026.
        entries = [p for p in root.iterdir() if p.is_file() and p.suffix == ".json"]
        if entries:
            raise ShellError("invalid_request", "Bootstrap project target execution is not available in this shell slice")
        if requested != "all":
            raise ShellError("target_not_found", "Requested target does not exist")
        result = {"schema": SCHEMA, "command": command, "dry_run": dry_run,
                  "selection": {"requested": requested, "source": source, "resolved": []},
                  "status": "not_required", "exit_code": 0, "targets": []}
        print(compact(result))
        return 0
    except ShellError as exc:
        print(compact(reject(exc.code, exc.message, exc.retryable)))
        return 2


def main():
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
