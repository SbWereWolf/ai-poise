"""CLI adapter for the explicit Task process-snapshot migration."""
from __future__ import annotations

import json

from ..composition import task_process_migration_tools
from ..infrastructure.goal_config import strict_json
from ..modules.foundation.errors import PoiseError


def execute(config_path, stream, output):
    try:
        request = strict_json(stream.read().decode("utf-8"))
        result = task_process_migration_tools(config_path).migrate(request)
        code = 0
    except (PoiseError, UnicodeError, RecursionError) as exc:
        result = {"status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return code
