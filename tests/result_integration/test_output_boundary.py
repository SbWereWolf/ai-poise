"""Real Git consumers retain complete inventories and preserve foreign data."""
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

from conftest import git
from poise.infrastructure.result_integration import RuntimeResultIntegration
from .helpers import integration_input, prepare_completed_task, request


def raw_git(repository, *args):
    return subprocess.run(['git', '--no-optional-locks', '-C', str(repository), *args],
                          capture_output=True, check=False)


def adapter():
    return RuntimeResultIntegration(SimpleNamespace(
        cfg={'limits': {'git_seconds': 10, 'preview_chars': 24}}))


def repository(tmp_path):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Boundary fixture')
    git(tmp_path, 'config', 'user.email', 'boundary@example.invalid')
    return tmp_path


def test_fingerprint_includes_first_and_last_paths_and_detects_change_outside_tail(tmp_path):
    root = repository(tmp_path)
    names = [f'file-{index:03}.txt' for index in range(50)]
    for name in names:
        (root / name).write_bytes(b'original\n')
    git(root, 'add', '--', *names)
    git(root, 'commit', '-qm', 'Fixture baseline')
    owner = adapter()
    before = owner._main_fingerprint(root)
    assert sorted(before['files']) == names
    assert before['files'][names[0]]['digest'] == hashlib.sha256(b'original\n').hexdigest()
    (root / names[0]).write_bytes(b'changed outside inventory tail\n')
    after = owner._main_fingerprint(root)
    assert before != after
    assert after['files'][names[0]]['digest'] == hashlib.sha256(b'changed outside inventory tail\n').hexdigest()
    assert after['files'][names[-1]] == before['files'][names[-1]]


def test_conflict_consumer_keeps_every_nul_name_beyond_preview(tmp_path):
    root = repository(tmp_path)
    names = [f'conflict-{index:03}.txt' for index in range(15)]
    for name in names:
        (root / name).write_bytes(b'base\n')
    git(root, 'add', '--', *names)
    git(root, 'commit', '-qm', 'Fixture baseline')
    git(root, 'checkout', '-qb', 'other')
    for name in names:
        (root / name).write_bytes(b'other\n')
    git(root, 'commit', '-qam', 'Other content')
    git(root, 'checkout', '-qb', 'target', 'HEAD~1')
    for name in names:
        (root / name).write_bytes(b'target\n')
    git(root, 'commit', '-qam', 'Target content')
    assert raw_git(root, 'merge', 'other').returncode == 1
    expected = b''.join(name.encode() + b'\0' for name in names)
    observed = raw_git(root, 'diff', '--name-only', '--diff-filter=U', '-z')
    assert observed.returncode == 0
    assert observed.stdout == expected
    assert len(expected) > 24
    assert adapter()._conflicts(root) == names


def test_public_integration_preserves_foreign_untracked_data_and_terminal_replay(project):
    # Set the limit before helper construction so the real runtime loads it.
    project['cfg']['limits']['preview_chars'] = 24
    def change(tree):
        (tree / 'src' / 'feature.py').write_bytes(b'VALUE = 1\n')
    tools, source, accepted = prepare_completed_task(project, change)
    foreign = project['app'] / 'foreign-untracked.bin'
    original = b'foreign\0data\r\n'
    foreign.write_bytes(original)
    foreign.chmod(0o640)
    inputs = integration_input(project, accepted, request_id='output-boundary-integrate')
    completed = tools.invoke(request('integrate', inputs))
    assert completed['status'] == 'integrated'
    assert completed['accepted_commit'] == accepted
    assert git(project['app'], 'rev-parse', 'HEAD') == completed['target_after']
    assert not source.exists()
    assert foreign.read_bytes() == original
    assert foreign.stat().st_mode & 0o777 == 0o640
    refs = raw_git(project['app'], 'show-ref').stdout
    replay = tools.invoke(request('integrate', inputs))
    assert replay['status'] == 'integrated'
    assert replay['replayed'] is True
    assert replay['target_after'] == completed['target_after']
    assert raw_git(project['app'], 'show-ref').stdout == refs
    assert foreign.read_bytes() == original
    assert foreign.stat().st_mode & 0o777 == 0o640


def test_actual_git_exit_and_complete_diagnostic_survive_integration_transport(tmp_path):
    root = repository(tmp_path)
    revision = 'absent-' + 'x' * 400
    observed = raw_git(root, 'show', revision)
    assert observed.returncode == 128
    assert revision.encode() in observed.stderr
    result = adapter()._run(root, 'show', revision)
    assert result['actual_exit_code'] == 128
    assert result['stdout'] == ''
    assert result['stderr'].encode() == observed.stderr
