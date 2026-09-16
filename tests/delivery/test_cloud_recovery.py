"""Cloud recovery: no live writes, exact transport, preserved conflicts and SQL guards."""
from pathlib import Path
import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tarfile

import pytest

TOOL = Path(__file__).resolve().parents[2] / 'recovery-tools' / 'cloud_recovery.py'


def module():
    spec = importlib.util.spec_from_file_location('cloud_recovery_under_test', TOOL)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def git(repo, *args):
    p = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=repo,
                       text=True, capture_output=True, check=True)
    return p.stdout.strip()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'work/current')
    git(root, 'config', 'user.name', 'Recovery test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'product.py').write_text("print('base')\n")
    (root / '.gitignore').write_text('data/\nHANDOFF.md\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'base')
    git(root, 'branch', 'tasks/keep-history')
    (root / 'HANDOFF.md').write_text('Next: resume existing bug queue.\n')
    (root / 'data').mkdir()
    with sqlite3.connect(root / 'data/tasks.sqlite') as c:
        c.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, goal TEXT NOT NULL)')
        c.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, task_id TEXT REFERENCES tasks(id), text TEXT)')
        c.execute("INSERT INTO tasks VALUES('001','cloud task')")
        c.execute("INSERT INTO notes VALUES(1,'001','cloud note')")
    return root


@pytest.mark.parametrize('compression', ['xz', 'zstd'])
def test_project_roundtrip_preserves_git_refs_db_and_untracked_handoff(project, tmp_path, compression):
    m = module()
    source_db = m.digest(project / 'data/tasks.sqlite')
    old_head = git(project, 'rev-parse', 'HEAD')
    out = tmp_path / 'delivery'
    result = m.pack(project, out, compression)
    assert result['delivery_verified'] is False
    assert m.archive_bytes(out / 'checkpoint.recovery.txt') == Path(result['archive']).read_bytes()
    restored = tmp_path / 'restored'
    m.restore(out / 'checkpoint.recovery.txt', restored)
    repo = restored / 'ai-poise'
    assert git(repo, 'rev-parse', 'HEAD') == old_head
    assert git(repo, 'rev-parse', 'tasks/keep-history') == old_head
    assert m.digest(repo / 'data/tasks.sqlite') == source_db
    assert (repo / 'HANDOFF.md').read_bytes() == (project / 'HANDOFF.md').read_bytes()
    assert not (project / 'RECOVERY-MANIFEST.json').exists()
    assert not git(repo, 'ls-files', 'HANDOFF.md')


def test_comparison_prefers_size_unless_time_cost_is_excessive():
    m = module()
    assert m.choose_compression({'xz': {'bytes': 50, 'seconds': 20}, 'zstd': {'bytes': 70, 'seconds': 5}}) == 'xz'
    assert m.choose_compression({'xz': {'bytes': 50, 'seconds': 700}, 'zstd': {'bytes': 70, 'seconds': 20}}) == 'zstd'
    assert m.choose_compression({'xz': {'bytes': 50, 'seconds': 60}, 'zstd': {'bytes': 70, 'seconds': 5}}) == 'zstd'


def test_corrupt_text_never_creates_destination(project, tmp_path):
    m = module(); out = tmp_path / 'delivery'; m.pack(project, out, 'xz')
    text = out / 'checkpoint.recovery.txt'
    text.write_text(text.read_text()[:-200])
    with pytest.raises((ValueError, m.CheckpointError)):
        m.restore(text, tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()


def test_restore_never_overwrites_a_live_directory(project, tmp_path):
    m = module(); out = tmp_path / 'delivery'; m.pack(project, out, 'xz')
    with pytest.raises(m.CheckpointError):
        m.restore(out / 'checkpoint.recovery.txt', project)
    assert (project / 'product.py').read_text() == "print('base')\n"


@pytest.mark.parametrize('kind', ['traversal', 'symlink', 'duplicate'])
def test_unsafe_archives_are_rejected_before_install(tmp_path, kind):
    m = module(); archive = tmp_path / 'bad.tar.xz'
    with tarfile.open(archive, 'w:xz') as t:
        info = tarfile.TarInfo('../escape' if kind == 'traversal' else 'ai-poise/a')
        if kind == 'symlink': info.type = tarfile.SYMTYPE; info.linkname = '/etc/passwd'; t.addfile(info)
        else:
            info.size = 1; t.addfile(info, io.BytesIO(b'a'))
            if kind == 'duplicate': t.addfile(info, io.BytesIO(b'b'))
    with pytest.raises(m.CheckpointError): m.restore(archive, tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()
    assert not (tmp_path / 'escape').exists()


def test_live_wal_is_rejected_not_ignored(project, tmp_path):
    m = module()
    (project / 'data/tasks.sqlite-wal').write_bytes(b'not a sealed snapshot')
    with pytest.raises(m.CheckpointError): m.pack(project, tmp_path / 'delivery', 'xz')
    assert not (tmp_path / 'delivery').exists()


def divergent_db(project, tmp_path):
    import shutil
    db = tmp_path / 'local.sqlite'; shutil.copy2(project / 'data/tasks.sqlite', db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE tasks SET goal='different local task' WHERE id='001'")
        c.execute("UPDATE notes SET text='different note' WHERE id=1")
    return db


def test_same_id_is_not_assumed_same_task(project, tmp_path):
    m = module(); local = divergent_db(project, tmp_path)
    plan = m.database_plan(project / 'data/tasks.sqlite', local)
    assert plan['task_identity']['001']['classification'] == 'identity_unproved'
    assert plan['requires_sql_plan'] is True


def sql_resolution(m, cloud, local):
    return {
        'cloud_sha256': m.digest(cloud), 'local_sha256': m.digest(local),
        'task_identity': {'001': {'target': 'local-001', 'kind': 'different', 'reason': 'Unrelated requirements; same ID is accidental.'}},
        'statements': [
            {'sql': "INSERT INTO main.tasks SELECT 'local-001', goal FROM incoming.tasks WHERE id='001'", 'parameters': [], 'expected_changes': 1},
            {'sql': "INSERT INTO main.notes SELECT 2, 'local-001', text FROM incoming.notes WHERE id=1", 'parameters': [], 'expected_changes': 1},
        ],
        'checks': [{'sql': 'SELECT count(*) FROM main.tasks', 'parameters': [], 'expected': [[2]]}]
    }


def test_guarded_sql_merge_inserts_distinct_colliding_tasks_and_keeps_sources(project, tmp_path):
    m = module(); cloud = project / 'data/tasks.sqlite'; local = divergent_db(project, tmp_path)
    before = [m.digest(cloud), m.digest(local)]
    result = tmp_path / 'result.sqlite'
    m.merge_database(cloud, local, result, sql_resolution(m, cloud, local))
    with sqlite3.connect(result) as c:
        assert c.execute('SELECT id,goal FROM tasks ORDER BY id').fetchall() == [('001', 'cloud task'), ('local-001', 'different local task')]
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    assert [m.digest(cloud), m.digest(local)] == before


@pytest.mark.parametrize('bad', ['hash', 'changes', 'drop', 'incoming_write', 'missing_identity', 'postcondition'])
def test_sql_merge_rolls_back_failures(project, tmp_path, bad):
    m = module(); cloud = project / 'data/tasks.sqlite'; local = divergent_db(project, tmp_path)
    p = sql_resolution(m, cloud, local)
    if bad == 'hash': p['local_sha256'] = '0' * 64
    if bad == 'changes': p['statements'][0]['expected_changes'] = 0
    if bad == 'drop': p['statements'][0]['sql'] = 'DROP TABLE main.tasks'
    if bad == 'incoming_write': p['statements'][0]['sql'] = "UPDATE incoming.tasks SET goal='bad'"
    if bad == 'missing_identity': p['task_identity'] = {}
    if bad == 'postcondition': p['checks'][0]['expected'] = [[999]]
    output = tmp_path / 'refused.sqlite'
    with pytest.raises((m.CheckpointError, sqlite3.DatabaseError)): m.merge_database(cloud, local, output, p)
    assert not output.exists()


def test_merge_dry_run_never_mutates_inputs_or_creates_destination(project, tmp_path):
    import shutil
    m = module(); local = tmp_path / 'local'; shutil.copytree(project, local)
    (local / 'product.py').write_text("print('local WIP')\n")
    before = m.digest(local / 'product.py')
    result = m.merge_projects(project, local, tmp_path / 'merged', apply=False)
    assert result['applied'] is False
    assert not (tmp_path / 'merged').exists()
    assert m.digest(local / 'product.py') == before


def test_merge_saves_local_wip_as_real_git_history_without_source_writes(project, tmp_path):
    import shutil
    m = module(); local = tmp_path / 'local'; shutil.copytree(project, local)
    (local / 'product.py').write_text("print('local WIP')\n")
    original = git(local, 'rev-parse', 'HEAD')
    result = m.merge_projects(project, local, tmp_path / 'merged', apply=True)
    assert result['status'] == 'merged'
    assert git(local, 'rev-parse', 'HEAD') == original
    assert git(local, 'status', '--porcelain').startswith('M')
    assert (tmp_path / 'merged/product.py').read_text() == "print('local WIP')\n"
    assert git(tmp_path / 'merged', 'rev-parse', 'refs/recovery/local-wip') != original


def test_merge_conflicting_data_is_preserved_not_overwritten(project, tmp_path):
    import shutil
    m = module(); local = tmp_path / 'local'; shutil.copytree(project, local)
    with sqlite3.connect(local / 'data/tasks.sqlite') as c: c.execute("UPDATE tasks SET goal='local' WHERE id='001'")
    before = m.digest(project / 'data/tasks.sqlite')
    result = m.merge_projects(project, local, tmp_path / 'merged', apply=True)
    assert result['status'] == 'needs_decisions'
    assert m.digest(tmp_path / 'merged/data/tasks.sqlite') == before
    assert (tmp_path / 'merged/.recovery-sources/local/data/tasks.sqlite').exists()
    assert 'data/tasks.sqlite' in result['data_conflicts']


def test_cloud_wip_and_local_commit_are_both_preserved(project, tmp_path):
    import shutil
    m = module(); local = tmp_path / 'local'; shutil.copytree(project, local)
    (project / 'product.py').write_text("print('cloud WIP')\n")
    (local / 'other.py').write_text('local = True\n')
    git(local, 'add', 'other.py'); git(local, 'commit', '-qm', 'local addition')
    result = m.merge_projects(project, local, tmp_path / 'merged', apply=True)
    assert result['status'] == 'merged'
    assert (tmp_path / 'merged/product.py').read_text() == "print('cloud WIP')\n"
    assert (tmp_path / 'merged/other.py').read_text() == 'local = True\n'
    assert git(project, 'status', '--porcelain').startswith('M')


def test_git_conflicts_leave_both_parents_in_new_candidate(project, tmp_path):
    import shutil
    m = module(); local = tmp_path / 'local'; shutil.copytree(project, local)
    (project / 'product.py').write_text('cloud conflict\n')
    (local / 'product.py').write_text('local conflict\n')
    result = m.merge_projects(project, local, tmp_path / 'merged', apply=True)
    assert result['status'] == 'needs_decisions'
    assert result['git_conflicts'] == ['product.py']
    assert (tmp_path / 'merged/.git/MERGE_HEAD').exists()
    assert (project / 'product.py').read_text() == 'cloud conflict\n'
    assert (local / 'product.py').read_text() == 'local conflict\n'


def test_staged_new_file_is_in_wip_snapshot(project, tmp_path):
    import shutil
    m = module(); local = tmp_path / 'local'; shutil.copytree(project, local)
    (local / 'new.py').write_text('new = 1\n'); git(local, 'add', 'new.py')
    result = m.merge_projects(project, local, tmp_path / 'merged', apply=True)
    assert result['status'] == 'merged'
    assert git(tmp_path / 'merged', 'show', 'HEAD:new.py') == 'new = 1'
    assert (tmp_path / 'merged/.recovery-sources/local-staged.patch.txt').read_text()


def test_payload_tampering_fails_manifest_verification(project, tmp_path):
    m = module(); out = tmp_path / 'delivery'; m.pack(project, out, 'xz')
    bad = tmp_path / 'tampered.tar.xz'
    with tarfile.open(out / 'checkpoint.tar.xz', 'r:xz') as source, tarfile.open(bad, 'w:xz') as target:
        for entry in source:
            if entry.isfile():
                data = source.extractfile(entry).read()
                if entry.name == 'ai-poise/product.py': data = b'not the saved product\n'; entry.size = len(data)
                target.addfile(entry, io.BytesIO(data))
            else: target.addfile(entry)
    with pytest.raises(m.CheckpointError): m.restore(bad, tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()


def test_explicit_unpack_limit_is_enforced(project, tmp_path):
    m = module(); out = tmp_path / 'delivery'; m.pack(project, out, 'xz')
    with pytest.raises(m.CheckpointError): m.restore(out / 'checkpoint.tar.xz', tmp_path / 'refused', max_unpacked_bytes=1)
    assert not (tmp_path / 'refused').exists()


def test_explicit_fast_compression_preserves_full_restore(project, tmp_path):
    m = module()
    result = m.pack(project, tmp_path / 'packed', 'xz', xz_preset=1)
    assert result['comparison']['xz']['argv'] == ['xz', '-1', '-T1', '-c']
    restored = m.restore(tmp_path / 'packed/checkpoint.recovery.txt', tmp_path / 'restored')
    assert restored['head'] == git(project, 'rev-parse', 'HEAD')
    assert (tmp_path / 'restored/ai-poise/data/tasks.sqlite').read_bytes() == (project / 'data/tasks.sqlite').read_bytes()


@pytest.mark.parametrize('preset', [-1, 10, True, 1.5])
def test_invalid_compression_preset_does_not_publish(project, tmp_path, preset):
    m = module()
    with pytest.raises(m.CheckpointError, match='preset'):
        m.pack(project, tmp_path / 'packed', 'xz', xz_preset=preset)
    assert not (tmp_path / 'packed').exists()
