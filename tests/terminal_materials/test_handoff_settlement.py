"""Ordinary release history never becomes a material-delivery obligation."""
from copy import deepcopy

import pytest

from batch.helpers import request
from runtime_services.test_participant_continuity import release_packet
from runtime_services.test_newborn_handoff_eligibility import release_rows
from .helpers import (absent, agree, arrange, declaration, delivery_status,
                      file_output, finish, fresh, settle)


def release(tools, request_id):
    saved = tools.invoke(release_packet(tools, request_id))
    assert saved['status'] == 'ownership_released'


def released_chain(project):
    tools, root, source, commit = arrange(project)
    destination = project['root'] / 'delivered-result.txt'
    agree(tools, declaration([file_output(source, destination)]))
    release(tools, 'old-release')
    tools.runtime.ownership.acquire_task('T1')
    release(tools, 'current-release')
    return tools, root, source, commit, destination


@pytest.mark.parametrize('foreign_pending', [False, True], ids=['alone', 'foreign-pending'])
def test_resumed_release_allows_public_terminal_delivery(project, foreign_pending):
    tools, root, _, commit, destination = released_chain(project)
    receiver = fresh(project)
    resumed = receiver.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None,
        'feedback': None, 'rework_stage': None,
    }))
    assert resumed['status'] == 'verified'
    receipts = release_rows(receiver.runtime, 'T1')
    assert len(receipts) == 2

    if foreign_pending:
        foreign, foreign_root, foreign_source, _ = arrange(
            project, identifier='FOREIGN-WORK', session='foreign-owner')
        assert foreign.runtime.current_task()['goal'] == 'Результат FOREIGN-WORK'
        release(foreign, 'foreign-release')
        foreign_record = deepcopy(foreign.runtime.task_queries.record('FOREIGN-WORK'))
        foreign_history = foreign.runtime.task_queries.history('FOREIGN-WORK')
        foreign_releases = release_rows(foreign.runtime, 'FOREIGN-WORK')

    history = receiver.runtime.task_queries.history('T1')
    integrated = finish(project, receiver, commit, 'completed')

    assert integrated['status'] == 'integrated'
    assert delivery_status(fresh(project))['status'] == 'delivery_complete'
    absent(root)
    assert destination.read_bytes() == b'customer result\n'
    assert release_rows(receiver.runtime, 'T1') == receipts
    assert receiver.runtime.task_queries.history('T1')[:len(history)] == history
    if foreign_pending:
        assert foreign.runtime.task_queries.record('FOREIGN-WORK') == foreign_record
        assert foreign.runtime.task_queries.history('FOREIGN-WORK') == foreign_history
        assert release_rows(foreign.runtime, 'FOREIGN-WORK') == foreign_releases
        assert foreign_root.is_dir()
        assert foreign_source.read_bytes() == b'customer result\n'


def test_released_nonterminal_task_retains_materials(project):
    tools, root, source, _, destination = released_chain(project)
    receipts = release_rows(tools.runtime, 'T1')
    assert len(receipts) == 2
    record = deepcopy(tools.runtime.task_queries.record('T1'))
    history = tools.runtime.task_queries.history('T1')

    blocked = settle(fresh(project))

    assert blocked['status'] == 'delivery_blocked'
    assert blocked['reason'] == 'task_not_terminal'
    assert root.is_dir()
    assert source.read_bytes() == b'customer result\n'
    assert not destination.exists()
    assert release_rows(tools.runtime, 'T1') == receipts
    assert tools.runtime.task_queries.record('T1') == record
    assert tools.runtime.task_queries.history('T1')[:len(history)] == history
