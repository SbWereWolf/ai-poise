from datetime import datetime, timedelta, timezone

import pytest

from poise.common import PoiseError
from poise.modules.backups.domain import generated_backup_name


def test_generated_backup_name_projects_observed_time_to_utc():
    observed_at = datetime(
        2026,
        9,
        12,
        17,
        34,
        56,
        123456,
        tzinfo=timezone(timedelta(hours=5)),
    )

    assert generated_backup_name("tasks.sqlite", observed_at) == (
        "tasks-backup-20260912T123456123456Z.sqlite"
    )


def test_generated_backup_name_rejects_naive_time():
    with pytest.raises(PoiseError, match="timezone-aware"):
        generated_backup_name("tasks.sqlite", datetime(2026, 9, 12, 12, 34, 56))
