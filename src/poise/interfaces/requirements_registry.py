"""Bounded JSON CLI adapter for the project Requirements Registry."""
from __future__ import annotations

import json

from ..common import load_config
from ..composition import migrate_requirements, requirements_tools
from ..infrastructure.goal_config import strict_json
from ..modules.foundation.errors import PoiseError


def _packet(value):
    if not isinstance(value, dict) or set(value) != {"operation", "input"}:
        raise PoiseError("Requirements packet requires operation and input")
    if not isinstance(value["input"], dict):
        raise PoiseError("Requirements packet input must be an object")
    return value


def execute(config_path, stream, output):
    try:
        _, config, _ = load_config(config_path)
        raw = stream.read(config["batch"]["max_input_bytes"] + 1)
        if len(raw) > config["batch"]["max_input_bytes"]:
            raise PoiseError("Requirements packet exceeds max_input_bytes")
        packet = _packet(strict_json(raw.decode("utf-8")))
        operation = packet["operation"]
        body = packet["input"]
        if operation == "migrate":
            if body:
                raise PoiseError("Requirements migrate input must be empty")
            result = migrate_requirements(config_path)
        elif operation in ("apply", "query", "import_bootstrap"):
            commands, _ = requirements_tools(config_path)
            result = _command(commands, operation, body)
        else:
            raise PoiseError(f"Unknown Requirements operation: {operation}")
        code = 0
    except (PoiseError, UnicodeError, RecursionError) as exc:
        result = {"status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return code


def _command(commands, operation, body):
    if operation == "apply":
        return {"status": "applied", **commands.apply(body)}
    if operation == "query":
        if set(body) != {"queries"}:
            raise PoiseError("Requirements query input requires queries")
        return {"status": "read_only", **commands.query(body["queries"])}
    if set(body) != {"path", "request_id"}:
        raise PoiseError("Requirements import requires path and request_id")
    return {"status": "imported", **commands.import_bootstrap(body["path"], body["request_id"])}
