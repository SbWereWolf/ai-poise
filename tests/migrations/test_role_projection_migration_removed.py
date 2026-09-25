import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.tasks.process_migration import MigrationRequest


ROLE_SCHEMA = "task-process-role-migration-1"


def role_projection_request(backup_name: str = "state-backup.sqlite") -> dict:
    return {
        "schema": ROLE_SCHEMA,
        "request_id": "removed-role-projection",
        "backup_name": backup_name,
        "task_ids": ["T1"],
        "authorization": "legacy request must be rejected",
    }


def test_role_projection_schema_is_not_supported_by_domain_contract():
    with pytest.raises(DomainError, match="Unsupported task process migration schema"):
        MigrationRequest.parse(role_projection_request())


def test_role_projection_schema_is_not_supported_by_public_cli(project):
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "poise", "task-process-migrate", "--config", str(project["config_path"])],
        cwd=root,
        env=env,
        input=json.dumps(role_projection_request()),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "Unsupported task process migration schema" in combined
