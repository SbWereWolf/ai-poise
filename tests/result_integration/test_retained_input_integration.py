"""Actual public local integration preserves and consumes the same Task bytes."""
from pathlib import Path
import hashlib

import pytest

from conftest import git
from batch.helpers import request
from result_integration.helpers import integration_input
from poise.common import PoiseError
from verification.retention_helpers import BYTES, assert_bundle, assert_preserved, invoke_required, manifest, verify_input


def test_public_integration_cleanup_leaves_complete_task_input_and_proof(project, tmp_path):
    tools, context, first, source, marker = verify_input(project, tmp_path)
    original = assert_bundle(first, context, first['commit'])
    assert invoke_required(tools, 'accept', {})['status'] == 'completed'
    source.unlink()
    source.parent.rmdir()
    packet = integration_input(project, first['commit'])
    integrated = invoke_required(tools, 'integrate', packet)
    assert integrated['status'] == 'integrated'
    assert git(project['app'], 'rev-parse', 'HEAD') == integrated['target_after']
    assert [item['method'] for item in integrated['checks']] == ['READ_WHEEL']
    assert marker.read_text() == 'run\nrun\n'
    assert not Path(context['worktree']).exists()
    assert (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').read_bytes() == BYTES
    assert_preserved(original, context)
    current = assert_bundle(integrated, context, integrated['target_after'])
    current_manifest, _ = manifest(integrated, context)
    original_manifest, _ = manifest(first, context)
    assert current_manifest != original_manifest
    replayed = invoke_required(tools, 'integrate', packet)
    assert replayed['replayed'] is True
    assert_bundle(replayed, context, integrated['target_after'])
    assert_preserved(original, context)
    assert_preserved(current, context)
    assert marker.read_text() == 'run\nrun\n'


@pytest.mark.parametrize('fault', ['missing', 'bytes'])
def test_integration_rejects_bad_retained_input_before_check_and_publication(project, tmp_path, fault):
    tools, context, first, _, marker = verify_input(project, tmp_path)
    assert invoke_required(tools, 'accept', {})['status'] == 'completed'
    target = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    if fault == 'missing':
        target.unlink()
    else:
        target.write_bytes(b'rebuilt different wheel')
    foreign = project['app'] / 'operator.txt'
    foreign.write_bytes(b'preserve operator WIP\n')
    index = Path(git(project['app'], 'rev-parse', '--path-format=absolute', '--git-path', 'index'))
    index_digest = hashlib.sha256(index.read_bytes()).hexdigest()
    before = git(project['app'], 'rev-parse', 'HEAD')
    status = git(project['app'], 'status', '--porcelain=v2')
    with pytest.raises(PoiseError, match='exist|missing|digest|измен'):
        tools.invoke(request('integrate', integration_input(project, first['commit'])))
    assert git(project['app'], 'rev-parse', 'HEAD') == before
    assert git(project['app'], 'status', '--porcelain=v2') == status
    assert hashlib.sha256(index.read_bytes()).hexdigest() == index_digest
    assert foreign.read_bytes() == b'preserve operator WIP\n'
    assert marker.read_text() == 'run\n'
    assert Path(context['worktree']).is_dir()
