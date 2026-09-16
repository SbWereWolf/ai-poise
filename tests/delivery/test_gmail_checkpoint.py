"""Full stopped-source Gmail snapshots, distinct readback and restore guards."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

TOOL = Path(__file__).resolve().parents[2] / 'recovery-tools/gmail_checkpoint.py'


def module():
    spec = importlib.util.spec_from_file_location('gmail_checkpoint_tests', TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=root).decode().strip()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'source'; root.mkdir()
    git(root, 'init', '-q', '-b', 'work/repair')
    git(root, 'config', 'user.name', 'Backup test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'product.py').write_text('original = 1\n')
    (root / '.gitignore').write_text('state/\nWORKLOG.md\n')
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'base')
    git(root, 'branch', 'preserved/work')
    (root / 'product.py').write_text('wip = 2\n')
    (root / 'WORKLOG.md').write_text('RED done; implementation pending.\n')
    (root / 'state').mkdir()
    with sqlite3.connect(root / 'state/tasks.sqlite') as db:
        db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT NOT NULL)')
        db.execute("INSERT INTO tasks VALUES ('0150', 'active')")
    (root / '__pycache__').mkdir(); (root / '__pycache__/disposable.pyc').write_bytes(b'cache')
    return root


def prepare(m, project, output):
    return m.prepare(project, output, 'test-001', 'test@example.invalid', 'Implement 0150')


def test_prepare_and_distinct_readback_restore_full_state(project, tmp_path):
    m = module(); out = tmp_path / 'out'
    head = git(project, 'rev-parse', 'HEAD')
    db_bytes = (project / 'state/tasks.sqlite').read_bytes()
    meta = prepare(m, project, out)
    assert meta['delivery_verified'] is False
    assert meta['excluded_cache_paths'] == ['__pycache__/']
    request = json.loads((out / 'SEND-REQUEST.json').read_text())
    assert request['to'] == 'test@example.invalid'
    assert str(out / 'checkpoint.recovery.txt') in request['attachment_files']
    assert 'RED done; implementation pending.' in (out / 'MAIL.txt').read_text()
    readback = tmp_path / 'downloaded.txt'
    shutil.copyfile(out / 'checkpoint.recovery.txt', readback)
    receipt = m.confirm(out, readback, '1a0abcdef', tmp_path / 'restore')
    restored = tmp_path / 'restore/ai-poise'
    assert receipt['readback_verified'] is True
    assert receipt['receipt_stored_in_gmail'] is False
    assert receipt['prepared_at_utc'] == meta['prepared_at_utc']
    assert git(restored, 'rev-parse', 'HEAD') == head
    assert git(restored, 'rev-parse', 'preserved/work') == head
    assert (restored / 'product.py').read_text() == 'wip = 2\n'
    assert (restored / 'state/tasks.sqlite').read_bytes() == db_bytes
    assert (restored / 'WORKLOG.md').read_text() == 'RED done; implementation pending.\n'
    assert not (restored / '__pycache__').exists()
    assert (project / '__pycache__/disposable.pyc').exists()


def test_confirm_rejects_outgoing_file_as_readback(project, tmp_path):
    m = module(); out = tmp_path / 'out'; prepare(m, project, out)
    with pytest.raises(m.CheckpointError, match='separate downloaded file'):
        m.confirm(out, out / 'checkpoint.recovery.txt', '1a0abcdef', tmp_path / 'restore')
    assert not (out / 'GMAIL-VERIFIED.json').exists()


def test_corruption_and_existing_destination_rejected(project, tmp_path):
    m = module(); out = tmp_path / 'out'; meta = prepare(m, project, out)
    downloaded = tmp_path / 'readback.txt'; downloaded.write_bytes(b'incomplete')
    with pytest.raises(m.CheckpointError, match='SHA-256 mismatch'):
        m.restore_from_gmail(meta, downloaded, tmp_path / 'restore')
    assert not (tmp_path / 'restore').exists()
    shutil.copyfile(out / 'checkpoint.recovery.txt', downloaded)
    with pytest.raises(m.CheckpointError, match='already exists'):
        m.restore_from_gmail(meta, downloaded, project)
    assert (project / 'product.py').read_text() == 'wip = 2\n'


@pytest.mark.parametrize('unsafe', ['wal', 'symlink', 'empty_journal'])
def test_refuse_unsafe_snapshot_without_promoting(project, tmp_path, unsafe):
    m = module(); out = tmp_path / 'out'
    if unsafe == 'wal':
        (project / 'state/tasks.sqlite-wal').write_bytes(b'live')
    elif unsafe == 'symlink':
        (project / 'linked').symlink_to(project / 'product.py')
    else:
        (project / 'WORKLOG.md').write_text('')
    with pytest.raises(m.CheckpointError):
        prepare(m, project, out)
    assert not out.exists()


def test_tracked_cache_named_sources_are_preserved(project):
    m = module()
    (project / '__pycache__/important.txt').write_text('authored')
    git(project, 'add', '__pycache__/important.txt')
    files, omitted = m.selected_inventory(project)
    assert '__pycache__/important.txt' in files
    assert '__pycache__/disposable.pyc' in omitted


def test_deadline_uses_snapshot_time_not_late_readback():
    m = module(); stamp = datetime(2026, 9, 17, tzinfo=timezone.utc)
    receipt = {'readback_verified': True, 'message_id': '1a0abcdef',
               'prepared_at_utc': stamp.isoformat(),
               'verified_at_utc': (stamp + timedelta(seconds=120)).isoformat()}
    assert m.backup_due(receipt, stamp + timedelta(seconds=599))['due'] is False
    assert m.backup_due(receipt, stamp + timedelta(seconds=600))['due'] is True
    assert m.backup_due(None, stamp)['due'] is True
