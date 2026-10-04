"""Public verification must reject invalid destinations before all effects."""
from pathlib import Path

import pytest

from batch.helpers import request, result
from poise.common import PoiseError
from verification.retention_helpers import (
    FIXTURES, consumer, file_item, invoke_required, source_file, start, manifest,
)
from verification.retention_regression_helpers import (
    damage_destination, node_snapshot, git_snapshot, semantic_snapshot,
)


@pytest.mark.parametrize('case', ['prefix', 'directory-file', 'parent-file', 'symlink', 'symlink-outside'])
@pytest.mark.parametrize('empty', [False, True], ids=['input', 'empty'])
def test_invalid_manifest_destination_has_no_verification_effects(project, tmp_path, case, empty):
    marker = tmp_path / 'never-run.txt'
    wheel = source_file(tmp_path)
    method = consumer(marker)
    if empty:
        method['artifact_inputs']['files'] = []
        method['argv'][-1] = (FIXTURES / 'retention_marker_consumer.py').read_text()
        method['outputs'] = []
        method['stdout_contains'] = ['consumer-completed']
    # The actual declaration is chosen before Task creation; no saved-state edits.
    predicted_root = project['root'] / 'state/standalone/T1'
    (predicted_root / 'artifacts').mkdir(parents=True, exist_ok=True)
    method['artifact_inputs']['manifest_directory'] = damage_destination(predicted_root, case)
    tools, context = start(project, marker, method)
    assert Path(context['task_root']) == predicted_root
    invoke_required(tools, 'artifacts', {'items': [file_item(wheel)]})
    before = semantic_snapshot(tools.runtime)
    code = git_snapshot(context['worktree'])
    files = node_snapshot(predicted_root)
    producer = node_snapshot(wheel.parent)
    # This exact external fixture is owned by this test; do not resolve aliases.
    external_fixture = predicted_root.parent / 'foreign-directory'
    external_nodes = node_snapshot(external_fixture) if case == 'symlink-outside' else None
    try:
        with pytest.raises(PoiseError, match='[Mm]anifest|[Dd]irectory|[Ss]ymlink|[Aa]rtifact'):
            tools.invoke(request('verify', {'result': result(context), 'artifacts': []}))
    finally:
        assert not marker.exists(), 'invalid destination executed the consumer'
        assert semantic_snapshot(tools.runtime) == before
        assert git_snapshot(context['worktree']) == code
        assert node_snapshot(predicted_root) == files
        assert node_snapshot(wheel.parent) == producer
        if external_nodes is not None:
            assert node_snapshot(external_fixture) == external_nodes, 'external fixture changed'


@pytest.mark.parametrize('directory', ['artifacts', 'artifacts/new/nested', 'artifacts/existing'])
def test_safe_manifest_destination_with_empty_inventory_succeeds(project, tmp_path, directory):
    marker = tmp_path / 'run.txt'
    method = consumer(marker)
    method['artifact_inputs'] = {'manifest_directory': directory, 'files': []}
    method['argv'][-1] = (FIXTURES / 'retention_marker_consumer.py').read_text()
    method['outputs'] = []
    method['stdout_contains'] = ['consumer-completed']
    tools, context = start(project, marker, method)
    path = Path(context['task_root']) / directory
    if directory.endswith('existing'):
        path.mkdir(parents=True)
    before = path.exists()
    assert tools.runtime.retention.bind_inputs(tools.runtime.current_task(), method) == {}
    assert path.exists() is before, 'preflight created a directory'
    done = invoke_required(tools, 'verify', {'result': result(context), 'artifacts': []})
    assert marker.read_text() == 'run\n'
    _, actual = manifest(done, context)
    assert actual['inputs'] == []
    assert type(actual['receipt']['actual_exit_code']) is int
    assert type(actual['receipt']['passed']) is bool
    assert actual['receipt']['passed'] is True


def test_undeclared_retention_has_no_manifest(project, tmp_path):
    marker = tmp_path / 'run.txt'
    method = consumer(marker)
    del method['artifact_inputs']
    method['argv'][-1] = (FIXTURES / 'retention_marker_consumer.py').read_text()
    method['outputs'] = []
    method['stdout_contains'] = ['consumer-completed']
    tools, context = start(project, marker, method)
    done = invoke_required(tools, 'verify', {'result': result(context), 'artifacts': []})
    assert marker.read_text() == 'run\n'
    assert done.get('acceptance_manifests', []) == []


@pytest.mark.parametrize('change', [
    'create-directory', 'create-link', 'retarget-link', 'remove-link', 'remove-directory',
])
def test_node_inventory_detects_directory_and_link_effects(tmp_path, change):
    root = tmp_path / 'owned'
    root.mkdir()
    first = root / 'first'
    second = root / 'second'
    first.mkdir()
    second.mkdir()
    link = root / 'alias'
    if change in ('retarget-link', 'remove-link'):
        link.symlink_to(first, target_is_directory=True)
    before = node_snapshot(root)
    if change == 'create-directory':
        (root / 'new-empty').mkdir()
    elif change == 'create-link':
        link.symlink_to(first, target_is_directory=True)
    elif change == 'retarget-link':
        link.unlink()
        link.symlink_to(second, target_is_directory=True)
    elif change == 'remove-link':
        link.unlink()
    else:
        second.rmdir()
    after = node_snapshot(root)
    assert after != before
    assert after['.'] == ('directory',)
    if change in ('create-link', 'retarget-link'):
        assert after['alias'] == ('symlink', str(second if change == 'retarget-link' else first))


def test_node_inventory_does_not_follow_a_foreign_symlink(tmp_path):
    root = tmp_path / 'owned'
    root.mkdir()
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'secret').write_bytes(b'outside initial bytes')
    (root / 'alias').symlink_to(foreign, target_is_directory=True)
    before = node_snapshot(root)
    assert before == {'.': ('directory',), 'alias': ('symlink', str(foreign))}
    (foreign / 'secret').write_bytes(b'outside changed bytes')
    assert node_snapshot(root) == before


@pytest.mark.parametrize('kind', ['directory', 'symlink'])
def test_git_inventory_detects_empty_directory_and_link(project, kind):
    directory = project['app']
    before = git_snapshot(directory)
    added = directory / 'new-node'
    if kind == 'directory':
        added.mkdir()
    else:
        added.symlink_to('missing-target')
    assert git_snapshot(directory) != before
    assert git_snapshot(directory)['nodes']['new-node'] == (
        ('directory',) if kind == 'directory' else ('symlink', 'missing-target')
    )


@pytest.mark.parametrize('empty', [False, True], ids=['input', 'empty'])
def test_preflight_oracle_detects_known_external_fixture_effect(project, tmp_path, monkeypatch, empty):
    from poise.application.work import WorkTools

    original = WorkTools.invoke
    effects = []

    def bad_preflight(tools, packet):
        if packet['operation'] != 'verify':
            return original(tools, packet)
        record = tools.runtime.task_queries.record('T1')
        root = Path(tools.runtime._roots(record)['task'])
        external_fixture = root.parent / 'foreign-directory'
        assert external_fixture.is_dir() and not external_fixture.is_symlink()
        alias = root / 'artifacts/alias'
        assert alias.is_symlink() and alias.readlink() == external_fixture
        effect = external_fixture / 'manifests'
        assert not effect.exists()
        effect.mkdir()
        assert effect.is_dir()
        effects.append(effect)
        raise PoiseError('Manifest directory rejected after a forbidden external fixture effect')

    with monkeypatch.context() as scoped:
        scoped.setattr(WorkTools, 'invoke', bad_preflight)
        with pytest.raises(AssertionError, match='external fixture changed'):
            test_invalid_manifest_destination_has_no_verification_effects(
                project, tmp_path, 'symlink-outside', empty,
            )
    assert len(effects) == 1 and effects[0].is_dir()
