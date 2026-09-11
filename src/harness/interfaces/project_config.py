"""Bounded JSON CLI adapter for the project configuration application route."""
import json
from ..composition import project_config_tools
from ..infrastructure.projects import ProjectSettings
from ..infrastructure.goal_config import strict_json
from ..modules.foundation.errors import HarnessError


def execute(settings_path, stream, output):
    settings = None
    try:
        settings = ProjectSettings(settings_path)
        raw = stream.read(settings.raw["max_input_bytes"] + 1)
        if len(raw) > settings.raw["max_input_bytes"]:
            raise HarnessError("Project config input limit exceeded; no mutation applied")
        result = project_config_tools(settings_path).apply(strict_json(raw.decode("utf-8")))
        category = "success"
    except (HarnessError, UnicodeError, RecursionError) as exc:
        result = {"status": "rejected", "reason": str(exc)}
        category = "rejected"
    output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 2 if settings is None else settings.raw["exit_codes"][category]
