"""Required byte retention through the existing declarative artifact batch."""
from pathlib import Path
import hashlib

import pytest

from batch.helpers import request
from poise.common import PoiseError
from verification.retention_helpers import (
    BYTES, digest, file_item, invoke_required, source_file, start,
)


def test_binary_batch_preserves_exact_bytes_and_identical_retry(project, tmp_path):
    tools, context = start(project)
    source = source_file(tmp_path)
    packet = {'items': [file_item(source)]}
    first = invoke_required(tools, 'artifacts', packet)
    target = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    assert target.read_bytes() == b'\x00\xffaccepted-wheel\x80\n'
    assert first['artifact_paths'] == [str(target)]
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest(BYTES)
    repeated = invoke_required(tools, 'artifacts', packet)
    assert repeated['artifact_paths'] == first['artifact_paths']
    assert repeated['artifacts'] == first['artifacts']
    source.write_bytes(b'other accepted bytes')
    with pytest.raises(PoiseError, match='digest|different content|conflict'):
        tools.invoke(request('artifacts', packet))
    assert target.read_bytes() == b'\x00\xffaccepted-wheel\x80\n'


@pytest.mark.parametrize('fault', ['missing', 'bytes', 'symlink', 'directory'])
def test_invalid_binary_source_rejects_before_valid_member_or_registry_write(
    project, tmp_path, fault,
):
    tools, context = start(project)
    source = source_file(tmp_path)
    bad = tmp_path / 'bad.whl'
    if fault == 'bytes':
        bad.write_bytes(b'different bytes')
    elif fault == 'symlink':
        bad.symlink_to(source)
    elif fault == 'directory':
        bad.mkdir()
    before = tools.runtime.store.artifact_records('T1')
    before_task = tools.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError, match='source|digest|symlink|regular|exist') as failure:
        tools.invoke(request('artifacts', {
            'items': [file_item(source), file_item(bad, 'inputs/bad.whl')],
        }))
    assert str(bad) in str(failure.value), 'refusal must identify the invalid file'
    assert not (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').exists()
    assert tools.runtime.store.artifact_records('T1') == before
    assert tools.runtime.task_queries.record('T1') == before_task


def test_conflicting_binary_destination_never_overwrites_first_bytes(project, tmp_path):
    tools, context = start(project)
    source = source_file(tmp_path)
    invoke_required(tools, 'artifacts', {'items': [file_item(source)]})
    alternate = tmp_path / 'rebuilt.whl'
    alternate.write_bytes(b'rebuilt-wheel')
    before = tools.runtime.store.artifact_records('T1')
    with pytest.raises(PoiseError, match='different content|conflict'):
        tools.invoke(request('artifacts', {
            'items': [file_item(alternate, expected=digest(b'rebuilt-wheel'))],
        }))
    assert (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').read_bytes() == BYTES
    assert tools.runtime.store.artifact_records('T1') == before
