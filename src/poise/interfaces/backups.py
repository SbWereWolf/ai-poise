from __future__ import annotations

from datetime import datetime, timezone
import json

from ..application.backups import BackupCommands
from ..infrastructure.backups import LocalTaskDatabaseBackups
from ..infrastructure.backup_authority import TaskMaterialBackupAuthority
from ..modules.foundation.errors import PoiseError


HELP = """poise backup commands:
  poise backup list --config PROJECT_JSON
  poise backup create --config PROJECT_JSON
  poise backup restore --config PROJECT_JSON BACKUP_NAME
  poise backup remove --config PROJECT_JSON BACKUP_NAME
  poise backup prune --config PROJECT_JSON
  poise backup retain --config PROJECT_JSON BACKUP_NAME --until UTC_TIMESTAMP
    --authorization-ref task-feedback:TASK_ID:COMMENT_ID --expected-digest SHA256 --request-id ID
  poise backup help

При настроенном paths.task_materials копия ZIP содержит Task DB и материалы проекта.
Без paths.task_materials создаётся SQLite-копия Task DB.
Запускайте create и restore после остановки всех писателей БД и материалов:
ни один агент или процесс не пишет Task DB, все соединения с БД закрыты.
restore создаёт целевой файл БД, если он отсутствует.
"""


def execute(action, config_path, name, output, *, retention=None):
    if action == "help":
        output.write(HELP)
        return 0
    try:
        commands = BackupCommands(
            LocalTaskDatabaseBackups(config_path),
            clock=lambda: datetime.now(timezone.utc),
            authority=TaskMaterialBackupAuthority(config_path),
        )
        if action == "list":
            result = {"backups": commands.list()}
        elif action == "create":
            result = commands.create()
        elif action == "restore":
            result = commands.restore(name)
        elif action == 'remove':
            result = commands.remove(name)
        elif action == 'prune':
            result = commands.prune()
        elif action == 'retain':
            result = commands.retain(name, valid_until=retention['until'],
                expected_digest=retention['expected_digest'],
                authorization_ref=retention['authorization_ref'], request_id=retention['request_id'])
        else:
            raise PoiseError(f"Unknown backup action: {action}")
        code = 0 if result.get('success', True) else 2
    except PoiseError as exc:
        result = {"status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return code
