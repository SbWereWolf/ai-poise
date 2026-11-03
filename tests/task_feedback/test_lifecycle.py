"""R4-R5: real terminal resource retirement, persistent reads, immutable terminal data."""
from pathlib import Path

import pytest

from batch.helpers import request
from poise.modules.foundation.errors import PoiseError
from terminal_materials.helpers import cli, delivery_status
from .helpers import (DATA, PACKETS, append, arrange, assert_reason, client,
                      configure_case, files, finish_task, prepare_delivery,
                      protected, read, read_bytes, source)


@pytest.mark.parametrize('member', [False, True], ids=['standalone', 'sprint'])
@pytest.mark.parametrize('state', ['completed', 'cancelled'])
def test_original_material_is_readable_after_real_terminal_cleanup(project, member, state):
    configure_case(project, terminal=True)
    owner, ctx = arrange(project, 'verified', member=member)
    author = client(project)
    item, expected = source(project)
    original = append(author, attachments=[item])['comments']
    Path(item['source']['path']).unlink()
    root, destination = prepare_delivery(project, owner, ctx)
    foreign = root.parent / 'FOREIGN/keep.bin'; foreign.parent.mkdir(exist_ok=True)
    foreign.write_bytes(b'foreign task bytes\n')
    shared = root.parent.parent / 'shared-fixture.bin'
    shared.write_bytes(b'shared sprint bytes\n')
    finish_task(project, owner, state)
    assert owner.runtime.task_queries.record('T1')['status'] == state
    assert not root.exists() and not Path(ctx['worktree']).exists()
    assert delivery_status(owner)['status'] == 'delivery_complete'
    assert destination.read_bytes() == b'customer result\n'
    assert foreign.read_bytes() == b'foreign task bytes\n'
    assert shared.read_bytes() == b'shared sprint bytes\n'
    physical = files(project['material_root'])
    assert expected in physical.values()
    assert b'temporary task service\n' not in physical.values()
    before = protected(owner)
    observer = client(project, 'after-cleanup')
    page = read(observer)
    assert page['task']['status'] == state and page['task']['goal'] == DATA['goal']
    assert page['comments'] == original
    assert len(page['task']['results']) == 1
    assert page['task']['results'][0]['task'] == 'T1'
    assert read_bytes(observer, original[0]['attachments'][0]) == expected
    exit_code, process_page = cli(project, PACKETS['read'])
    assert exit_code == 0, process_page
    assert process_page['comments'] == original and process_page['task']['status'] == state
    bytes_packet = {**PACKETS['bytes'], 'input': {
        **PACKETS['bytes']['input'], 'attachment_id': original[0]['attachments'][0]['id']}}
    exit_code, process_bytes = cli(project, bytes_packet)
    assert exit_code == 0, process_bytes
    import base64
    assert base64.b64decode(process_bytes['data'], validate=True) == expected
    assert protected(owner) == before and files(project['material_root']) == physical
    assert not root.exists() and not Path(ctx['worktree']).exists()


@pytest.mark.parametrize('state', ['completed', 'cancelled'])
def test_terminal_refuses_new_append_and_restart_but_replays_saved_read(project, state):
    configure_case(project, terminal=True)
    owner, ctx = arrange(project, 'verified')
    author = client(project)
    item, expected = source(project)
    original = append(author, attachments=[item])['comments']
    root, _ = prepare_delivery(project, owner, ctx)
    finish_task(project, owner, state)
    before = protected(owner); physical = files(project['material_root'])
    with pytest.raises(PoiseError) as error:
        append(author, request_id='new-terminal-comment', text=DATA['later_text'])
    assert_reason(error.value, 'terminal')
    record = owner.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError) as error:
        author.invoke(request('task', {'action': 'restart', 'task_id': 'T1',
            'request_id': 'terminal-restart', 'expected_version': record['version'],
            'reason': 'Separate fixture continuation.', 'authorization': 'Fixture owner decision.'}))
    assert 'terminal' in str(error.value).lower() or 'терминаль' in str(error.value).lower()
    assert append(author, attachments=[item])['comments'] == original
    assert read(author)['comments'] == original
    assert read_bytes(author, original[0]['attachments'][0]) == expected
    assert protected(owner) == before and files(project['material_root']) == physical
    assert not root.exists()
    # New work is registered independently, without reopening the terminal Task.
    from .helpers import own_other_task
    own_other_task(author)
    added = append(author, task='OTHER', request_id='new-task-material', text=DATA['other_text'])
    assert read(author, 'OTHER')['comments'] == added['comments']
    assert read(author)['comments'] == original
    assert owner.runtime.task_queries.record('T1')['status'] == state


def test_fresh_cli_reads_exact_saved_task_and_bytes_without_acquiring_it(project):
    configure_case(project)
    owner, _ = arrange(project, 'verified')
    author = client(project)
    item, expected = source(project)
    comments = append(author, attachments=[item])['comments']
    before = protected(owner)
    exit_code, page = cli(project, PACKETS['read'])
    assert exit_code == 0, page
    assert page['comments'] == comments and page['task']['goal'] == DATA['goal']
    assert page['task']['results'][0]['stage'] == 'work'
    packet = {**PACKETS['bytes'], 'input': {
        **PACKETS['bytes']['input'], 'attachment_id': comments[0]['attachments'][0]['id']}}
    exit_code, value = cli(project, packet)
    assert exit_code == 0, value
    import base64
    assert base64.b64decode(value['data'], validate=True) == expected
    assert protected(owner) == before
