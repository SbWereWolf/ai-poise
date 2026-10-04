"""Public verification must reject invalid destinations before all effects."""
from pathlib import Path

import pytest

from batch.helpers import request, result
from poise.common import PoiseError
from verification.retention_helpers import (
    FIXTURES, consumer, file_item, invoke_required, source_file, start, manifest,
)
from verification.retention_regression_helpers import (
    damage_destination, file_snapshot, git_snapshot, semantic_snapshot,
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
    files = file_snapshot(predicted_root / 'artifacts')
    try:
        with pytest.raises(PoiseError, match='[Mm]anifest|[Dd]irectory|[Ss]ymlink|[Aa]rtifact'):
            tools.invoke(request('verify', {'result': result(context), 'artifacts': []}))
    finally:
        assert not marker.exists(), 'invalid destination executed the consumer'
        assert semantic_snapshot(tools.runtime) == before
        assert git_snapshot(context['worktree']) == code
        assert file_snapshot(predicted_root / 'artifacts') == files


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
