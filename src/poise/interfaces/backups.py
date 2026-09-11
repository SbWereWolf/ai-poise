from __future__ import annotations

from datetime import datetime, timezone
import json

from ..application.backups import BackupCommands
from ..infrastructure.backups import LocalTaskDatabaseBackups
from ..modules.foundation.errors import PoiseError


HELP = """poise backup commands:
  poise backup list --config PROJECT_JSON
  poise backup create --config PROJECT_JSON
  poise backup restore --config PROJECT_JSON BACKUP_NAME
  poise backup help

Запускайте create и restore только когда ни один агент или процесс не пишет Task DB.
"""


def execute(action, config_path, name, output):
    if action == "help":
        output.write(HELP)
        return 0
    try:
        commands = BackupCommands(
            LocalTaskDatabaseBackups(config_path),
            clock=lambda: datetime.now(timezone.utc),
        )
        if action == "list":
            result = {"backups": commands.list()}
        elif action == "create":
            result = commands.create()
        elif action == "restore":
            result = commands.restore(name)
        else:
            raise PoiseError(f"Unknown backup action: {action}")
        code = 0
    except PoiseError as exc:
        result = {"status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return code
