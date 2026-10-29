"""Bounded JSON CLI adapter for the project Requirements Registry."""
from __future__ import annotations

import json

from ..common import load_config
from ..composition import requirements_tools
from ..infrastructure.goal_config import strict_json
from ..modules.foundation.errors import PoiseError
from ..modules.requirements_registry.crud import RequirementsCrudError, is_crud_query_batch


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
        if operation in ("apply", "command", "import_bootstrap", "migrate"):
            raise PoiseError(
                'Запись требований требует действующей Task. Передайте этот пакет как '
                'work.operation=requirements через poise runtime --settings "$runtime_settings", '
                'указав существующий файл настроек runtime.'
            )
        if operation == "query":
            commands, _ = requirements_tools(config_path, read_only=True)
            result = _command(commands, operation, body)
        else:
            raise PoiseError(f"Unknown Requirements operation: {operation}")
        code = 0
    except RequirementsCrudError as exc:
        result = exc.response()
        code = 2
    except (PoiseError, UnicodeError, RecursionError) as exc:
        result = {"status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return code


def _command(commands, operation, body):
    if set(body) != {"queries"}:
        if is_crud_query_batch(body.get("queries")):
            raise RequirementsCrudError("invalid_packet")
        raise PoiseError("Requirements query input requires queries")
    return {"status": "read_only", **commands.query(body["queries"])}
