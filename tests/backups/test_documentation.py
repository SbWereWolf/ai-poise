from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backup_documentation_matches_public_cli_and_storage_scope():
    # BACKUP_FOCUSED runs before the workflow permits docs/** changes. The
    # separately registered BACKUP_DOCS method enables the content assertions
    # only at the documentation stage.
    if os.environ.get("POISE_REQUIRE_BACKUP_DOCS") != "1":
        assert (ROOT / "README.md").is_file()
        return

    guide = ROOT / "docs/task-db-backups.md"
    assert guide.is_file()
    text = guide.read_text(encoding="utf-8").lower()
    for fragment in (
        "poise backup list",
        "poise backup create",
        "poise backup restore",
        "poise backup help",
        "pragma integrity_check",
        "backups/",
        "ctime",
        "task",
        "sprint",
        "agent",
        "process",
    ):
        assert fragment in text
    assert "task-db-backups.md" in (ROOT / "README.md").read_text(encoding="utf-8")
