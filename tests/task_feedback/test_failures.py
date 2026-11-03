"""Accepted ordinary failure contracts cannot pass on generic missing-operation errors."""
from copy import deepcopy
from pathlib import Path
import re

import pytest

from batch.helpers import request
from poise.modules.foundation.errors import PoiseError
from .helpers import (DATA, PACKETS, append, arrange, assert_reason, client,
                      configure_case, files, own_other_task, protected,
                      read, read_bytes, source, subject)


@pytest.mark.parametrize('case', ['empty-text', 'wrong-text-type', 'empty-batch',
    'wrong-items-type', 'unknown-field', 'duplicate-name', 'missing-file',
    'wrong-digest', 'too-large-file', 'too-many-items', 'invalid-second-item'])
def test_invalid_append_has_actionable_refusal_and_no_partial_effect(project, case):
    configure_case(project)
    owner, _ = arrange(project)
    author = client(project)
    item, _ = source(project)
    packet = deepcopy(PACKETS['append'])
    body = packet['input']; body['items'][0]['attachments'] = [item]
    if case == 'empty-text': body['items'][0]['text'] = ' \n '
    elif case == 'wrong-text-type': body['items'][0]['text'] = 42
    elif case == 'empty-batch': body['items'] = []
    elif case == 'wrong-items-type': body['items'] = {}
    elif case == 'unknown-field': body['surprise'] = True
    elif case == 'duplicate-name': body['items'][0]['attachments'] *= 2
    elif case == 'missing-file': Path(item['source']['path']).unlink()
    elif case == 'wrong-digest': item['source']['digest'] = '0' * 64
    elif case == 'too-large-file':
        item, _ = source(project, content=b'X' * (project['cfg']['batch']['max_artifact_bytes'] + 1))
        body['items'][0]['attachments'] = [item]
    elif case == 'too-many-items': body['items'] *= project['cfg']['batch']['max_items'] + 1
    elif case == 'invalid-second-item': body['items'].append({'text': '', 'attachments': []})
    before = protected(owner); physical = files(project['material_root'])
    with pytest.raises(PoiseError) as error:
        # Invoke the true public parser: malformed packets must fail for their reason.
        author.invoke(packet)
    assert_reason(error.value, 'invalid')
    assert re.search(DATA['invalid_case_patterns'][case], str(error.value), re.I), str(error.value)
    assert protected(owner) == before and files(project['material_root']) == physical
    assert read(author)['comments'] == []


def test_missing_task_and_unknown_attachment_are_not_owner_takeovers(project):
    configure_case(project)
    owner, _ = arrange(project)
    author = client(project)
    before = protected(owner)
    for action in ('read', 'append'):
        packet = deepcopy(PACKETS[action]); packet['input']['task_id'] = 'ABSENT'
        with pytest.raises(PoiseError) as error:
            author.invoke(packet)
        assert_reason(error.value, 'missing_task')
    with pytest.raises(PoiseError) as error:
        author.invoke(PACKETS['bytes'])
    assert_reason(error.value, 'missing_attachment')
    assert protected(owner) == before and files(project['material_root']) == {}


def test_attachment_id_cannot_be_read_through_another_task(project):
    configure_case(project)
    owner, _ = arrange(project)
    author = client(project); own_other_task(author)
    item, _ = source(project)
    stored = append(author, attachments=[item])['comments'][0]['attachments'][0]
    before = protected(owner); physical = files(project['material_root'])
    with pytest.raises(PoiseError) as error:
        read_bytes(author, stored, task='OTHER')
    assert_reason(error.value, 'missing_attachment')
    assert protected(owner) == before and files(project['material_root']) == physical


def test_missing_storage_setting_preserves_old_operation_and_has_no_default(project):
    configure_case(project, storage=False)
    owner, _ = arrange(project)
    fresh = client(project, 'fresh-no-storage')
    old = fresh.invoke(request('show', {'queries': [{'id': 'task', 'kind': 'task'}]}))
    assert old['status'] == 'read_only'
    before = protected(owner); configuration = project['config_path'].read_bytes()
    with pytest.raises(PoiseError) as error:
        fresh.invoke(PACKETS['append'])
    assert_reason(error.value, 'configuration')
    assert protected(owner) == before and not project['material_root'].exists()
    assert project['config_path'].read_bytes() == configuration
    assert 'task_materials' not in fresh.runtime.paths


def test_publication_failure_retries_same_batch_without_partial_logical_comments(project, monkeypatch):
    from poise.infrastructure.file_publication import BinaryFilePublisher
    configure_case(project)
    owner, _ = arrange(project)
    author = client(project)
    first, first_bytes = source(project, 0)
    second, second_bytes = source(project, 1)
    before = protected(owner)
    original = BinaryFilePublisher.publish_bytes
    published = []

    def fail_second(cls, content, target, mode):
        if Path(target).is_relative_to(project['material_root']):
            if published:
                raise OSError('fixture publication interrupted')
            value = original(content, target, mode)
            published.append(str(target))
            return value
        return original(content, target, mode)

    with monkeypatch.context() as patch:
        patch.setattr(BinaryFilePublisher, 'publish_bytes', classmethod(fail_second))
        with pytest.raises(PoiseError) as error:
            append(author, attachments=[first, second])
        assert 'retry' in str(error.value).lower() or 'повтор' in str(error.value).lower()
    assert len(published) == 1, 'Fixture must exercise an actual partial file publication'
    assert read(author)['comments'] == []
    assert protected(owner) == before
    result = append(author, attachments=[first, second])['comments']
    assert read(author)['comments'] == result and len(result) == 1
    assert [read_bytes(author, a) for a in result[0]['attachments']] == [first_bytes, second_bytes]
    stored = files(project['material_root'])
    assert list(stored.values()).count(first_bytes) == 1
    assert list(stored.values()).count(second_bytes) == 1
    assert append(author, attachments=[first, second])['comments'] == result
    assert files(project['material_root']) == stored and protected(owner) == before
