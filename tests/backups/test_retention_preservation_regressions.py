"""Finding-scoped physical preservation checks; accepted 49 oracles remain intact."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
import json
import sqlite3
import threading

import pytest

from poise.application.backups import BackupCommands
from poise.common import PoiseError
from poise.infrastructure.backups import LocalTaskDatabaseBackups
from tests.backups.test_retention_lifecycle import (
    NOW, OLD_A, OLD_B, arrange, consumer_grant, database,
)


def invoke(commands, operation, name):
    try:
        return commands.prune() if operation == 'prune' else commands.remove(name)
    except PoiseError as error:
        return {'success': False, 'reason': str(error)}


@pytest.mark.parametrize('operation', ['prune', 'remove'])
def test_existing_null_grant_preserves_archive_before_refusal(tmp_path, operation):
    _, live, material, commands = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_A
    ordinary = tmp_path / 'backups' / OLD_B
    database(archive, 'protected original')
    database(ordinary, 'expired ordinary')
    sidecar = consumer_grant(archive)
    archive_before, live_before = archive.read_bytes(), live.read_bytes()
    material_before = material.read_bytes()

    control = commands.prune()
    assert control['success'] is True
    assert not ordinary.exists(), 'absent grant must permit ordinary expiry'
    assert archive.read_bytes() == archive_before
    assert any(item['name'] == archive.name and item['reason'] == 'retained_by_user_decision'
               for item in control['retained'])

    sidecar.write_bytes(b'null\n')
    result = invoke(commands, operation, archive.name)

    assert archive.exists(), 'existing null grant was treated as absent and the archive was deleted'
    assert archive.read_bytes() == archive_before, 'uncertain grant changed protected archive bytes'
    assert sidecar.read_bytes() == b'null\n', 'refusal must preserve the undecidable source metadata'
    assert result['success'] is False, 'uncertain retention must refuse before a physical effect'
    assert result.get('reason') or any(
        item['name'] == archive.name and item.get('reason') and item.get('continuation')
        for item in result['rejected'])
    assert live.read_bytes() == live_before
    assert material.read_bytes() == material_before


@pytest.mark.parametrize('bundle', [False, True], ids=['sqlite', 'zip'])
@pytest.mark.parametrize('operation', ['prune', 'remove'])
def test_database_copy_consumer_keeps_source_until_context_release(tmp_path, bundle, operation):
    config, live, material, _ = arrange(tmp_path)
    if bundle:
        manifest = json.loads(config.read_text())
        manifest['paths']['task_materials'] = str(tmp_path / 'user-materials')
        config.write_text(json.dumps(manifest))
        attachment = tmp_path / 'user-materials' / 'current' / 'attachment.bin'
        attachment.parent.mkdir(parents=True)
        attachment.write_bytes(b'attachment original')
    creator = BackupCommands(LocalTaskDatabaseBackups(config),
                             clock=lambda: datetime(2026, 11, 13, tzinfo=timezone.utc))
    created = creator.create()
    expected = 'tasks-backup-20261113T000000000000Z.' + ('zip' if bundle else 'sqlite')
    assert created['name'] == expected
    archive = tmp_path / 'backups' / expected
    archive_before, live_before = archive.read_bytes(), live.read_bytes()
    material_before = material.read_bytes()
    reader = LocalTaskDatabaseBackups(config)
    remover = BackupCommands(LocalTaskDatabaseBackups(config), clock=lambda: NOW)
    entered = threading.Event()

    def delete():
        entered.set()
        return invoke(remover, operation, expected)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with reader.database_copy(expected) as source:
            future = pool.submit(delete)
            assert entered.wait(2), 'fixture worker did not start the real deletion invocation'
            try:
                during = future.result(timeout=0.5)
            except TimeoutError:
                during = None  # Waiting for consumer release is an allowed outcome.
            observed_archive = archive.read_bytes() if archive.exists() else None
            try:
                with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as connection:
                    rows = connection.execute('SELECT value FROM marker').fetchall()
                    integrity = connection.execute('PRAGMA integrity_check').fetchall()
            except sqlite3.Error as error:
                rows, integrity = None, str(error)
        after = future.result(timeout=5)

    assert observed_archive == archive_before, 'active database_copy source archive was deleted or changed'
    assert rows == [('LIVE',)], 'the public consumer could not read its supplied database source'
    assert integrity == [('ok',)]
    assert during is None or during['success'] is False, 'deletion reported success during active use'
    if after['success'] is False:
        assert after.get('reason') or after.get('rejected'), 'busy refusal must describe its cause'
        after = invoke(remover, operation, expected)
    assert after['success'] is True
    assert not archive.exists(), 'deletion did not continue after consumer release'
    assert live.read_bytes() == live_before
    assert material.read_bytes() == material_before
    if bundle:
        assert attachment.read_bytes() == b'attachment original'
