"""Joint Task DB/material snapshots through the existing backup boundary."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from poise.modules.foundation.errors import PoiseError
from .helpers import (
    backup_commands, backup_directory, configured_path, create_database,
    integrity, json_output, live_database, marker, run_cli,
)


PAYLOAD = b"\x00\xff\x80ERP attachment\n"


def material_store(project, absolute=False):
    config = json.loads(project['config_path'].read_text())
    value = str(project['root'] / 'external-materials') if absolute else 'user-materials'
    config['paths']['task_materials'] = value
    project['config_path'].write_text(json.dumps(config))
    root = configured_path(project['config_path'], 'task_materials')
    # Literal fixture project identity, independent of production path allocation.
    namespace = root / hashlib.sha256(b'demo').hexdigest()
    foreign = root / hashlib.sha256(b'other-project').hexdigest() / 'keep'
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b'foreign unchanged')
    return root, namespace, foreign


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.mark.parametrize('absolute', [False, True])
@pytest.mark.parametrize('missing_db', [False, True])
def test_joint_snapshot_restores_exact_project_materials(project, absolute, missing_db):
    database = live_database(project)
    create_database(database, 'snapshot')
    root, namespace, foreign = material_store(project, absolute)
    write(namespace / 'task-a/request/0/0', PAYLOAD)
    write(namespace / 'task-b/request/1/0', b'second attachment')
    sentinel = database.parent / 'standalone/keep.txt'
    write(sentinel, b'not a comment material')

    created = backup_commands(project).create()
    archive = backup_directory(project) / created['name']
    assert created['backup_size_bytes'] == archive.stat().st_size
    assert created['live_size_bytes'] == database.stat().st_size
    shutil.rmtree(namespace)
    write(namespace / 'post-snapshot', b'new data')
    database.unlink()
    if not missing_db:
        create_database(database, 'replacement')

    assert backup_commands(project).restore(created['name']) == {
        'success': True, 'name': created['name'],
    }
    assert marker(database) == 'snapshot'
    assert integrity(database) == 'ok'
    assert sorted(p.relative_to(namespace).as_posix() for p in namespace.rglob('*') if p.is_file()) == [
        'task-a/request/0/0', 'task-b/request/1/0',
    ]
    assert (namespace / 'task-a/request/0/0').read_bytes() == PAYLOAD
    assert (namespace / 'task-b/request/1/0').read_bytes() == b'second attachment'
    assert foreign.read_bytes() == b'foreign unchanged'
    assert sentinel.read_bytes() == b'not a comment material'
    assert created['name'].endswith('.zip')
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read('task-materials/task-a/request/0/0') == PAYLOAD
        assert not any('other-project' in name or foreign.parent.name in name for name in bundle.namelist())
    assert sorted(p.name for p in backup_directory(project).iterdir()) == [created['name']]


@pytest.mark.parametrize('initial_namespace', ['missing', 'empty'])
def test_empty_snapshot_replaces_later_materials(project, initial_namespace):
    database = live_database(project)
    create_database(database, 'empty snapshot')
    _, namespace, foreign = material_store(project)
    if initial_namespace == 'empty':
        namespace.mkdir()
    created = backup_commands(project).create()
    write(namespace / 'later', b'discard later')
    backup_commands(project).restore(created['name'])
    assert not list(namespace.rglob('*'))
    assert foreign.read_bytes() == b'foreign unchanged'


def test_material_read_error_is_not_an_empty_snapshot(project, monkeypatch):
    database = live_database(project)
    create_database(database, 'before')
    _, namespace, _ = material_store(project)
    write(namespace / 'file', PAYLOAD)
    original = Path.open

    def fail_material(path, *args, **kwargs):
        if path == namespace / 'file':
            raise PermissionError('fixture read denied')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_material)
    with pytest.raises(PoiseError, match='read denied'):
        backup_commands(project).create()
    assert not backup_directory(project).exists() or not list(backup_directory(project).iterdir())


@pytest.mark.parametrize('fault', ['corrupt', 'missing-db', 'missing-materials', 'sqlite-only'])
def test_invalid_bundle_preserves_both_targets(project, fault):
    database = live_database(project)
    create_database(database, 'before')
    _, namespace, foreign = material_store(project)
    write(namespace / 'file', PAYLOAD)
    before = database.read_bytes()
    archive = backup_directory(project) / 'invalid.zip'
    archive.parent.mkdir(parents=True)
    if fault == 'corrupt':
        archive.write_bytes(b'broken ZIP')
    elif fault == 'sqlite-only':
        create_database(archive, 'db without materials')
    else:
        with zipfile.ZipFile(archive, 'w') as bundle:
            if fault != 'missing-db':
                bundle.write(database, 'task.sqlite')
            if fault != 'missing-materials':
                bundle.writestr('task-materials/', b'')
    with pytest.raises(PoiseError):
        backup_commands(project).restore(archive.name)
    assert database.read_bytes() == before
    assert (namespace / 'file').read_bytes() == PAYLOAD
    assert foreign.read_bytes() == b'foreign unchanged'
    assert sorted(p.name for p in database.parent.iterdir() if p.name.startswith('.')) == []


@pytest.mark.parametrize('missing_db', [False, True])
def test_database_install_failure_rolls_back_materials(project, monkeypatch, missing_db):
    import poise.infrastructure.backups as subject

    database = live_database(project)
    create_database(database, 'snapshot')
    root, namespace, foreign = material_store(project)
    write(namespace / 'file', PAYLOAD)
    created = backup_commands(project).create()
    database.unlink()
    if not missing_db:
        create_database(database, 'before attempt')
    before = database.read_bytes() if database.exists() else None
    write(namespace / 'file', b'before attempt')
    original = subject.os.replace
    observed = []

    def fail_install(source, destination):
        if Path(destination) == database:
            observed.append((namespace / 'file').read_bytes())
            raise OSError('fixture DB install failed')
        return original(source, destination)

    monkeypatch.setattr(subject.os, 'replace', fail_install)
    with pytest.raises(PoiseError, match='fixture DB install failed'):
        backup_commands(project).restore(created['name'])
    assert observed == [PAYLOAD]
    assert database.exists() is (not missing_db)
    if before is not None:
        assert database.read_bytes() == before
    assert (namespace / 'file').read_bytes() == b'before attempt'
    assert foreign.read_bytes() == b'foreign unchanged'
    assert sorted(p.name for p in root.iterdir()) == sorted([namespace.name, foreign.parent.name])
    assert not [p for p in database.parent.iterdir() if p.name.startswith('.')]


def test_sqlite_only_restore_creates_missing_target(project):
    database = live_database(project)
    create_database(database, 'db-only snapshot')
    created = backup_commands(project).create()
    database.unlink()
    backup_commands(project).restore(created['name'])
    assert marker(database) == 'db-only snapshot'
    assert integrity(database) == 'ok'


def test_cli_restores_missing_database_and_materials_from_one_backup(project):
    database = live_database(project)
    create_database(database, 'cli snapshot')
    _, namespace, foreign = material_store(project)
    write(namespace / 'file', PAYLOAD)
    created = json_output(run_cli('backup', 'create', '--config', str(project['config_path'])))
    database.unlink()
    shutil.rmtree(namespace)
    result = json_output(run_cli('backup', 'restore', '--config', str(project['config_path']), created['name']))
    assert result == {'success': True, 'name': created['name']}
    assert marker(database) == 'cli snapshot'
    assert (namespace / 'file').read_bytes() == PAYLOAD
    assert foreign.read_bytes() == b'foreign unchanged'
