"""R1-R4: addressed public append/read and independent stored-byte oracles."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from batch.helpers import request
from poise.modules.foundation.errors import PoiseError
from .helpers import (DATA, PACKETS, append, arrange, assert_comment, assert_reason,
                      client, configure_case, files, own_other_task, protected,
                      read, read_bytes, source, subject, saved_task_expectation,
                      assert_saved_task, arrange_two_worktrees, worktree_snapshots)


@pytest.mark.parametrize('state', ['newborn', 'available', 'active', 'verified', 'accepted'])
def test_foreign_append_and_read_preserve_both_executions(project, state):
    configure_case(project)
    owner, ctx = arrange(project, state)
    author = client(project)
    contexts = arrange_two_worktrees(project, owner, ctx, author) if state == 'active' else None
    if contexts is None:
        own_other_task(author)
    physical_work = worktree_snapshots(contexts) if contexts else None
    saved = saved_task_expectation(owner, state)
    observer = client(project, 'other-reader')
    before = protected(owner)
    attachment, expected = source(project)
    out = append(author, attachments=[attachment])
    assert set(out) - {'interaction'} == set(DATA['append_fields'])
    assert out['status'] == 'feedback_appended' and out['task_id'] == 'T1'
    assert out['request_id'] == 'source-1' and len(out['comments']) == 1
    assert_comment(out['comments'][0], DATA['initial_text'])
    later = append(author, request_id='later-1', items=[
        {'text': DATA['later_text'], 'attachments': []},
        {'text': DATA['other_text'], 'attachments': []}])
    assert len(later['comments']) == 2
    page = read(observer)
    assert set(page) - {'interaction'} == set(DATA['read_fields'])
    assert page['status'] == 'read_only' and page['task_id'] == 'T1'
    assert set(page['task']) == set(DATA['task_fields'])
    assert page['task']['id'] == 'T1' and page['task']['status'] == state
    assert_saved_task(page['task'], saved)
    assert page['comments'] == out['comments'] + later['comments']
    assert [c['text'] for c in page['comments']] == [
        DATA['initial_text'], DATA['later_text'], DATA['other_text']]
    assert len({c['id'] for c in page['comments']}) == 3
    assert read_bytes(observer, page['comments'][0]['attachments'][0]) == expected
    assert protected(owner) == before
    if state == 'newborn':
        assert ctx is None and page['task']['stage'] is None
        assert page['task']['requirements'] == [] and page['task']['definition_of_done'] == []
        assert owner.runtime.task_queries.record('T1')['worktree'] is None
    else:
        assert page['task']['requirements'] == DATA['requirements']
        assert page['task']['definition_of_done'] == DATA['dod']
    if state in ('verified', 'accepted'):
        assert len(page['task']['results']) == 1
        assert page['task']['results'][0]['task'] == 'T1'
        assert page['task']['results'][0]['stage'] == 'work'
    fresh_page = read(client(project, 'fresh-reader'))
    assert fresh_page['comments'] == page['comments']
    assert_saved_task(fresh_page['task'], saved)
    from terminal_materials.helpers import cli
    exit_code, process_page = cli(project, PACKETS['read'])
    assert exit_code == 0, process_page
    assert process_page['comments'] == page['comments']
    assert_saved_task(process_page['task'], saved)
    assert protected(owner) == before
    if contexts:
        assert worktree_snapshots(contexts) == physical_work


def test_typed_newborn_accepts_material_without_starting_a_stage(project):
    configure_case(project)
    owner, _ = arrange(project, typed=True)
    author = client(project)
    before = protected(owner)
    saved = saved_task_expectation(owner, 'newborn', typed=True)
    out = append(author)
    page = read(author)
    assert page['comments'] == out['comments']
    assert_saved_task(page['task'], saved)
    assert protected(owner) == before
    record = owner.runtime.task_queries.record('T1')
    assert record['status'] == 'newborn' and record['goal_type'] == 'development'
    assert record['worktree'] is None


def test_newborn_empty_read_is_addressed_and_creates_no_execution(project):
    configure_case(project)
    owner, _ = arrange(project)
    author = client(project)
    before = protected(owner)
    page = read(author)
    assert page['comments'] == [] and page['task']['results'] == []
    assert page['task']['goal'] == DATA['goal'] and page['task']['stage'] is None
    assert protected(owner) == before


def test_original_binary_text_empty_and_same_named_files_survive_intake_removal(project):
    configure_case(project)
    owner, _ = arrange(project)
    author = client(project)
    inputs = [source(project, i) for i in range(3)]
    first = append(author, attachments=[item for item, _ in inputs])['comments'][0]
    other, other_bytes = source(project, 1, filename='binary.bin', content=b'second comment\x00\xfe')
    second = append(author, request_id='same-name', attachments=[other])['comments'][0]
    for path in (project['root'] / 'intake').iterdir():
        path.unlink()
    observer = client(project, 'independent-reader')
    page = read(observer)
    assert page['comments'] == [first, second]
    assert [a['name'] for a in first['attachments']] == ['данные.txt', 'binary.bin', 'empty.bin']
    assert first['attachments'][1]['name'] == second['attachments'][0]['name']
    assert first['attachments'][1]['id'] != second['attachments'][0]['id']
    for stored, (_, expected) in zip(first['attachments'], inputs):
        assert stored['size'] == len(expected)
        assert stored['digest'] == hashlib.sha256(expected).hexdigest()
        assert read_bytes(observer, stored) == expected
    assert read_bytes(observer, second['attachments'][0]) == other_bytes
    assert owner.runtime.task_queries.record('T1')['status'] == 'newborn'


@pytest.mark.parametrize('root_form', ['absolute', 'relative'])
def test_selected_material_root_roundtrip_uses_independent_expected_coordinates(project, root_form):
    configure_case(project, root_form=root_form)
    owner, _ = arrange(project)
    author = client(project)
    item, expected = source(project)
    added = append(author, attachments=[item])['comments'][0]['attachments'][0]
    root = project['root'] / project['cfg']['paths']['state'] / 'user-materials'
    assert root == project['material_root']
    stored = files(root)
    assert stored and expected in stored.values()
    task = owner.runtime.task_queries.record('T1')
    assert task['worktree'] is None
    assert read_bytes(client(project, 'new-reader'), added) == expected
    # Wrong cwd/manifest-based relative resolution cannot satisfy this physical oracle.
    assert not (project['app'] / 'user-materials').exists()
    assert not (project['config_path'].parent / 'user-materials').exists()


def test_exact_replay_and_conflicting_reuse_preserve_one_business_result(project):
    configure_case(project)
    owner, _ = arrange(project)
    author = client(project)
    item, _ = source(project)
    out = append(author, attachments=[item])
    before = protected(owner); stored = files(project['material_root'])
    assert append(author, attachments=[item])['comments'] == out['comments']
    assert append(client(project, 'retry-client'), attachments=[item])['comments'] == out['comments']
    with pytest.raises(PoiseError) as error:
        append(author, text='Changed intent', attachments=[item])
    assert_reason(error.value, 'conflict')
    other_item, _ = source(project, filename='different.bin', content=b'new file intent')
    with pytest.raises(PoiseError) as error:
        append(author, attachments=[other_item])
    assert_reason(error.value, 'conflict')
    assert read(author)['comments'] == out['comments']
    assert protected(owner) == before and files(project['material_root']) == stored


@pytest.mark.parametrize('boundary', ['items', 'bytes'])
def test_selected_inclusive_limits_accept_their_exact_boundary(project, boundary):
    from conftest import write_json
    configure_case(project)
    owner, _ = arrange(project)
    limits = DATA['boundary_limits']
    # Prepare the Task with its ordinary fixture settings. Four is the smallest
    # selected limit that admits the append input object's own four fields.
    project['cfg']['batch']['max_items'] = limits['max_items']
    project['cfg']['batch']['max_artifact_bytes'] = limits['max_artifact_bytes']
    write_json(project['config_path'], project['cfg'])
    author = client(project)
    before = protected(owner)
    if boundary == 'items':
        items = [{'text': DATA['later_text'], 'attachments': []}
                 for _ in range(limits['max_items'])]
        comments = append(author, items=items)['comments']
        assert len(comments) == 4
        assert [c['text'] for c in comments] == [DATA['later_text']] * 4
        assert len({c['id'] for c in comments}) == 4
        assert all(c['attachments'] == [] for c in comments)
    else:
        expected = bytes.fromhex(limits['payload_hex'])
        assert len(expected) == 7
        item, _ = source(project, content=expected)
        comments = append(author, attachments=[item])['comments']
        assert comments[0]['attachments'][0]['size'] == 7
        assert read_bytes(author, comments[0]['attachments'][0]) == expected
    assert read(author)['comments'] == comments
    assert protected(owner) == before
