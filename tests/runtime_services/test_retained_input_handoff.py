"""Public handoff must transfer a usable declared acceptance input closure."""
from pathlib import Path

import pytest

from conftest import WorkPoise
from batch.helpers import request, result
from poise.application.work import WorkTools
from poise.common import PoiseError
from verification.retention_helpers import BYTES, invoke_required, manifest, verify_input


def handoff(tools):
    return tools.invoke(request('handoff', {
        'request_id': 'retained-input-handoff', 'reason': 'Independent repeat verification',
        'result': None, 'commit_message': None, 'artifact_paths': [],
    }))


def test_handoff_and_resumed_check_use_retained_bytes_after_source_removal(project, tmp_path):
    tools, context, first, source, marker = verify_input(project, tmp_path)
    path, _ = manifest(first, context)
    released = handoff(tools)
    assert released['status'] == 'handed_off'
    assert not tools.runtime.runtime.exists()
    source.unlink()
    source.parent.rmdir()
    receiver = WorkTools(WorkPoise(project['config_path'], 'receiver'))
    resumed = invoke_required(receiver, 'bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    })
    assert resumed['status'] == 'verified'
    repair = invoke_required(receiver, 'bootstrap', {
        'task': None, 'decision': 'rework',
        'feedback': 'Repeat the same consumer from retained inputs.',
        'rework_stage': 'implementation',
    })
    repeated = invoke_required(receiver, 'verify', {
        'result': result(repair, 'Repeated retained input check.'), 'artifacts': [],
    })
    assert repeated['status'] == 'verified'
    assert marker.read_text() == 'run\nrun\n'
    assert (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').read_bytes() == BYTES
    assert path.is_file()


@pytest.mark.parametrize('fault', ['missing', 'bytes'])
def test_handoff_refuses_invalid_required_input_without_releasing_owner(project, tmp_path, fault):
    tools, context, _, _, marker = verify_input(project, tmp_path)
    target = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    if fault == 'missing':
        target.unlink()
    else:
        target.write_bytes(b'changed')
    before = tools.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError, match='exist|missing|digest|измен'):
        handoff(tools)
    assert tools.runtime.task_queries.record('T1') == before
    assert tools.runtime.current_task()['claimed_by'] == 'producer'
    assert marker.read_text() == 'run\n'
