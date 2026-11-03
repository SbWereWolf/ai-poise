"""CR-01/02: preserved planning data and exact nonblank comment payloads."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from batch.helpers import request
from terminal_materials.helpers import cli
from .helpers import (
    DATA, PACKETS, append, arrange, assert_comment, client, configure_case,
    database_path, files, protected, read, read_bytes, source,
)


def contains_value(value, expected):
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(contains_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(contains_value(item, expected) for item in value)
    return False


@pytest.mark.parametrize('typed', [False, True], ids=['goal-only', 'typed'])
def test_cancelled_unstarted_materials_preserve_the_actual_draft(project, typed):
    configure_case(project)
    owner, context = arrange(project, typed=typed)
    assert context is None
    author = client(project)
    attachment, expected_bytes = source(project)
    comments = append(author, attachments=[attachment])['comments']
    before_page = read(author)
    with closing(sqlite3.connect(database_path(owner))) as database:
        original_metadata, = database.execute(
            'SELECT metadata FROM tasks WHERE id=?', ('T1',)).fetchone()
    draft = json.loads(original_metadata)['newborn']['draft']
    assert draft['goal'] == DATA['goal']
    assert before_page['task']['stage'] is None
    assert before_page['task']['results'] == []

    cancelled = owner.invoke(request('cancel', {
        'reason': 'Fixture executor cancels this unstarted Task.',
    }))

    assert cancelled['status'] == 'cancelled'
    with closing(sqlite3.connect(database_path(owner))) as database:
        assert database.execute('SELECT status,metadata FROM tasks WHERE id=?',
                                ('T1',)).fetchone() == ('cancelled', original_metadata)
    assert owner.runtime.task_queries.record('T1')['worktree'] is None
    Path(attachment['source']['path']).unlink()
    before = protected(owner)
    stored = files(project['material_root'])

    page = read(client(project, 'cancelled-draft-reader'))

    assert page['task'] == {**before_page['task'], 'status': 'cancelled'}
    assert page['task']['goal'] == DATA['goal']
    assert page['task']['requirements'] == (DATA['requirements'] if typed else [])
    assert page['task']['definition_of_done'] == (DATA['dod'] if typed else [])
    assert page['task']['stage'] is None and page['task']['results'] == []
    assert contains_value(page['task']['content'], draft)
    assert page['comments'] == comments
    assert read_bytes(client(project, 'cancelled-draft-bytes'),
                      comments[0]['attachments'][0]) == expected_bytes
    exit_code, process_page = cli(project, PACKETS['read'])
    assert exit_code == 0, process_page
    assert process_page['task'] == page['task']
    assert process_page['comments'] == comments
    assert protected(owner) == before
    assert files(project['material_root']) == stored


def test_nonblank_comment_preserves_embedded_nul_and_exact_replay(project):
    configure_case(project)
    owner, _ = arrange(project)
    text = 'Original\0context'
    before = protected(owner)

    added = append(client(project), text=text)

    assert len(added['comments']) == 1
    assert_comment(added['comments'][0], 'Original\0context')
    page = read(client(project, 'payload-reader'))
    assert page['comments'] == added['comments']
    assert page['comments'][0]['text'] == 'Original\0context'
    replay = append(client(project, 'payload-replay-author'), text=text)
    assert replay['comments'] == added['comments']
    exit_code, process_page = cli(project, PACKETS['read'])
    assert exit_code == 0, process_page
    assert process_page['comments'] == added['comments']
    assert process_page['comments'][0]['text'] == 'Original\0context'
    assert protected(owner) == before
