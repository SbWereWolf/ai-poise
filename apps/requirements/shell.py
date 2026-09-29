#!/usr/bin/env python3
"""Standalone Requirements JSON provider with app-owned configuration."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

from poise.application.requirements_registry import RequirementsCommands
from poise.modules.foundation.strict_json import strict_json
from poise.infrastructure.requirements_registry import RequirementsStore
from poise.modules.foundation.errors import PoiseError
from poise.modules.tasks.allocation import TaskIdPolicy


SCHEMA = "stride-requirements-config/v1"


def _exact(value, keys, where):
    required = set(keys)
    if not isinstance(value, dict):
        raise PoiseError(f"{where}: required object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing or unknown:
        raise PoiseError(
            f"{where}: required exact fields {sorted(required)}; "
            f"missing {missing}; unknown {unknown}"
        )


def _path_text(value, where):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise PoiseError(f"{where}: required nonempty path")
    return value


def _positive_integer(value, where):
    if type(value) is not int or value <= 0:
        raise PoiseError(f"{where}: required positive integer")
    return value


def _positive_number(value, where):
    if type(value) not in (int, float) or isinstance(value, bool) or value <= 0 or not math.isfinite(value):
        raise PoiseError(f"{where}: required positive finite number")
    return value


def load_requirements_config(config_path):
    path = Path(config_path).resolve()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PoiseError(f"Requirements config cannot be read: {exc}") from exc
    data = strict_json(raw)
    _exact(data, {"schema", "paths", "batch", "lock", "task_ids"}, "Requirements config")
    if data["schema"] != SCHEMA:
        raise PoiseError("Requirements config schema is unsupported")

    _exact(data["paths"], {"state", "database", "lock"}, "Requirements config paths")
    _exact(data["batch"], {"max_input_bytes", "max_items"}, "Requirements config batch")
    _exact(data["lock"], {"seconds", "poll_seconds"}, "Requirements config lock")

    state_value = _path_text(data["paths"]["state"], "Requirements config paths.state")
    database_value = _path_text(data["paths"]["database"], "Requirements config paths.database")
    lock_value = _path_text(data["paths"]["lock"], "Requirements config paths.lock")

    state = Path(state_value)
    if not state.is_absolute():
        state = path.parent / state
    state = state.resolve()

    database_rel = Path(database_value)
    lock_rel = Path(lock_value)
    if database_rel.is_absolute() or lock_rel.is_absolute():
        raise PoiseError("Requirements config database/lock paths must be relative to state")
    database = (state / database_rel).resolve()
    lock = (state / lock_rel).resolve()
    if not database.is_relative_to(state) or not lock.is_relative_to(state):
        raise PoiseError("Requirements config database/lock paths must stay inside state")
    if database == lock:
        raise PoiseError("Requirements config database and lock must be distinct")

    max_input_bytes = _positive_integer(data["batch"]["max_input_bytes"], "Requirements config batch.max_input_bytes")
    max_items = _positive_integer(data["batch"]["max_items"], "Requirements config batch.max_items")
    lock_seconds = _positive_number(data["lock"]["seconds"], "Requirements config lock.seconds")
    poll_seconds = _positive_number(data["lock"]["poll_seconds"], "Requirements config lock.poll_seconds")
    TaskIdPolicy.parse(data["task_ids"])

    return {
        "database": database,
        "lock": lock,
        "max_input_bytes": max_input_bytes,
        "max_items": max_items,
        "lock_seconds": lock_seconds,
        "poll_seconds": poll_seconds,
    }


def _packet(value):
    if not isinstance(value, dict) or set(value) != {"operation", "input"}:
        raise PoiseError("Requirements packet requires operation and input")
    if not isinstance(value["input"], dict):
        raise PoiseError("Requirements packet input must be an object")
    return value


def execute(config_path, stream, output):
    try:
        config = load_requirements_config(config_path)
        raw = stream.read(config["max_input_bytes"] + 1)
        if len(raw) > config["max_input_bytes"]:
            raise PoiseError("Requirements packet exceeds max_input_bytes")
        packet = _packet(strict_json(raw.decode("utf-8")))
        commands = RequirementsCommands(
            RequirementsStore(
                config["database"],
                config["lock"],
                config["lock_seconds"],
                config["poll_seconds"],
            ),
            config["max_items"],
        )
        operation = packet["operation"]
        body = packet["input"]
        if operation == "apply":
            result = {"status": "applied", **commands.apply(body)}
        elif operation == "query":
            if set(body) != {"queries"}:
                raise PoiseError("Requirements query input requires queries")
            result = {"status": "read_only", **commands.query(body["queries"])}
        elif operation == "import_bootstrap":
            if set(body) != {"path", "request_id"}:
                raise PoiseError("Requirements import requires path and request_id")
            result = {
                "status": "imported",
                **commands.import_bootstrap(body["path"], body["request_id"]),
            }
        else:
            raise PoiseError(f"Unknown Requirements operation: {operation}")
        code = 0
    except (PoiseError, UnicodeError, RecursionError) as exc:
        result = {"status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return code


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="requirements")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    return execute(args.config, sys.stdin.buffer, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
